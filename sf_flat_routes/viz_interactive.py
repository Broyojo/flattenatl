"""The two web maps: the analysis explorer and the simple route page.

Both are single self-contained HTML files with Leaflet vendored and every
byte of data embedded, so they can be moved around and opened directly, and
both route in the browser over the packed graph (``webgraph.py``) with the
same cost model Python uses.

* **Explorer** (``outputs/sf_flat_routes_map.html``): every analysis layer
  (gradient-coloured network, corridors, passes, barriers, basins, bike
  facilities), the four objectives with live weight sliders, Pareto readout
  and the cost-warped city. Dense by design; this is the working view.
* **Route page** (``outputs/sf_flat_route_finder.html`` and the artifact
  variant): one card with origin, destination and a shortest-to-flattest
  slider over a quiet hillshade. Place search is offline (intersections from
  the graph, Overture places and addresses packed into the page). This is
  the one to share.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .config import OUTPUT_DIR
from .utils import get_logger, human_bytes, step

log = get_logger("sf_flat_routes.viz_interactive")

INTERACTIVE_HTML = OUTPUT_DIR / "sf_flat_routes_map.html"
#: The simple route page, and its variant for publishing as a Claude
#: artifact: no document wrapper (the host supplies it) and no basemap
#: tiles (the artifact sandbox blocks image loads from other hosts; the
#: hillshade is an embedded data URI for exactly that reason).
SIMPLE_HTML = OUTPUT_DIR / "sf_flat_route_finder.html"
ARTIFACT_HTML = OUTPUT_DIR / "sf_flat_routes_artifact.html"
#: Where the artifact variant is published; baked in so its "Copy link"
#: button can produce a link that opens the trip (the page cannot learn its
#: own public address from inside the host's sandbox).
ARTIFACT_URL = "https://claude.ai/artifact/DbDYAJPypSf7srG1yJ3bNC"
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


def _strip_wrapper(html: str) -> str:
    """Artifact variant: the host supplies <html>/<head>/<body> and metas."""
    import re
    head = re.search(r"<head>(.*?)</head>", html, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
    head = re.sub(r"<meta[^>]*>\s*", "", head)
    return head.strip() + "\n" + body.strip() + "\n"


def _render(payload: dict, artifact: bool) -> str:
    """The explorer page."""
    html = _asset("index.html")
    html = html.replace("/*__LEAFLET_CSS__*/", _vendor("leaflet-1.9.4.css"))
    html = html.replace("/*__APP_CSS__*/", _asset("app.css"))
    html = html.replace("/*__LEAFLET_JS__*/", _vendor("leaflet-1.9.4.min.js"))
    html = html.replace("/*__APP_JS__*/", _asset("engine.js") + "\n" + _asset("app.js")
                        + "\n" + _asset("warp.js"))
    if artifact:
        payload = dict(payload, basemap=False)
    html = html.replace("/*__DATA__*/", json.dumps(payload, separators=(",", ":")))
    return _strip_wrapper(html) if artifact else html


def _render_simple(payload: dict, artifact: bool) -> str:
    """The route page."""
    html = _asset("simple.html")
    html = html.replace("/*__LEAFLET_CSS__*/", _vendor("leaflet-1.9.4.css"))
    html = html.replace("/*__APP_CSS__*/", _asset("simple.css"))
    html = html.replace("/*__LEAFLET_JS__*/", _vendor("leaflet-1.9.4.min.js"))
    html = html.replace("/*__APP_JS__*/", _asset("engine.js") + "\n" + _asset("simple.js"))
    if artifact:
        payload = dict(payload, share_base=ARTIFACT_URL)
    html = html.replace("/*__DATA__*/", json.dumps(payload, separators=(",", ":")))
    return _strip_wrapper(html) if artifact else html


#: Where the route page opens before anyone types: a walk whose shortest
#: path climbs over the northern hills and whose flattest path does not.
#: Resolved against the place index at build time; the neighborhood access
#: points are the fallback.
_DEFAULT_TRIP = (
    ("Dolores Park", ("Mission Dolores Park", "Dolores Park"), "Mission"),
    ("Marina Green", ("Marina Green", "Fort Mason"), "Marina"),
)


def _default_trip(places: dict | None, points: dict) -> list[dict]:
    out = []
    for label, names, nb in _DEFAULT_TRIP:
        hit = None
        if places:
            for want in names:
                for i, n in enumerate(places["names"]):
                    if n == want:
                        hit = {"label": label, "lon": places["lon"][i], "lat": places["lat"][i]}
                        break
                if hit:
                    break
        if hit is None and nb in points.get("walk", {}):
            lon, lat = points["walk"][nb]
            hit = {"label": nb, "lon": lon, "lat": lat}
        if hit is None:
            return []
        out.append(hit)
    return out


def _labels(ctx) -> list[dict]:
    """Sparse neighborhood labels for the route page's base map."""
    nb = ctx.neighborhoods.to_crs("EPSG:4326")
    out = []
    for _, r in nb.iterrows():
        p = r.geometry.representative_point()
        out.append({"n": r["neighborhood"], "lon": round(p.x, 5), "lat": round(p.y, 5)})
    return out


def _write_route_page(ctx, graph: dict, pts: dict) -> Path:
    """Pack the place index and hillshade with the graph; write both variants."""
    from .download import ADDRESSES_PARQUET, PLACES_PARQUET
    from .places import build_addresses, build_hillshade, build_places
    from .webgraph import bundle

    strings = {"geom": graph["geom"]}
    arrays = dict(graph["arrays"])
    places = None
    if PLACES_PARQUET.exists():
        places = build_places()
        strings["places"] = json.dumps(places, separators=(",", ":"))
    else:
        log.warning("no places parquet; the route page will search intersections only")
    if ADDRESSES_PARQUET.exists():
        addr = build_addresses()
        strings["addr_streets"] = json.dumps(addr["streets"], separators=(",", ":"))
        for k in ("street", "number", "lon", "lat"):
            arrays["addr_" + k] = addr[k]
        addr_meta = {"origin": addr["origin"], "step": 1e-5}
    else:
        addr_meta = None
    try:
        hillshade = build_hillshade()
    except Exception as exc:  # the page works without it, on streets alone
        log.warning("hillshade unavailable (%s)", exc)
        hillshade = None

    with step("bundling the route page payload", log):
        packed = bundle(dict(graph, arrays=arrays), strings)
    payload = {
        "manifest": packed["manifest"], "bundle": packed["b64"],
        "meta": packed["meta"], "addr": addr_meta, "hillshade": hillshade,
        "labels": _labels(ctx), "default": _default_trip(places, pts),
    }
    SIMPLE_HTML.write_text(_render_simple(payload, artifact=False), encoding="utf-8")
    ARTIFACT_HTML.write_text(_render_simple(payload, artifact=True), encoding="utf-8")
    log.info("wrote %s (%s) and %s (%s)", SIMPLE_HTML.name,
             human_bytes(SIMPLE_HTML.stat().st_size), ARTIFACT_HTML.name,
             human_bytes(ARTIFACT_HTML.stat().st_size))
    return SIMPLE_HTML


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

    with step("writing the explorer HTML", log):
        INTERACTIVE_HTML.parent.mkdir(parents=True, exist_ok=True)
        INTERACTIVE_HTML.write_text(_render(payload, artifact=False), encoding="utf-8")
    log.info("wrote %s (%s)", INTERACTIVE_HTML.name,
             human_bytes(INTERACTIVE_HTML.stat().st_size))
    with step("writing the route page", log):
        _write_route_page(ctx, graph, pts)
    return INTERACTIVE_HTML
