"""Offline place index for the route page: addresses, places, and the base.

A static page cannot call a geocoding API without carrying a billed key in
public, and should not need one for a single city anyway, so the index is
built here and shipped with the page.
Three sources, all already in hand or fetched the same way as the streets:

* **Street intersections** are derived in the browser from the graph itself
  ("24th St & Mission St"); nothing to pack.
* **Mapped features** come from Overture's base theme, which is OpenStreetMap
  data: parks, playgrounds, schools, hospitals, plazas, stations, piers,
  bridges, viewpoints, peaks and beaches, each placed on its mapped outline.
  These are authoritative and are never pruned.
* **Places** come from Overture's places theme (Meta/Microsoft POI data):
  landmarks, museums, shops, cafes and so on. The feed is noisy -- the same
  name recurs at several spots, some of them nowhere near the real thing --
  so a record is kept only where nearby records corroborate it, and it is
  dropped when a mapped feature already carries its name.
* **Addresses** come from Overture's addresses theme (OpenAddresses data for
  San Francisco), deduplicated to one point per street number.

The hillshade base is rendered from the same lidar DEM the analysis uses and
reprojected to WGS84 so it overlays correctly, then palette-quantised: it is
a quiet grey image and does not need 24-bit colour.
"""
from __future__ import annotations

import base64
import io
import re

import numpy as np
import pandas as pd

from .config import CITY_BBOX, LAT_M_PER_DEG, LON_M_PER_DEG, PROCESSED_DIR
from .download import ADDRESSES_PARQUET, BASE_PARQUETS, PLACES_PARQUET
from .utils import get_logger, step

log = get_logger("sf_flat_routes.places")

HILLSHADE_PNG = PROCESSED_DIR / "hillshade_light.png"

#: Overture primary categories kept, grouped for display. Anything not listed
#: is dropped unless it is a landmark-like category matched by _KEEP_RE.
CATEGORY_GROUPS = {
    "park": ["park", "garden", "playground", "beach", "dog_park", "hiking_trail",
             "scenic_point", "nature_preserve", "botanical_garden", "national_park",
             "state_park", "plaza", "picnic_ground"],
    "landmark": ["landmark_and_historical_building", "monument", "tourist_attraction",
                 "museum", "art_museum", "history_museum", "science_museum",
                 "aquarium", "zoo", "stadium_arena", "observatory", "lighthouse",
                 "historical_site", "memorial", "pier", "marina", "amusement_park",
                 "theatre", "performing_arts_theatre", "concert_hall", "cinema",
                 "library", "public_library"],
    "transit": ["train_station", "light_rail_station", "subway_station",
                "bus_station", "ferry_terminal", "transit_station", "cable_car_station",
                "metro_station", "public_transportation"],
    "school": ["school", "university", "college_university", "high_school",
               "elementary_school", "middle_school", "community_college", "campus"],
    "civic": ["city_hall", "courthouse", "post_office", "hospital", "fire_station",
              "police_station", "community_center", "recreation_center",
              "swimming_pool", "public_swimming_pool", "church_cathedral", "synagogue",
              "mosque", "temple", "farmers_market", "public_market"],
    "food": ["restaurant", "cafe", "coffee_shop", "bakery", "bar", "pub", "brewery",
             "ice_cream_shop", "pizza_restaurant", "taco_restaurant", "diner",
             "dessert_shop", "tea_room", "wine_bar", "cocktail_bar", "food_court"],
    "shop": ["grocery_store", "supermarket", "bookstore", "shopping_center",
             "hardware_store", "bicycle_shop", "pharmacy", "farmers_market",
             "convenience_store", "department_store", "record_store", "florist"],
    "lodging": ["hotel", "hostel", "bed_and_breakfast"],
}
_GROUP_OF = {c: g for g, cs in CATEGORY_GROUPS.items() for c in cs}

