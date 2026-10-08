"""Regional bike facility inventory, conflated onto the street graph, and
the bike "stress" multiplier the route page uses to prefer calm streets.

The Atlanta Regional Commission's inventory of existing bicycle and trail
facilities (``Existing_Facilities``, April 2026) is a set of hand-drawn
lines with no key into Overture, so the match is geometric: an edge takes a
facility when at least half of the points sampled along it lie within
``MATCH_M`` of an inventory line that runs roughly parallel to it.  Where
several facilities match, the most protected one wins.

Facility types (ARC ``Facility_type``): 1 = unprotected bike lane (buffered
or not, from ``Buffer_exist``), 2 = protected bike lane, 3 = multi-use
trail (greenway), 4 = multi-use trail (sidepath), 5 = park trail.  The
inventory has no signed-route or sharrow class, so the ``route`` and
``neighborway`` codes inherited from the San Francisco version stay
defined but unused.

The stress multiplier is in equivalent metres per metre: a block that feels
like 1.4 blocks.  It is a comfort scale, not a speed model, and it only ever
applies to the distance objective in bike mode when the rider asks for calm
streets, so the climbing axis of the frontier is untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString
from shapely.strtree import STRtree

from .download import BIKEWAYS_GEOJSON
from .utils import get_logger, step

log = get_logger("sf_flat_routes.bikeways")

#: distance within which an edge sample point counts as "on" an inventory line
MATCH_M = 12.0
#: and the largest angle between the two for them to count as the same street
MATCH_DEG = 25.0
#: spacing of sample points along an edge
SAMPLE_M = 10.0

#: facility code per edge, most protected first (higher rank wins a tie)
FACILITIES = ("", "route", "neighborway", "lane", "buffered_lane", "separated", "path")
_RANK = {f: i for i, f in enumerate(FACILITIES)}

#: comfort multiplier by road class when there is no bikeway on the street
CLASS_STRESS = {
    "cycleway": 0.8, "living_street": 0.9, "path": 1.0, "residential": 1.0,
    "unclassified": 1.0, "track": 1.1, "service": 1.1, "unknown": 1.1,
    "pedestrian": 1.2, "tertiary": 1.2, "footway": 1.3, "secondary": 1.4,
    "primary": 1.6, "trunk": 2.0,
}
#: and by facility where there is one; a class III route keeps the street's
#: own rating, slightly softened and never worse than a tertiary street,
#: because sharrows do not change the traffic but the city did pick the street
FACILITY_STRESS = {"path": 0.8, "separated": 0.8, "buffered_lane": 0.9,
                   "lane": 1.0, "neighborway": 0.9}
ROUTE_SOFTEN = 0.95
ROUTE_CAP = 1.2


def _facility(props: dict) -> str:
    kind = str(props.get("Facility_type") or "").strip()
    if kind in ("3", "4", "5"):          # greenway, sidepath, park trail
        return "path"
    if kind == "2":
        return "separated"
    if kind == "1":
        buffered = str(props.get("Buffer_exist") or "").strip() == "1"
        return "buffered_lane" if buffered else "lane"
    return ""


def load_bikeways(path: Path = BIKEWAYS_GEOJSON, bbox=None) -> gpd.GeoDataFrame:
    """Inventory lines with a ``facility`` column, in WGS84.

    The inventory covers the 19-county region; only lines that touch
    ``bbox`` (the study box by default) are kept.
    """
    from .config import CITY_BBOX

    lon_min, lon_max, lat_min, lat_max = CITY_BBOX if bbox is None else bbox
    with open(path) as fh:
        data = json.load(fh)
    rows = []
    for f in data["features"]:
        g = f.get("geometry")
        if not g:
            continue
        parts = ([g["coordinates"]] if g["type"] == "LineString"
                 else g["coordinates"] if g["type"] == "MultiLineString" else [])
        fac = _facility(f["properties"])
        if not fac:
            continue
        for coords in parts:
            if len(coords) < 2:
                continue
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            if max(xs) < lon_min or min(xs) > lon_max or max(ys) < lat_min or min(ys) > lat_max:
                continue
            rows.append({"facility": fac,
                         "street": (f["properties"].get("Name") or "").strip(),
                         "geometry": LineString([c[:2] for c in coords])})
    gdf = gpd.GeoDataFrame(rows, columns=["facility", "street", "geometry"],
                           geometry="geometry", crs="EPSG:4326")
    log.info("bike facility inventory: %d lines in the study box, %s", len(gdf),
             dict(gdf["facility"].value_counts()))
    return gdf


def _segments(line: LineString) -> np.ndarray:
    c = np.asarray(line.coords)[:, :2]
    return np.c_[c[:-1], c[1:]]            # x0 y0 x1 y1 per segment


def _sample(line: LineString, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Points along the line and the local bearing (radians) at each."""
    n = max(2, int(line.length // spacing) + 1)
    d = np.linspace(0.0, line.length, n)
    # bearing from a short step along the line, so curves get local headings
    eps = min(1.0, line.length / 2)
    pts = [line.interpolate(x) for x in d]
    ahead = [line.interpolate(min(x + eps, line.length)) for x in d]
    behind = [line.interpolate(max(x - eps, 0.0)) for x in d]
    xy = np.array([(p.x, p.y) for p in pts])
    br = np.arctan2([a.y - b.y for a, b in zip(ahead, behind)],
                    [a.x - b.x for a, b in zip(ahead, behind)])
    return xy, br


def conflate(edges: gpd.GeoDataFrame, bikeways: gpd.GeoDataFrame | None = None) -> pd.Series:
    """Facility per edge ('' where the street has none), aligned to ``edges``."""
    if bikeways is None:
        bikeways = load_bikeways()
    bw = bikeways.to_crs(edges.crs)
    # one straight piece per inventory vertex pair, so the heading test is local
    pieces, piece_fac = [], []
    for fac, geom in zip(bw["facility"], bw.geometry):
        for x0, y0, x1, y1 in _segments(geom):
            pieces.append(LineString([(x0, y0), (x1, y1)]))
            piece_fac.append(fac)
    piece_fac = np.array(piece_fac)
    piece_br = np.array([np.arctan2(p.coords[1][1] - p.coords[0][1],
                                    p.coords[1][0] - p.coords[0][0]) for p in pieces])
    tree = STRtree(pieces)
    out = np.full(len(edges), "", dtype=object)
    cand = edges.index[edges["bike_ok"].fillna(False).astype(bool)] \
        if "bike_ok" in edges else edges.index
    with step(f"conflating {len(pieces)} bikeway pieces onto {len(cand)} edges", log):
        for i in cand:
            geom = edges.geometry.loc[i]
            if geom is None or geom.is_empty or geom.length < 1.0:
                continue
            xy, br = _sample(geom, SAMPLE_M)
            from shapely.geometry import Point
            pts = [Point(x, y) for x, y in xy]
            hit = np.zeros(len(pts), dtype=int) - 1
            for k, p in enumerate(pts):
                best = -1
                for j in tree.query(p.buffer(MATCH_M)):
                    if pieces[j].distance(p) > MATCH_M:
                        continue
                    da = abs((piece_br[j] - br[k] + np.pi / 2) % np.pi - np.pi / 2)
                    if np.degrees(da) > MATCH_DEG:
                        continue
                    if best < 0 or _RANK[piece_fac[j]] > _RANK[piece_fac[best]]:
                        best = j
                hit[k] = best
            matched = hit >= 0
            if matched.sum() * 2 >= len(pts):
                facs = piece_fac[hit[matched]]
                out[edges.index.get_loc(i)] = max(set(facs), key=lambda f: _RANK[f])
    s = pd.Series(out, index=edges.index, name="bikeway_facility")
    log.info("edges with an inventory facility: %s", dict(s[s != ""].value_counts()))
    return s


def stress(edges: gpd.GeoDataFrame, facility: pd.Series | None = None) -> np.ndarray:
    """Bike comfort multiplier per edge (1.0 = an ordinary residential block)."""
    cls = edges["cls"].fillna("unknown").to_numpy()
    m = np.array([CLASS_STRESS.get(c, 1.1) for c in cls])
    if facility is None:
        facility = edges["bikeway_facility"] if "bikeway_facility" in edges else pd.Series("", index=edges.index)
    fac = facility.fillna("").to_numpy()
    for f, v in FACILITY_STRESS.items():
        m[fac == f] = np.minimum(m[fac == f], v)
    r = fac == "route"
    m[r] = np.maximum(0.9, np.minimum(m[r], ROUTE_CAP) * ROUTE_SOFTEN)
    return m
