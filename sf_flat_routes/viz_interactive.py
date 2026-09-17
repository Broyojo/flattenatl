"""Interactive web map with a working routing interface.

The map is a single self-contained HTML file: MapLibre GL JS is loaded from a
CDN, but every byte of data is embedded, so the file can be moved around and
opened directly.

Because a static file cannot run Dijkstra, the routing UI is served from
**precomputed routes**: every ordered neighborhood pair, under every
objective, for both travel modes.  Selecting an origin, a destination and a
preference looks the route up rather than solving it, which makes the UI
instant and keeps the analysis and the map in exact agreement.  Route
geometry is stored as encoded polylines and elevation profiles are
downsampled, which keeps the whole thing to a manageable size.

Layers
------
neighborhoods, street network coloured by gradient, discovered flat
corridors, steep barriers, critical passes, lowland basins, bicycle
facilities and car-free / low-stress streets.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .config import OUTPUT_DIR
from .utils import get_logger, human_bytes, step

log = get_logger("sf_flat_routes.viz_interactive")

INTERACTIVE_HTML = OUTPUT_DIR / "sf_flat_routes_map.html"
WEB_DIR = Path(__file__).resolve().parent / "web"
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"

# --------------------------------------------------------------------------
# layer preparation
# --------------------------------------------------------------------------
def _round_geometry(geo: dict, ndigits: int = 5) -> dict:
    """Round every coordinate in a __geo_interface__ mapping.

    Full-precision floats serialise to ~17 characters each; five decimal
    places is about 1 m at this latitude, which is finer than the source
    data's own accuracy, and shrinks the embedded GeoJSON several-fold.
    """
    def walk(c):
        if isinstance(c, (list, tuple)):
            if c and isinstance(c[0], (int, float)):
                return [round(float(v), ndigits) for v in c[:2]]
            return [walk(x) for x in c]
        return c
    return {"type": geo["type"], "coordinates": walk(geo["coordinates"])}


def _geojson(gdf, props: list[str], simplify: float = 4.0) -> dict:
    """GeoDataFrame -> GeoJSON dict in WGS84, geometry simplified in metres."""
    g = gdf.copy()
    if simplify:
        g["geometry"] = g.geometry.simplify(simplify, preserve_topology=False)
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g = g.to_crs("EPSG:4326")
    keep = [c for c in props if c in g.columns]
    feats = []
    for _, r in g.iterrows():
        p = {}
        for c in keep:
            v = r[c]
            if isinstance(v, (np.floating, float)):
                v = None if not np.isfinite(v) else round(float(v), 5)
            elif isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.bool_, bool)):
                v = bool(v)
            elif v is not None and not isinstance(v, str):
                v = str(v)
            p[c] = v
        feats.append({"type": "Feature", "properties": p,
                      "geometry": _round_geometry(r.geometry.__geo_interface__)})
    return {"type": "FeatureCollection", "features": feats}


def build_layers(ctx, corridors, passes, barriers, basins):
    """The small vector overlays. The street network is *not* here.

    The network is served from the packed graph instead, so its geometry
    exists exactly once in the file and the gradient display can never
    disagree with what the router uses.
    """
    edges = ctx.edges
    layers = {
        "neighborhoods": _geojson(ctx.neighborhoods,
                                  ["neighborhood", "area_km2"], simplify=12.0),
        "corridors": _geojson(
            corridors.to_crs(edges.crs) if corridors.crs != edges.crs else corridors,
            ["corridor_id", "corridor_name", "street_names", "mode",
             "length_km", "mean_abs_grade", "max_grade", "gain_per_km",
             "pair_count_max", "neighborhood_span", "climb_saved_m",
             "elev_min_m", "elev_max_m", "neighborhoods", "total_score"],
            simplify=5.0),
        "passes": _geojson(
            passes.to_crs(edges.crs) if passes.crs != edges.crs else passes,
            ["edge_id", "name", "neighborhood", "pass_elev_m", "pass_elev_ft",
             "pairs_served", "max_abs_grade", "neighborhoods_separated"],
            simplify=2.0),
        "barriers": _geojson(
            (barriers.to_crs(edges.crs) if barriers.crs != edges.crs
             else barriers).head(350),
            ["edge_id", "name", "neighborhood", "max_abs_grade", "length_m",
             "shortest_use", "flat_use_per_objective", "unavoidability"],
            simplify=2.0),
    }
    bike = edges[edges["bike_facility"].astype(bool) & (edges["bike_facility"] != "")]
    layers["bike_network"] = _geojson(
        bike, ["name", "cls", "bike_facility", "length_m", "max_abs_grade"],
        simplify=5.0)
    carfree = edges[edges["cls"].isin(["pedestrian", "living_street"])
                    | (edges["bike_facility"] == "car_free_street")]
    layers["low_stress"] = _geojson(
        carfree, ["name", "cls", "bike_facility", "length_m"], simplify=5.0)
    if basins is not None and len(basins):
        b = basins.to_crs(edges.crs) if basins.crs != edges.crs else basins
        agg = b.dissolve(by="basin", aggfunc={"basin_label": "first",
                                              "length_m": "sum"}).reset_index()
        agg["length_km"] = agg["length_m"] / 1000.0
        layers["basins"] = _geojson(agg, ["basin", "basin_label", "length_km"],
                                    simplify=14.0)
    return layers


#: Guided examples, so that opening the map demonstrates the findings
#: without anyone having to know which neighborhoods to pick. Notes are
#: filled in from the analysis outputs at build time.
_EXAMPLE_PAIRS = (
    ("Mission", "Outer Sunset", "walk", "min_climb",
     "crossing the city east to west"),
    ("Noe Valley", "Financial District", "walk", "min_climb",
     "almost all the climbing is optional"),
    ("Bayview", "Golden Gate Park", "walk", "min_climb",
     "the biggest single saving in the city"),
    ("Mission", "Marina", "bike", "balanced",
     "by bicycle, over the northern saddles"),
    ("West of Twin Peaks", "Downtown/Civic Center", "walk", "grade_averse",
     "behind the Twin Peaks barrier: no cheap way over"),
)


def _examples(points: dict) -> list[dict]:
    """Attach measured savings to each guided example."""
    from .pairs import PAIRS_PARQUET
    import pandas as pd

    out = []
    table = None
    if PAIRS_PARQUET.exists():
        table = pd.read_parquet(PAIRS_PARQUET)
    for o, d, mode, prof, blurb in _EXAMPLE_PAIRS:
        if o not in points.get(mode, {}) or d not in points.get(mode, {}):
            continue
        note = blurb
        if table is not None:
            sel = table[(table["mode"] == mode) & (table["profile"] == prof)
                        & (table["origin"] == o) & (table["destination"] == d)]
            if len(sel):
                r = sel.iloc[0]
                note = (f"{blurb} &mdash; "
                        f"{r['shortest_gain_m']*3.28084:.0f} ft of climbing "
                        f"becomes {r['elev_gain_m']*3.28084:.0f} ft")
        out.append({"o": o, "d": d, "mode": mode, "profile": prof,
                    "note": note})
    return out


def _asset(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def _vendor(name: str) -> str:
    """Read a vendored asset for inlining (see ``vendor/README.md``)."""
    return (VENDOR_DIR / name).read_text(encoding="utf-8")


def make_interactive_map(ctx, corridors, passes, barriers, pairs_df=None,
                         arc_store=None, basins=None) -> Path:
    """Assemble and write the interactive map.

    ``pairs_df`` and ``arc_store`` are accepted for call-site compatibility
    but are no longer needed: the map routes for itself rather than looking
    up precomputed answers.
    """
    from .webgraph import build_payload, bundle

    with step("preparing interactive map layers", log):
        layers = build_layers(ctx, corridors, passes, barriers, basins)

    graph = build_payload(ctx.edges, ctx.directed)

    pts = {}
    for mode, gdf in ctx.points.items():
        g = gdf.to_crs("EPSG:4326")
        pts[mode] = {r["neighborhood"]: [round(r.geometry.x, 6),
                                         round(r.geometry.y, 6)]
                     for _, r in g.iterrows()}
    names = sorted(set(pts.get("walk", {})) | set(pts.get("bike", {})))

    with step("bundling and compressing the map payload", log):
        packed = bundle(graph, {
            "geom": graph["geom"],
            "layers": json.dumps(layers, separators=(",", ":")),
        })
    payload = {
        "manifest": packed["manifest"], "bundle": packed["b64"],
        "meta": packed["meta"], "points": pts, "neighborhood_names": names,
        "examples": _examples(pts),
    }

    with step("writing the interactive HTML", log):
        html = _asset("index.html")
        html = html.replace("/*__LEAFLET_CSS__*/", _vendor("leaflet-1.9.4.css"))
        html = html.replace("/*__APP_CSS__*/", _asset("app.css"))
        html = html.replace("/*__LEAFLET_JS__*/", _vendor("leaflet-1.9.4.min.js"))
        html = html.replace("/*__APP_JS__*/", _asset("app.js"))
        html = html.replace("/*__DATA__*/",
                            json.dumps(payload, separators=(",", ":")))
        INTERACTIVE_HTML.parent.mkdir(parents=True, exist_ok=True)
        INTERACTIVE_HTML.write_text(html, encoding="utf-8")
    log.info("wrote %s (%s)", INTERACTIVE_HTML.name,
             human_bytes(INTERACTIVE_HTML.stat().st_size))
    return INTERACTIVE_HTML