#: The feed has over a thousand categories and the lists above name about a
#: hundred, which left out every restaurant filed under its cuisine, every
#: clothing shop, every gym, and one of Atlanta's Whole Foods (an
#: "organic_grocery_store"). These catch the rest of the places people
#: actually walk or ride to, by the words in the category. First match wins;
#: offices, agencies and trades are still left out.
_WORD_GROUPS = (
    ("salon", {"salon", "barber", "spa", "spas", "nail", "tattoo"}),
    ("food", {"restaurant", "cafe", "coffee", "bakery", "bar", "pub", "brewery",
              "diner", "pizza", "dessert", "juice", "sandwich", "deli", "donut",
              "donuts", "bistro", "steakhouse", "winery", "distillery", "lounge",
              "bagel", "gastropub", "creperie", "smoothie", "food", "bars"}),
    ("gym", {"gym", "gyms", "fitness", "yoga", "pilates", "climbing", "boxing",
             "crossfit", "cycling"}),
    ("shop", {"store", "shop", "boutique", "shopping", "grocery", "supermarket",
              "market", "pharmacy", "mall", "outlet", "florist"}),
    ("bank", {"bank", "banks"}),
    ("clinic", {"clinic", "hospital", "urgent"}),
)
_EXACT_GROUPS = {
    "music_venue": "venue", "venue_and_event_space": "venue", "art_gallery": "venue",
    "arts_and_entertainment": "venue", "night_club": "venue", "dance_club": "venue",
    "comedy_club": "venue", "jazz_and_blues": "venue", "bowling_alley": "venue",
    "arcade": "venue", "topic_concert_venue": "venue", "karaoke": "venue",
    "medical_center": "clinic", "accommodation": "lodging",
    "apartments": "apartments", "condominium": "apartments",
}


def _group_of(category) -> str | None:
    """The display kind for an Overture category, or None to leave it out."""
    if not isinstance(category, str):
        return None
    g = _GROUP_OF.get(category) or _EXACT_GROUPS.get(category)
    if g:
        return g
    words = set(category.split("_"))
    for group, vocab in _WORD_GROUPS:
        if words & vocab:
            return group
    return "landmark" if _KEEP_RE.search(category) else None

#: Overture base-theme (OpenStreetMap) classes kept, with the kind shown in
#: the search list. Keyed by (type, class).
BASE_CLASSES = {
    ("land_use", "park"): "park", ("land_use", "dog_park"): "dog park",
    ("land_use", "playground"): "playground", ("land_use", "garden"): "garden",
    ("land_use", "allotments"): "community garden", ("land_use", "plaza"): "plaza",
    ("land_use", "pedestrian"): "plaza", ("land_use", "school"): "school",
    ("land_use", "kindergarten"): "school", ("land_use", "university"): "university",
    ("land_use", "college"): "college", ("land_use", "hospital"): "hospital",
    ("land_use", "golf_course"): "golf course", ("land_use", "stadium"): "stadium",
    ("land_use", "marina"): "marina", ("land_use", "recreation_ground"): "park",
    ("land_use", "national_park"): "park", ("land_use", "military"): "landmark",
    ("land_use", "protected_landscape_seascape"): "park",
    ("infrastructure", "railway_station"): "station",
    ("infrastructure", "subway_station"): "station",
    ("infrastructure", "ferry_terminal"): "ferry", ("infrastructure", "pier"): "pier",
    ("infrastructure", "bridge"): "bridge", ("infrastructure", "viewpoint"): "viewpoint",
    ("infrastructure", "observation"): "landmark",
    ("infrastructure", "communication_tower"): "landmark",
    ("land", "peak"): "peak", ("land", "hill"): "hill", ("land", "beach"): "beach",
    ("land", "island"): "island", ("land", "islet"): "island",
}
_KEEP_RE = re.compile(r"park|garden|museum|station|landmark|monument|library|"
                      r"theat|school|universit|college|church|cathedral|hospital|"
                      r"beach|plaza|square|pier|market|stadium|trail|overlook", re.I)

_SUFFIX = {
    "ST": "St", "AVE": "Ave", "BLVD": "Blvd", "DR": "Dr", "RD": "Rd", "CT": "Ct",
    "PL": "Pl", "LN": "Ln", "TER": "Ter", "WAY": "Way", "HWY": "Hwy", "PKWY": "Pkwy",
    "CIR": "Cir", "ALY": "Aly", "SQ": "Sq", "TERR": "Ter", "STWY": "Stwy",
    "HL": "Hl", "WALK": "Walk", "LOOP": "Loop", "ROW": "Row", "PATH": "Path",
    "EXPY": "Expy", "PLZ": "Plz",
}
_DIR = {"N": "N", "S": "S", "E": "E", "W": "W"}
#: Atlanta addresses end in a quadrant, which the address feed spells out
#: ("PEACHTREE Street Northeast") and everyone else writes as two letters.
_QUADRANT = {"NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE",
             "SOUTHWEST": "SW", "NE": "NE", "NW": "NW", "SE": "SE", "SW": "SW"}
