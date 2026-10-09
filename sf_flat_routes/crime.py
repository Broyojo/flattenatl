"""Reported violent crime, for the route page's "avoid high-crime areas".

The Atlanta Police Department publishes every incident report since 2021 as
an ArcGIS feature service (the one behind its open-data site), updated
daily, with the offense, where it happened and what kind of place that was.
This module asks it for the last two years of the offenses that bear on
walking down a street, and turns them into one number per block.

**What counts.**  Violent crime against a person, in a public place, between
people who are not family:

* robbery, aggravated assault, murder, kidnapping, rape and the other
  forcible sex offenses, at full weight;
* simple assault at half weight;
* nothing flagged as family violence, and nothing whose location is a home,
  an apartment, a hotel room, a shelter, a jail or "cyberspace".

That drops two thirds of the crimes against persons, which is the point:
most assaults happen indoors between people who know each other, and say
nothing about the pavement outside.  Property crime is left out altogether.
A car break-in is a reason not to park somewhere, not a reason not to walk.

**The number.**  Incidents are binned on a 25 m grid, spread with a 75 m
Gaussian (they are geocoded to a street address, so finer would be false
precision) and divided through to weighted incidents per square kilometre
per year.  A block takes the highest value along its length.  The page
calls a block *high-crime* at ``HIGH_CRIME_DENSITY`` or more.

**What it is not.**  A count, not a rate: the busiest streets in the city
score high partly because more people are on them to be robbed.  Reported
crime, not crime.  And a record of where incidents were written up, which
follows where police are as well as where trouble is.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import requests

from .config import RAW_DIR, REPO_URL
from .utils import get_logger

log = get_logger("sf_flat_routes.crime")

CRIME_JSON = RAW_DIR / "apd_crime.json"
CRIME_LAYER = ("https://services3.arcgis.com/Et5Qfajgiyosiw4d/arcgis/rest/services/"
               "OpenDataWebsite_Crime_view/FeatureServer/0")

#: Years of incidents used. Two is recent enough to describe the city as it
#: is and long enough that one bad weekend does not paint a block.
WINDOW_YEARS = 2
#: offense -> weight
OFFENSES = {
    "Robbery": 1.0, "Aggravated Assault": 1.0,
    "Murder & Nonnegligent Manslaughter": 1.0, "Kidnapping/Abduction": 1.0,
    "Rape": 1.0, "Sodomy": 1.0, "Sexual Assault with An Object": 1.0, "Fondling": 1.0,
    "Simple Assault": 0.5,
}
#: location types that are not the street
PRIVATE_PLACES = frozenset({
    "RESIDENCE_HOME", "APARTMENT", "HOTEL_MOTEL_ETC", "CYBERSPACE",
    "SHELTER_MISSION_HOMELESS", "JAIL_PRISON_PENITENTIARY_CORRECTIONS_FACILITY",
})
GRID_M = 25.0
SIGMA_M = 75.0
#: Weighted incidents per km2 per year at which a block is "high-crime".
#: At 75 that is about five within 150 m in a year, and 4% of the city's
#: street length: a third of Downtown, a fifth of Midtown and West End, and
#: pockets elsewhere. 50 nearly doubles it (7%); 100 nearly halves it (2.4%).
HIGH_CRIME_DENSITY = 75.0
#: The page stores density as a byte, in steps of this.
DENSITY_STEP = 2.0
_UA = {"User-Agent": f"flattenatl/1.0 ({REPO_URL})"}


def download_crime(force: bool = False, today: float | None = None) -> Path:
    """Fetch and cache the last ``WINDOW_YEARS`` of the offenses that count."""
    if CRIME_JSON.exists() and not force:
        log.info("cached %s", CRIME_JSON.name)
        return CRIME_JSON
    now = time.gmtime(today if today is not None else time.time())
    since = f"{now.tm_year - WINDOW_YEARS:04d}-{now.tm_mon:02d}-{now.tm_mday:02d}"
    until = time.strftime("%Y-%m-%d", now)
    names = ",".join("'" + n.replace("'", "''") + "'" for n in OFFENSES)
    # (a handful of reports carry dates far in the future: bound both ends)
    where = (f"OccurredFromDate >= DATE '{since}' AND OccurredFromDate <= DATE '{until} 23:59:59' "
             f"AND NIBRS_Offense IN ({names})")
    rows, offset = [], 0
    while True:
        r = requests.get(CRIME_LAYER + "/query", headers=_UA, timeout=120, params={
            "where": where, "returnGeometry": "false", "orderByFields": "OBJECTID",
            "outFields": "NIBRS_Offense,LocationType,GAFamilyViolenceIndicator,"
                         "OccurredFromDate,Longitude,Latitude",
            "resultOffset": offset, "resultRecordCount": 2000, "f": "json"})
        r.raise_for_status()
        d = r.json()
        if "error" in d:
            raise RuntimeError(f"APD crime service: {d['error']}")
        feats = d.get("features", [])
        rows += [f["attributes"] for f in feats]
        offset += len(feats)
        if not feats or not d.get("exceededTransferLimit"):
            break
    data = {"source": CRIME_LAYER, "since": since, "until": until, "incidents": rows}
    CRIME_JSON.parent.mkdir(parents=True, exist_ok=True)
    CRIME_JSON.write_text(json.dumps(data, separators=(",", ":")))
    log.info("APD crime: %d reports of the listed offenses, %s to %s", len(rows), since, until)
    return CRIME_JSON


def street_incidents(path: Path = CRIME_JSON):
    """The incidents that count, as (lon, lat, weight) arrays, and the meta."""
    data = json.loads(path.read_text())
    lon, lat, w = [], [], []
    for a in data["incidents"]:
        weight = OFFENSES.get(a.get("NIBRS_Offense"))
        if (weight is None or a.get("GAFamilyViolenceIndicator") == "YES"
                or a.get("LocationType") in PRIVATE_PLACES):
            continue
        x, y = a.get("Longitude"), a.get("Latitude")
        if not x or not y:
            continue
        lon.append(float(x)); lat.append(float(y)); w.append(weight)
    log.info("crime: %d of %d reports are street-relevant (no family violence, "
             "not in a home)", len(w), len(data["incidents"]))
    return np.asarray(lon), np.asarray(lat), np.asarray(w), data


def edge_density(edges, lon, lat, weight, years: float = WINDOW_YEARS) -> np.ndarray:
    """Weighted incidents per km2 per year, per edge: the peak along it."""
    import shapely
    from pyproj import Transformer
    from scipy.ndimage import gaussian_filter

    from .config import CRS_GEOGRAPHIC

    if not len(weight):
        return np.zeros(len(edges))
    tr = Transformer.from_crs(CRS_GEOGRAPHIC, edges.crs, always_xy=True)
    x, y = tr.transform(lon, lat)
    b = edges.total_bounds
    pad = 6 * SIGMA_M
    x0, y0 = b[0] - pad, b[1] - pad
    nx = int((b[2] - b[0] + 2 * pad) / GRID_M) + 1
    ny = int((b[3] - b[1] + 2 * pad) / GRID_M) + 1
    H, _, _ = np.histogram2d(y, x, bins=[ny, nx], weights=weight,
                             range=[[y0, y0 + ny * GRID_M], [x0, x0 + nx * GRID_M]])
    D = gaussian_filter(H, SIGMA_M / GRID_M, mode="constant") / (GRID_M * GRID_M / 1e6) / years
    geoms = edges.geometry.values
    out = np.zeros(len(edges))
    for frac in (0.05, 0.25, 0.5, 0.75, 0.95):
        p = shapely.line_interpolate_point(geoms, frac, normalized=True)
        col = np.clip(((shapely.get_x(p) - x0) / GRID_M).astype(int), 0, nx - 1)
        row = np.clip(((shapely.get_y(p) - y0) / GRID_M).astype(int), 0, ny - 1)
        out = np.maximum(out, D[row, col])
    return out


def build_crime(edges, path: Path = CRIME_JSON) -> dict | None:
    """Per-edge density as a byte array for the page, with what it means."""
    if not path.exists():
        return None
    lon, lat, w, data = street_incidents(path)
    dens = edge_density(edges, lon, lat, w)
    length = edges["length_m"].to_numpy()
    hot = dens >= HIGH_CRIME_DENSITY
    log.info("crime: %.1f%% of street length (%d blocks) is at or above %g per km2 per year",
             100 * length[hot].sum() / length.sum(), int(hot.sum()), HIGH_CRIME_DENSITY)
    return {
        "edge_crime": np.clip(np.round(dens / DENSITY_STEP), 0, 255).astype("<u1"),
        "meta": {"step": DENSITY_STEP, "threshold": HIGH_CRIME_DENSITY,
                 "since": data["since"], "until": data["until"],
                 "incidents": int(len(w)), "years": WINDOW_YEARS,
                 "share": round(float(length[hot].sum() / length.sum()), 4)},
    }
