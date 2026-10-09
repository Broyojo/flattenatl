"""License-plate cameras, for the route page's "avoid Flock cameras".

DeFlock (deflock.me) is a crowd-sourced map of automated license plate
readers.  Its database *is* OpenStreetMap: a camera is a node tagged
``man_made=surveillance`` + ``surveillance:type=ALPR``, usually with the
``direction`` it faces and its ``manufacturer`` (about nine in ten in
Atlanta are Flock Safety).  So there are two ways to the same data:

* the **Overpass API**, which queries live OpenStreetMap for a bounding
  box and allows cross-origin requests, so the page itself can ask it; and
* **DeFlock's CDN**, which serves the world in 20-degree tiles refreshed
  from OpenStreetMap.  It sends no CORS header, and the tile holding
  Atlanta is 8 MB, so it is only usable here, at build time, as a fallback.

The build bakes a snapshot into the page, so the feature works at once and
offline.  When a visitor ticks the box the page asks Overpass for the
current set and re-plans if it differs (``simple.js``, ``Cameras``).

Which streets a camera watches is worked out in the browser, not here, so
that the live set and the snapshot are treated identically.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .config import CITY_BBOX, RAW_DIR, REPO_URL
from .utils import get_logger

log = get_logger("sf_flat_routes.cameras")

CAMERAS_JSON = RAW_DIR / "alpr_cameras.json"

#: Overpass instances, tried in order (by the build and by the page).
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
DEFLOCK_INDEX = "https://cdn.deflock.me/regions/index.json"
#: Cameras this far outside the city limits are kept: one just over the
#: line can still see a street inside it.
CITY_MARGIN_M = 300.0
_UA = {"User-Agent": f"flattenatl/1.0 ({REPO_URL})"}


def overpass_query(bbox=CITY_BBOX) -> str:
    """Overpass QL for every ALPR node in ``bbox`` (lon/lat order as config)."""
    lon_min, lon_max, lat_min, lat_max = bbox
    return ('[out:json][timeout:25];node["man_made"="surveillance"]'
            f'["surveillance:type"="ALPR"]({lat_min},{lon_min},{lat_max},{lon_max});'
            "out body;")


def _direction(tags: dict) -> str:
    return str(tags.get("direction") or tags.get("camera:direction") or "").strip()


def _from_overpass(bbox) -> dict:
    last = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": overpass_query(bbox)}, headers=_UA, timeout=90)
            r.raise_for_status()
            d = r.json()
            return {
                "source": "OpenStreetMap via Overpass API",
                "asof": d.get("osm3s", {}).get("timestamp_osm_base", ""),
                "cameras": [{"id": e["id"], "lon": e["lon"], "lat": e["lat"],
                             "direction": _direction(e.get("tags", {})),
                             "manufacturer": e.get("tags", {}).get("manufacturer", "")}
                            for e in d.get("elements", []) if e.get("type") == "node"],
            }
        except Exception as exc:
            last = exc
            log.warning("Overpass at %s failed (%s)", url.split("/")[2], exc)
    raise RuntimeError(f"no Overpass instance answered: {last}")


def _from_deflock(bbox) -> dict:
    """DeFlock's own tiles: the same nodes, a few hours behind."""
    lon_min, lon_max, lat_min, lat_max = bbox
    idx = requests.get(DEFLOCK_INDEX, headers=_UA, timeout=60).json()
    size = int(idx["tile_size_degrees"])
    cams = []
    lat0 = int(lat_min // size * size); lon0 = int(lon_min // size * size)
    for la in range(lat0, int(lat_max // size * size) + 1, size):
        for lo in range(lon0, int(lon_max // size * size) + 1, size):
            if f"{la}/{lo}" not in idx["regions"]:
                continue
            tile = requests.get(idx["tile_url"].format(lat=la, lon=lo), headers=_UA,
                                timeout=180).json()
            cams += [{"id": t["id"], "lon": t["lon"], "lat": t["lat"],
                      "direction": _direction(t.get("tags", {})),
                      "manufacturer": t.get("tags", {}).get("manufacturer", "")}
                     for t in tile
                     if lat_min <= t["lat"] <= lat_max and lon_min <= t["lon"] <= lon_max]
    asof = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(int(idx["tile_url"].rsplit("v=", 1)[-1])))
    return {"source": "DeFlock (deflock.me) tiles of OpenStreetMap", "asof": asof,
            "cameras": cams}


def download_cameras(force: bool = False, bbox=CITY_BBOX) -> Path:
    """Fetch and cache the cameras in the study box."""
    if CAMERAS_JSON.exists() and not force:
        log.info("cached %s", CAMERAS_JSON.name)
        return CAMERAS_JSON
    try:
        data = _from_overpass(bbox)
    except Exception as exc:
        log.warning("%s; falling back to DeFlock's tiles", exc)
        data = _from_deflock(bbox)
    CAMERAS_JSON.parent.mkdir(parents=True, exist_ok=True)
    CAMERAS_JSON.write_text(json.dumps(data, separators=(",", ":")))
    log.info("license-plate cameras: %d in the study box, as of %s (%s)",
             len(data["cameras"]), data["asof"], data["source"])
    return CAMERAS_JSON


def build_cameras(path: Path = CAMERAS_JSON) -> dict | None:
    """The snapshot the page carries: cameras in or just outside the city.

    ``cams`` is a list of ``[lon, lat, direction]``, the direction as
    OpenStreetMap has it ("180", "70;340", "270-315" or ""), because the
    page has to read the same strings from a live Overpass answer anyway.
    """
    import numpy as np
    import shapely
    from pyproj import Transformer

    from .config import CRS_GEOGRAPHIC, CRS_PROJECTED

    if not path.exists():
        return None
    data = json.loads(path.read_text())
    cams = data["cameras"]
    keep = np.ones(len(cams), dtype=bool)
    try:
        from .neighborhoods import city_boundary
        boundary = city_boundary(buffer_m=CITY_MARGIN_M)
        tr = Transformer.from_crs(CRS_GEOGRAPHIC, CRS_PROJECTED, always_xy=True)
        x, y = tr.transform([c["lon"] for c in cams], [c["lat"] for c in cams])
        shapely.prepare(boundary)
        keep = shapely.contains_xy(boundary, x, y)
    except Exception as exc:   # no boundary on disk: keep the whole box
        log.warning("city boundary unavailable (%s); cameras not clipped", exc)
    kept = sorted(([round(c["lon"], 6), round(c["lat"], 6), c["direction"]]
                   for c, k in zip(cams, keep) if k), key=lambda c: (c[1], c[0]))
    flock = sum(1 for c, k in zip(cams, keep) if k and "flock" in c["manufacturer"].lower())
    log.info("cameras: %d of %d in or near the city, %d of them Flock Safety",
             len(kept), len(cams), flock)
    return {"asof": data["asof"], "source": data["source"], "cams": kept,
            "flock": flock, "bbox": list(CITY_BBOX), "query": overpass_query(),
            "overpass": list(OVERPASS_URLS)}