#: Words that stay lower-case inside a street name ("Ponce de Leon").
_SMALL = {"DE", "LA", "DEL", "OF", "THE", "AT"}


def _title_street(raw: str) -> str:
    """'PONCE DE LEON Avenue Northeast' -> 'Ponce de Leon Avenue NE'.

    Also 'JOHN MUIR DR' -> 'John Muir Dr'; keeps ordinals ('10TH' -> '10th').
    """
    out = []
    toks = str(raw).split()
    for i, tok in enumerate(toks):
        up = tok.upper()
        if tok in _SUFFIX:
            out.append(_SUFFIX[tok])
        elif re.fullmatch(r"\d+(ST|ND|RD|TH)", up):
            out.append(up.lower())
        elif up in _QUADRANT and i == len(toks) - 1 and len(out):
            out.append(_QUADRANT[up])
        elif up in _SMALL and 0 < i < len(toks) - 1:
            out.append(up.lower())
        elif tok in _DIR and len(out):
            out.append(tok)
        else:
            out.append(tok.capitalize() if not tok.isdigit() else tok)
    return " ".join(out)


def _support(names: pd.Series, lon: np.ndarray, lat: np.ndarray,
             all_names: pd.Series, all_lon: np.ndarray, all_lat: np.ndarray,
             radius_m: float = 300.0) -> np.ndarray:
    """How many nearby records mention each name.

    'Dolores Park' at the real park is surrounded by 'Dolores Park Cafe',
    'Dolores Park Tennis Courts' and so on; a stray 'Dolores Park' dropped in
    the Tenderloin has none of that. The feed carries several such strays
    with full confidence, so the name alone cannot pick the right one.
    (The examples in this module are San Francisco's, where the rules were
    worked out; Piedmont Park and its conservancy, tennis centre and dog
    park behave the same way.)
    """
    cell = radius_m / LAT_M_PER_DEG
    grid: dict[tuple[int, int], list[int]] = {}
    low = all_names.str.lower().to_numpy()
    for i, (x, y) in enumerate(zip(all_lon, all_lat)):
        grid.setdefault((int(x / cell), int(y / cell)), []).append(i)
    out = np.zeros(len(names), dtype=int)
    for k, (n, x, y) in enumerate(zip(names.str.lower(), lon, lat)):
        cx, cy = int(x / cell), int(y / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((cx + dx, cy + dy), ()):
                    o = low[j]
                    if o != n and n in o and abs(all_lon[j] - x) * LON_M_PER_DEG < radius_m \
                            and abs(all_lat[j] - y) * LAT_M_PER_DEG < radius_m:
                        out[k] += 1
    return out


_CITY_SUFFIXES = {"atl", "atlanta", "atlanta ga", "atl ga", "ga",
                  "atlanta georgia", "georgia", "usa"}
# 'X Atlanta' and 'X ATL' are city suffixes even without a comma;
# a bare trailing 'Georgia' or 'GA' only after one ('Cafe Georgia').
_CORE_RE = re.compile(
    r"(?:[\s,\-/]+(?:atlanta|atl)(?:[\s,]+(?:ga|georgia|usa))?"
    r"|[,\-/]\s*(?:ga|georgia|usa))\s*$", re.I)


def _core(name: str) -> str:
    """'Piedmont Park, Atlanta' -> 'piedmont park'."""
    return _CORE_RE.sub("", str(name)).strip().lower()


def _prune_variants(df: pd.DataFrame, radius_m: float = 500.0) -> pd.DataFrame:
    """Drop 'Piedmont Park, Atlanta' when 'Piedmont Park' is 200 m away.

    The places feed carries many user-typed variants of the same name. A
    record is dropped when a better-supported kept name is a prefix of it
    (at a word boundary) and the two points are within ``radius_m``.
    """
    df = df.sort_values(["support", "conf"], ascending=False).reset_index(drop=True)
    kept_idx = []
    by_first: dict[str, list[int]] = {}
    lon = df["lon"].to_numpy(); lat = df["lat"].to_numpy()
    names = df["name"].tolist(); groups = df["group"].tolist()
    support = df["support"].to_numpy()
    for i, n in enumerate(names):
        first = n.split()[0].lower()
        dup = False
        for j in by_first.get(first, ()):
            k = names[j]
            if not (n.startswith(k) and len(n) > len(k) and n[len(k)] in " ,-/("):
                continue
            # a city suffix never marks a different place; anything else
            # (a branch, a sub-area) only when it is close by
            rest = n[len(k):].strip(" ,-/()").lower()
            if rest in _CITY_SUFFIXES:
                r = 6000.0                       # 'X, Atlanta' anywhere
            elif groups[i] != groups[j]:
                continue                         # 'Dolores Park Cafe' is a cafe
            elif support[j] >= 3 and support[i] == 0:
                r = 6000.0                       # a same-kind variant of a well-known name
            else:
                r = radius_m
            if (abs(lon[i] - lon[j]) * LON_M_PER_DEG < r
                    and abs(lat[i] - lat[j]) * LAT_M_PER_DEG < r):
                dup = True
                break
        if not dup:
            kept_idx.append(i)
            by_first.setdefault(first, []).append(i)
    out = df.iloc[kept_idx].reset_index(drop=True)
    log.info("places: %d near-duplicate name variants pruned", len(df) - len(out))
    return out


#: Kinds of place that come in branches. Upstream kept one record per name,
#: which is right for a landmark (the feed drops stray copies of a famous
#: name all over town, and only the best-supported one is real) and wrong
#: for a grocery: Atlanta has four Whole Foods inside the city, a dozen
#: Krogers and forty Starbucks, and "the" one is whichever is nearest.
BRANCH_GROUPS = frozenset({"food", "shop", "lodging", "civic", "gym", "salon",
                           "bank", "clinic"})
#: Two records of one name closer than this are the same branch.
BRANCH_MIN_M = 200.0


def _dedupe_branches(df: pd.DataFrame) -> pd.DataFrame:
    """One record per (name, kind), or one per branch for the kinds above.

    ``df`` must already be sorted best first; the best record of each
    branch (or of the name) is the one kept.
    """
    keep = np.zeros(len(df), dtype=bool)
    lon = df["lon"].to_numpy(); lat = df["lat"].to_numpy()
    for (_name, group), idx in df.groupby(["name", "group"], sort=False).indices.items():
        if group not in BRANCH_GROUPS or len(idx) == 1:
            keep[idx[0]] = True
            continue
        kept: list[int] = []
        for i in idx:
            if all(np.hypot((lon[i] - lon[j]) * LON_M_PER_DEG,
                            (lat[i] - lat[j]) * LAT_M_PER_DEG) >= BRANCH_MIN_M
                   for j in kept):
                kept.append(i)
        keep[kept] = True
    return df[keep].reset_index(drop=True)


def _neighborhood_of(lon, lat) -> tuple[list[str], np.ndarray]:
    """Neighborhood names and, per point, an index into them (0 = none).

    Shown beside a search result, so that four Whole Foods are "Midtown",
    "Old Fourth Ward", "Buckhead" and "Paces" rather than four of the same.
    """
    import geopandas as gpd

    from .config import CRS_GEOGRAPHIC
    try:
        from .neighborhoods import load_neighborhoods
        nb = load_neighborhoods()[["neighborhood", "geometry"]].to_crs(CRS_GEOGRAPHIC)
    except Exception as exc:
        log.warning("neighborhoods unavailable (%s); places carry no locality", exc)
        return [], np.zeros(len(lon), dtype=int)
    pts = gpd.GeoDataFrame({"i": np.arange(len(lon))},
                           geometry=gpd.points_from_xy(lon, lat), crs=CRS_GEOGRAPHIC)
    j = gpd.sjoin(pts, nb, how="left", predicate="within").drop_duplicates("i")
    names = sorted(nb["neighborhood"].unique())
    pos = {n: k + 1 for k, n in enumerate(names)}
    return names, j.sort_values("i")["neighborhood"].map(pos).fillna(0).astype(int).to_numpy()


def _street_of(lon, lat, wanted: np.ndarray, max_m: float = 120.0) -> tuple[list[str], np.ndarray]:
    """The named street nearest each wanted point (0 = none within reach).

    Two branches of a chain can share a neighborhood (Midtown has a Whole
    Foods on 14th Street and another on Ponce de Leon), and the street is
    what tells them apart. Only computed where asked for, which is the
    places whose name recurs.
    """
    import geopandas as gpd
    import shapely
    from pyproj import Transformer

    from .config import CRS_GEOGRAPHIC, CRS_PROJECTED
    out = np.zeros(len(lon), dtype=int)
    path = PROCESSED_DIR / "edges_metrics.parquet"
    idx = np.flatnonzero(wanted)
    if not path.exists() or not len(idx):
        return [], out
    edges = gpd.read_parquet(path, columns=["name", "cls", "geometry"])
    edges = edges[edges["name"].notna() & ~edges["cls"].isin(["footway", "path", "steps", "cycleway"])]
    tr = Transformer.from_crs(CRS_GEOGRAPHIC, CRS_PROJECTED, always_xy=True)
    x, y = tr.transform(np.asarray(lon)[idx], np.asarray(lat)[idx])
    pts = shapely.points(x, y)
    tree = shapely.STRtree(edges.geometry.values)
    near = tree.nearest(pts)
    close = shapely.distance(pts, edges.geometry.values[near]) <= max_m
    found = edges["name"].to_numpy()[near]
    names = sorted(set(found[close]))
    pos = {n: k + 1 for k, n in enumerate(names)}
    out[idx[close]] = [pos[n] for n in found[close]]
    return names, out


def _in_city(lon, lat) -> np.ndarray:
    """Which points fall inside the city limits (plus the network's margin).

    The Overture extracts cover the study box, and around Atlanta most of
    that box is somewhere else: Decatur, East Point, Sandy Springs, Cobb
    County.  A search hit out there would drop a pin a mile from the
    nearest routable street.
    """
    import shapely

    lon = np.asarray(lon, dtype="float64"); lat = np.asarray(lat, dtype="float64")
    box = ((lon >= CITY_BBOX[0]) & (lon <= CITY_BBOX[1])
           & (lat >= CITY_BBOX[2]) & (lat <= CITY_BBOX[3]))
    try:
        from pyproj import Transformer

        from .config import CRS_GEOGRAPHIC, CRS_PROJECTED
        from .neighborhoods import city_boundary
        boundary = city_boundary(buffer_m=250.0)
    except Exception as exc:   # no boundary on disk: the box is the best we have
        log.warning("city boundary unavailable (%s); places clipped to the box", exc)
        return box
    tr = Transformer.from_crs(CRS_GEOGRAPHIC, CRS_PROJECTED, always_xy=True)
    x, y = tr.transform(lon[box], lat[box])
    shapely.prepare(boundary)
    out = np.zeros(len(lon), dtype=bool)
    out[np.flatnonzero(box)] = shapely.contains_xy(boundary, x, y)
    return out


def build_base() -> pd.DataFrame:
    """Named OpenStreetMap features from the Overture base theme."""
    import pyarrow.parquet as pq
    import shapely
    frames = []
    for typ, path in BASE_PARQUETS.items():
        if not path.exists():
            continue
        t = pq.read_table(path, columns=["names", "class", "geometry"]).to_pandas()
        name = t["names"].map(lambda n: (n or {}).get("primary") if isinstance(n, dict) else None)
        kind = [BASE_CLASSES.get((typ, c)) for c in t["class"]]
        keep = name.notna() & pd.Series(kind, index=t.index).notna()
        geom = shapely.from_wkb(t.loc[keep, "geometry"].values)
        pts = shapely.get_coordinates(shapely.point_on_surface(geom)) if len(geom) else np.zeros((0, 2))
        area = np.where(shapely.get_type_id(geom) >= 3, shapely.area(geom), 0.0) if len(geom) else []
        frames.append(pd.DataFrame({
            "name": name[keep].to_numpy(), "group": np.asarray(kind, dtype=object)[keep.to_numpy()],
            "lon": pts[:, 0], "lat": pts[:, 1], "area": area,
        }))
    if not frames:
        return pd.DataFrame(columns=["name", "group", "lon", "lat", "area"])
    df = pd.concat(frames, ignore_index=True)
    df = df[df["name"].str.len() >= 3]
    # a bridge is mapped once per carriageway and a park once per polygon
    # ring: keep the largest outline under each name
    df = (df.sort_values("area", ascending=False)
            .drop_duplicates(["name", "group"]).reset_index(drop=True))
    log.info("base features: %d named (%s)", len(df),
             ", ".join(f"{g} {n}" for g, n in df["group"].value_counts().head(8).items()))
    return df


def build_places() -> dict:
    """Compact place list: names, display kind, coordinates."""
    import pyarrow.parquet as pq
    base = build_base()
    t = pq.read_table(PLACES_PARQUET).to_pandas()
    names = t["names"].map(lambda n: (n or {}).get("primary") if isinstance(n, dict) else None)
    cats = t["categories"].map(lambda c: (c or {}).get("primary") if isinstance(c, dict) else None)
    conf = pd.to_numeric(t["confidence"], errors="coerce").fillna(0)
    import shapely
    geom = shapely.from_wkb(t["geometry"].values)
    lon = np.array([g.x for g in geom]); lat = np.array([g.y for g in geom])

    group = cats.map(_group_of)
    support = _support(names.fillna(""), lon, lat, names.fillna(""), lon, lat)
    keep = names.notna() & (names.str.len() >= 3) & group.notna() & (conf >= 0.6)
    # a famous place with no useful category still deserves a slot, and so
    # does anything the records around it keep mentioning (the Ferry
    # Building is filed under farming services, after its market)
    keep |= names.notna() & cats.isna() & (conf >= 0.9)
    keep |= names.notna() & (support >= 5) & (conf >= 0.6)
    df = pd.DataFrame({"name": names, "group": group.fillna("landmark"),
                       "conf": conf, "lon": lon, "lat": lat, "support": support})[keep]
    df = df[_in_city(df["lon"], df["lat"])]
    df = _dedupe_branches(df.sort_values(["support", "conf"], ascending=False)
                            .reset_index(drop=True))
    # the mapped feature wins over any POI record of the same name, or of a
    # trailing part of it ('Dolores Park' for 'Mission Dolores Park')
    mapped = set(_core(n) for n in base["name"])
    tails = set()
    for n in mapped:
        words = n.split()
        for k in range(1, len(words)):
            tail = " ".join(words[k:])
            if len(tail) >= 8:
                tails.add(tail)
    def superseded(n: str) -> bool:
        c = _core(n)
        head = re.split(r"\s*[,\-/(]\s*", c, maxsplit=1)[0]   # 'Ferry Building, Embarcadero'
        return c in mapped or c in tails or head in mapped or head in tails
    dup = df["name"].map(superseded)
    log.info("places: %d records superseded by mapped features", int(dup.sum()))
    df = _prune_variants(df[~dup].reset_index(drop=True))
    df = pd.concat([base[["name", "group", "lon", "lat"]], df[["name", "group", "lon", "lat"]]],
                   ignore_index=True)
    df = df[_in_city(df["lon"], df["lat"])]
    df = df.sort_values(["name", "lat", "lon"]).reset_index(drop=True)
    hoods, hood = _neighborhood_of(df["lon"].to_numpy(), df["lat"].to_numpy())
    streets, street = _street_of(df["lon"].to_numpy(), df["lat"].to_numpy(),
                                 df["name"].duplicated(keep=False).to_numpy())
    log.info("places: %d kept of %d POI records plus %d mapped features (%s)",
             len(df) - len(base), len(t), len(base),
             ", ".join(f"{g} {n}" for g, n in df["group"].value_counts().head(12).items()))
    groups = sorted(set(df["group"]))
    return {
        "names": df["name"].tolist(),
        "group": [groups.index(g) for g in df["group"]],
        "groups": groups,
        "lon": np.round(df["lon"].to_numpy(), 5).tolist(),
        "lat": np.round(df["lat"].to_numpy(), 5).tolist(),
        # neighborhood per place, as an index into ``hoods`` plus one
        "hoods": hoods,
        "hood": hood.tolist(),
        # and, where a name recurs, the street the place is on
        "streets": streets,
        "street": street.tolist(),
    }


def build_addresses() -> dict:
    """One point per (street, number), with a street table."""
    import pyarrow.parquet as pq
    import shapely
    t = pq.read_table(ADDRESSES_PARQUET, columns=["number", "street", "geometry"]).to_pandas()
    t = t[t["street"].notna() & t["number"].notna()]
    num = pd.to_numeric(t["number"].astype(str).str.extract(r"^(\d+)")[0], errors="coerce")
    ok = num.notna() & (num < 65536)
    t = t[ok].copy(); t["num"] = num[ok].astype(int)
    geom = shapely.from_wkb(t["geometry"].values)
    t["lon"] = [g.x for g in geom]; t["lat"] = [g.y for g in geom]
    t = t[_in_city(t["lon"], t["lat"])].copy()
    t["street_t"] = t["street"].map(_title_street)
    t = (t.sort_values(["street_t", "num"])
           .drop_duplicates(["street_t", "num"]).reset_index(drop=True))
    streets = sorted(t["street_t"].unique())
    sidx = {s: i for i, s in enumerate(streets)}
    log.info("addresses: %d unique street numbers on %d streets", len(t), len(streets))
    lon0, lat0 = CITY_BBOX[0], CITY_BBOX[2]
    return {
        "streets": streets,
        "street": t["street_t"].map(sidx).to_numpy().astype("<u2"),
        "number": t["num"].to_numpy().astype("<u2"),
        # 1e-5 degree offsets from the bbox corner fit in uint16 (~1 m)
        "lon": np.clip(np.round((t["lon"].to_numpy() - lon0) / 1e-5), 0, 65535).astype("<u2"),
        "lat": np.clip(np.round((t["lat"].to_numpy() - lat0) / 1e-5), 0, 65535).astype("<u2"),
        "origin": [lon0, lat0],
    }


def build_hillshade(width_px: int = 2200) -> dict:
    """Quiet shaded relief in WGS84, as a palette PNG data URI with bounds.

    Masked to the city limits: the lidar tiles are only fetched where the
    city touches them, so the corners of the study box are empty, and relief
    that stops at the edge of the routable network says where the page works.
    """
    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling
    from rasterio.features import rasterize
    from rasterio.warp import calculate_default_transform, reproject

    from .config import CRS_GEOGRAPHIC, CRS_PROJECTED
    from .elevation import DEM_MOSAIC
    from .viz_static import hillshade

    with step("rendering the hillshade base", log):
        with rasterio.open(DEM_MOSAIC) as src:
            transform, w, h = calculate_default_transform(
                src.crs, "EPSG:4326", src.width, src.height, *src.bounds)
            scale = width_px / w
            w2, h2 = int(w * scale), int(h * scale)
            transform = transform * transform.scale(w / w2, h / h2)
            dem = np.full((h2, w2), np.nan, dtype="float32")
            reproject(rasterio.band(src, 1), dem, dst_transform=transform,
                      dst_crs="EPSG:4326", dst_nodata=np.nan,
                      resampling=Resampling.average)
            nod = src.nodata
        dem = np.where(np.isfinite(dem) & (dem != nod) & (dem > -50), dem, np.nan)
        valid = np.isfinite(dem)
        try:
            import geopandas as gpd

            from .neighborhoods import city_boundary
            city = gpd.GeoSeries([city_boundary(buffer_m=250.0)], crs=CRS_PROJECTED)
            inside = rasterize([city.to_crs(CRS_GEOGRAPHIC).iloc[0]], out_shape=(h2, w2),
                               transform=transform, fill=0, default_value=1,
                               dtype="uint8").astype(bool)
        except Exception as exc:
            log.warning("city boundary unavailable (%s); hillshade left unmasked", exc)
            inside = np.ones((h2, w2), dtype=bool)
        filled = np.where(valid, dem, np.nanmedian(dem))
        valid &= inside
        px_m = abs(transform.a) * LON_M_PER_DEG
        hs = hillshade(filled, res=px_m, z_factor=1.8)
        shade = (0.72 + 0.28 * hs)[..., None]
        # tint by height above the city's own low ground: Atlanta sits on a
        # plateau, so an absolute scale would paint all of it one shade
        lo, hi = np.nanpercentile(dem[valid], [1.0, 99.5]) if valid.any() else (0.0, 1.0)
        # (and at 0.6 of full strength, because most of a plateau is high:
        # the page should stay paper-coloured, with the ridges a shade darker)
        tint = 0.6 * np.clip((filled - lo) / max(hi - lo, 1.0), 0, 1)[..., None]
        base = np.array([243, 242, 238], float); dark = np.array([196, 194, 186], float)
        rgb = (base * (1 - tint * 0.45) + dark * (tint * 0.45)) * shade
        img = np.zeros((h2, w2, 4), np.uint8)
        img[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        img[..., 3] = np.where(valid, 255, 0)
        im = Image.fromarray(img, "RGBA").quantize(colors=96, method=Image.Quantize.FASTOCTREE)
        buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
        im.save(HILLSHADE_PNG)
        west, north = transform.c, transform.f
        east, south = west + transform.a * w2, north + transform.e * h2
        log.info("  hillshade %dx%d, %.0f KB", w2, h2, len(buf.getvalue()) / 1024)
    return {"bounds": [[south, west], [north, east]], "png": buf.getvalue(),
            "data_uri": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}
