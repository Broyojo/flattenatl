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
import pandas as pd

from .config import GRADE_THRESHOLDS, OUTPUT_DIR
from .utils import get_logger, human_bytes, progress, step

log = get_logger("sf_flat_routes.viz_interactive")

INTERACTIVE_HTML = OUTPUT_DIR / "sf_flat_routes_map.html"

_TH = [int(t * 100) for t in GRADE_THRESHOLDS]

#: Gradient buckets used to colour the street network.
GRADE_BUCKETS = [
    (0.00, 0.03, "#2c7bb6", "under 3% - flat"),
    (0.03, 0.05, "#7fcdbb", "3-5% - gentle"),
    (0.05, 0.08, "#fed976", "5-8% - noticeable"),
    (0.08, 0.10, "#fd8d3c", "8-10% - hard"),
    (0.10, 0.15, "#e3492e", "10-15% - very hard"),
    (0.15, 1.01, "#7f1d1d", "over 15% - extreme"),
]


# --------------------------------------------------------------------------
# geometry encoding
# --------------------------------------------------------------------------
def encode_polyline(coords, precision: int = 5) -> str:
    """Google encoded-polyline format for a sequence of (lon, lat)."""
    factor = 10 ** precision
    out = []
    prev_lat = prev_lon = 0

    def enc(v: int) -> str:
        v = ~(v << 1) if v < 0 else (v << 1)
        chunks = []
        while v >= 0x20:
            chunks.append(chr((0x20 | (v & 0x1f)) + 63))
            v >>= 5
        chunks.append(chr(v + 63))
        return "".join(chunks)

    for lon, lat in coords:
        ilat = int(round(lat * factor))
        ilon = int(round(lon * factor))
        out.append(enc(ilat - prev_lat))
        out.append(enc(ilon - prev_lon))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


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


def build_grade_layer(edges, mode: str = "walk") -> dict:
    """Street network dissolved by street name and gradient bucket.

    87,000 individual edges is more than a browser wants to hold as GeoJSON,
    and most of them are fragments of the same street with the same
    gradient.  Dissolving by (name, bucket) cuts the feature count by roughly
    an order of magnitude while keeping popups meaningful -- a click still
    reports a named street and its gradient class.
    """
    from shapely.ops import linemerge

    net = edges[edges[f"{mode}_ok"]].copy()
    net["g"] = net["max_abs_grade"].fillna(0.0)
    net["bucket"] = np.digitize(net["g"], [b[1] for b in GRADE_BUCKETS[:-1]])
    net["gname"] = net["name"].where(net["name"].notna(), "(unnamed)")

    rows = []
    for (nm, bk), grp in net.groupby(["gname", "bucket"], sort=False):
        try:
            geom = linemerge(list(grp.geometry.values))
        except Exception:
            continue
        lo, hi, colour, label = GRADE_BUCKETS[int(bk)]
        w = grp["length_m"].to_numpy(dtype="float64")
        rows.append({
            "name": nm, "bucket": int(bk), "colour": colour,
            "grade_class": label,
            "length_m": float(w.sum()),
            "max_grade": float(grp["g"].max()),
            "avg_grade": float((grp["avg_grade_fwd"].abs() * w).sum() / w.sum()),
            "gain_per_km": float(grp["cum_gain_fwd"].sum()
                                 / max(w.sum() / 1000.0, 1e-9)),
            "cls": grp["cls"].mode().iloc[0] if len(grp) else "",
            "geometry": geom,
        })
    import geopandas as gpd
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=edges.crs)
    log.info("grade layer: %d edges dissolved to %d features",
             len(net), len(out))
    return _geojson(out, ["name", "bucket", "colour", "grade_class", "length_m",
                          "max_grade", "avg_grade", "gain_per_km", "cls"],
                    simplify=5.0)


def build_route_store(graphs: dict, arc_store: dict, pairs_df: pd.DataFrame,
                      edges, profiles: dict, simplify_m: float = 13.0,
                      profile_points: int = 56) -> dict:
    """Precomputed routes keyed ``mode|profile|origin|destination``."""
    from .routing import route_geometry, route_profile

    store: dict[str, dict] = {}
    lookup = pairs_df.set_index(["mode", "profile", "origin", "destination"])

    for mode, graph in graphs.items():
        items = arc_store[mode]
        with step(f"packing {len(items)} precomputed routes [{mode}]", log):
            for (pname, o, d), arcs in progress(items.items(), total=len(items),
                                                desc=f"  {mode}", unit="route"):
                if not arcs:
                    continue
                geom = route_geometry(graph, arcs, edges)
                if geom is None:
                    continue
                geom = geom.simplify(simplify_m, preserve_topology=False)
                import geopandas as gpd
                ll = gpd.GeoSeries([geom], crs=edges.crs).to_crs("EPSG:4326")[0]
                coords = list(ll.coords)

                dist, elev = route_profile(graph, arcs, profiles)
                if dist.size > profile_points:
                    idx = np.linspace(0, dist.size - 1, profile_points).astype(int)
                    dist, elev = dist[idx], elev[idx]
                try:
                    row = lookup.loc[(mode, pname, o, d)]
                except KeyError:
                    continue

                rec = {
                    "p": encode_polyline(coords),
                    "d": round(float(row["distance_m"]), 1),
                    "g": round(float(row["elev_gain_m"]), 1),
                    "l": round(float(row["elev_loss_m"]), 1),
                    "mx": round(float(row["max_grade"]), 4),
                    "ag": round(float(row["avg_abs_grade"]), 4),
                    "sd": round(float(row.get("shortest_distance_m", np.nan) or 0), 1),
                    "sg": round(float(row.get("shortest_gain_m", np.nan) or 0), 1),
                    "th": [int(round(float(row[f"d_above_{t}"]))) for t in _TH],
                    # the profile's x-axis is regenerated in the browser as
                    # evenly spaced stations over the route length, so only
                    # the elevations need storing
                    "pe": [round(float(x), 1) for x in elev],
                }
                store[f"{mode}|{pname}|{o}|{d}"] = rec
    log.info("route store: %d routes", len(store))
    return store


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def make_interactive_map(ctx, corridors, passes, barriers, pairs_df,
                         arc_store, basins=None) -> Path:
    """Assemble and write the interactive map."""
    edges = ctx.edges
    neighborhoods = ctx.neighborhoods

    with step("preparing interactive map layers", log):
        layers = {
            "neighborhoods": _geojson(neighborhoods, ["neighborhood", "area_km2"],
                                      simplify=12.0),
            "grades_walk": build_grade_layer(edges, "walk"),
            "corridors": _geojson(
                corridors.to_crs(edges.crs) if corridors.crs != edges.crs
                else corridors,
                ["corridor_id", "corridor_name", "street_names", "mode",
                 "length_km", "mean_abs_grade", "max_grade", "gain_per_km",
                 "pair_count_max", "neighborhood_span", "climb_saved_m",
                 "elev_min_m", "elev_max_m", "neighborhoods",
                 "low_stress_share", "total_score"], simplify=5.0),
            "passes": _geojson(
                passes.to_crs(edges.crs) if passes.crs != edges.crs else passes,
                ["edge_id", "name", "neighborhood", "pass_elev_m",
                 "pass_elev_ft", "pairs_served", "max_abs_grade",
                 "neighborhoods_separated"], simplify=2.0),
            "barriers": _geojson(
                (barriers.to_crs(edges.crs) if barriers.crs != edges.crs
                 else barriers).head(350),
                ["edge_id", "name", "neighborhood", "max_abs_grade",
                 "length_m", "shortest_use", "flat_use_per_objective",
                 "unavoidability", "barrier_score"],
                simplify=2.0),
        }
        bike = edges[edges["bike_facility"].astype(bool)
                     & (edges["bike_facility"] != "")]
        layers["bike_network"] = _geojson(
            bike, ["name", "cls", "bike_facility", "length_m", "max_abs_grade"],
            simplify=5.0)
        carfree = edges[edges["cls"].isin(["pedestrian", "living_street"])
                        | (edges["bike_facility"] == "car_free_street")]
        layers["low_stress"] = _geojson(
            carfree, ["name", "cls", "bike_facility", "length_m"], simplify=5.0)
        if basins is not None and len(basins):
            b = basins.to_crs(edges.crs) if basins.crs != edges.crs else basins
            # one feature per basin rather than per edge: the per-edge version
            # is ~8 MB of geometry for a layer nobody inspects street by street
            agg = b.dissolve(by="basin", aggfunc={"basin_label": "first",
                                                  "length_m": "sum"})
            agg = agg.reset_index()
            agg["length_km"] = agg["length_m"] / 1000.0
            layers["basins"] = _geojson(agg, ["basin", "basin_label",
                                              "length_km"], simplify=14.0)

    routes = build_route_store(ctx.graphs, arc_store, pairs_df, edges,
                               ctx.profiles)

    pts = {}
    for mode, gdf in ctx.points.items():
        g = gdf.to_crs("EPSG:4326")
        pts[mode] = {r["neighborhood"]: [round(r.geometry.x, 6),
                                         round(r.geometry.y, 6)]
                     for _, r in g.iterrows()}

    names = sorted(set(pts.get("walk", {})) | set(pts.get("bike", {})))
    payload = {
        "layers": layers, "routes": routes, "points": pts,
        "neighborhood_names": names,
        "thresholds": _TH,
        "buckets": [{"lo": b[0], "hi": b[1], "colour": b[2], "label": b[3]}
                    for b in GRADE_BUCKETS],
    }

    with step("writing the interactive HTML", log):
        INTERACTIVE_HTML.parent.mkdir(parents=True, exist_ok=True)
        INTERACTIVE_HTML.write_text(_render_html(payload), encoding="utf-8")
    log.info("wrote %s (%s)", INTERACTIVE_HTML.name,
             human_bytes(INTERACTIVE_HTML.stat().st_size))
    return INTERACTIVE_HTML


VENDOR_DIR = Path(__file__).resolve().parent / "vendor"


def _vendor(name: str) -> str:
    """Read a vendored asset for inlining (see ``vendor/README.md``)."""
    return (VENDOR_DIR / name).read_text(encoding="utf-8")


_HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>San Francisco Flat Routes</title>
<style>/*__LEAFLET_CSS__*/</style>
<style>
  :root{
    --bg:#10151b; --panel:#181f27; --panel2:#212a34; --line:#313d49;
    --text:#e9eef3; --muted:#93a1af; --accent:#4cc3d9; --ok:#7fcdbb;
    --warn:#e3492e;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
  #app{display:flex;height:100%}
  #side{width:390px;min-width:390px;overflow-y:auto;background:var(--panel);border-right:1px solid var(--line)}
  #map{flex:1;background:#0d1319}
  h1{font-size:17px;margin:0 0 4px}
  h2{font-size:11.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin:22px 0 8px;font-weight:600}
  .pad{padding:16px 18px 26px}
  .sub{color:var(--muted);font-size:12px;margin:0 0 2px}
  label{display:block;font-size:11px;color:var(--muted);margin:11px 0 4px;text-transform:uppercase;letter-spacing:.05em}
  select,button{width:100%;padding:8px 10px;background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:6px;font-size:13px;font-family:inherit}
  select:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  .seg{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:4px}
  .seg button{cursor:pointer}
  .seg button[aria-pressed="true"]{background:var(--accent);color:#07222b;border-color:var(--accent);font-weight:650}
  .swap{margin-top:8px;cursor:pointer}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  td{padding:4px 0;border-bottom:1px solid #27313b}
  td.k{color:var(--muted)} td.v{text-align:right;font-variant-numeric:tabular-nums;font-weight:650}
  .big{font-size:21px;font-weight:700;font-variant-numeric:tabular-nums;margin-bottom:8px}
  .cmp{display:flex;gap:8px;margin-top:10px}
  .card{flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 10px}
  .card .t{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
  .card .n{font-variant-numeric:tabular-nums;font-weight:650}
  .good{color:var(--ok)} .bad{color:var(--warn)}
  .layers label{display:flex;align-items:center;gap:8px;text-transform:none;letter-spacing:0;font-size:13px;color:var(--text);margin:7px 0;cursor:pointer}
  .layers input{width:auto;margin:0;accent-color:var(--accent)}
  .key{display:flex;align-items:center;gap:7px;font-size:11.5px;color:var(--muted);margin:3px 0}
  .key i{width:22px;height:3px;border-radius:2px;display:inline-block;flex:none}
  #prof{width:100%;height:132px;display:block;background:var(--panel2);border:1px solid var(--line);border-radius:8px;margin-top:10px}
  .note{font-size:11px;color:var(--muted);margin-top:7px;line-height:1.45}
  .leaflet-container{background:#0d1319;font-family:inherit}
  .leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#181f27;color:#e9eef3;border:1px solid #313d49}
  .leaflet-popup-content{font-size:12.5px;margin:10px 12px;max-width:280px}
  .leaflet-popup-content b{color:#4cc3d9}
  .leaflet-bar a{background:#212a34;color:#e9eef3;border-bottom-color:#313d49}
  .leaflet-bar a:hover{background:#2c3742}
  .leaflet-control-attribution{background:rgba(24,31,39,.86)!important;color:#93a1af!important}
  .leaflet-control-attribution a{color:#93a1af!important}
  .legend{background:rgba(24,31,39,.94);border:1px solid var(--line);border-radius:8px;padding:9px 11px;color:var(--text)}
  @media (max-width:880px){#app{flex-direction:column}#side{width:100%;min-width:0;max-height:54%}#map{min-height:46%}}
</style>
</head>
<body>
<div id="app">
  <div id="side">
    <div class="pad">
      <h1>San Francisco flat routes</h1>
      <p class="sub">The city's low-elevation street network, modelled from
      USGS 3DEP 1&nbsp;m lidar and the Overture&nbsp;/&nbsp;OpenStreetMap
      street graph.</p>

      <h2>Plan a route</h2>
      <label>Travel mode</label>
      <div class="seg" id="mode">
        <button data-v="walk" aria-pressed="true">Walking</button>
        <button data-v="bike" aria-pressed="false">Bicycle</button>
      </div>
      <label for="o">Origin</label>
      <select id="o"></select>
      <label for="d">Destination</label>
      <select id="d"></select>
      <button class="swap" id="swap">Swap origin and destination</button>
      <label>Preference</label>
      <div class="seg" id="pref">
        <button data-v="shortest" aria-pressed="false">Shortest</button>
        <button data-v="balanced" aria-pressed="true">Balanced</button>
        <button data-v="min_climb" aria-pressed="false">Flattest</button>
        <button data-v="grade_averse" aria-pressed="false">Avoid steep</button>
      </div>

      <h2>Result</h2>
      <div id="res"></div>
      <canvas id="prof" width="720" height="264"></canvas>
      <div class="note" id="profnote"></div>

      <h2>Layers</h2>
      <div class="layers" id="layers"></div>

      <h2>Street gradient key</h2>
      <div id="key"></div>

      <p class="note">Routes are precomputed between each neighborhood's
      representative access point, snapped to a real street intersection.
      Climb figures are <em>cumulative</em> elevation gain, not the net
      difference between the endpoints. Leaflet is embedded in this file, so
      everything works offline; only the optional basemap tiles need a
      network connection.</p>
    </div>
  </div>
  <div id="map"></div>
</div>
<script>/*__LEAFLET_JS__*/</script>
<script>
const DATA = /*__DATA__*/;
"""

_HTML_TAIL = r"""
/* ---------------- polyline decoding ---------------- */
function decodePolyline(str, precision){
  const factor = Math.pow(10, precision || 5);
  let index = 0, lat = 0, lon = 0; const out = [];
  while(index < str.length){
    let b, shift = 0, result = 0;
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += ((result & 1) ? ~(result >> 1) : (result >> 1));
    shift = 0; result = 0;
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lon += ((result & 1) ? ~(result >> 1) : (result >> 1));
    out.push([lat / factor, lon / factor]);      // Leaflet wants [lat, lon]
  }
  return out;
}

/* ---------------- map ---------------- */
const map = L.map("map", {preferCanvas:true, zoomControl:true,
                          center:[37.762,-122.437], zoom:12, minZoom:10,
                          maxZoom:18});
L.control.scale({imperial:true, metric:true}).addTo(map);
// Basemap is optional: if the tiles cannot be reached the analysis layers
// still render on the dark background.
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png", {
  subdomains:"abc", maxZoom:19, opacity:0.55, crossOrigin:true,
  attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, &copy; <a href="https://carto.com/attributions">CARTO</a> &middot; elevation USGS 3DEP &middot; streets Overture Maps'
}).addTo(map);

/* ---------------- popup formatting ---------------- */
function fmt(v, how){
  if(v === null || v === undefined || v === "") return "—";
  switch(how){
    case "pct": return (v*100).toFixed(1) + "%";
    case "m0": return Math.round(v).toLocaleString() + " m";
    case "m1": return (+v).toFixed(1) + " m";
    case "km": return (+v).toFixed(2) + " km";
    case "ft": return Math.round(v).toLocaleString() + " ft";
    case "int": return (+v).toLocaleString();
    case "km2": return (+v).toFixed(2) + " km²";
    default: return String(v);
  }
}
const POPUP_FIELDS = {
  grades:[["name","Street"],["grade_class","Gradient class"],
          ["max_grade","Max gradient","pct"],["avg_grade","Mean gradient","pct"],
          ["gain_per_km","Climb per km","m1"],["length_m","Length","m0"],
          ["cls","Road class"]],
  corridors:[["corridor_name","Corridor"],["mode","Mode"],["length_km","Length","km"],
             ["mean_abs_grade","Mean gradient","pct"],["max_grade","Max gradient","pct"],
             ["gain_per_km","Climb per km","m1"],
             ["pair_count_max","Neighborhood pairs served","int"],
             ["neighborhood_span","Neighborhoods spanned","int"],
             ["climb_saved_m","Climbing avoided (total)","m0"],
             ["elev_min_m","Lowest point","m0"],["elev_max_m","Highest point","m0"],
             ["neighborhoods","Passes through"],["street_names","Streets"]],
  passes:[["name","Street"],["neighborhood","Neighborhood"],
          ["pass_elev_ft","Lowest possible crossing","ft"],
          ["pairs_served","Neighborhood pairs forced over it","int"],
          ["max_abs_grade","Max gradient","pct"],
          ["neighborhoods_separated","Separates"]],
  barriers:[["name","Street"],["neighborhood","Neighborhood"],
            ["max_abs_grade","Max gradient","pct"],["length_m","Length","m0"],
            ["shortest_use","Neighborhood pairs via shortest route","int"],
            ["flat_use_per_objective","... still via the flat route","int"],
            ["unavoidability","Unavoidable share","pct"]],
  bike_network:[["name","Street"],["bike_facility","Facility"],["cls","Road class"],
                ["max_abs_grade","Max gradient","pct"],["length_m","Length","m0"]],
  low_stress:[["name","Street"],["cls","Class"],["bike_facility","Facility"],
              ["length_m","Length","m0"]],
  neighborhoods:[["neighborhood","Neighborhood"],["area_km2","Area","km2"]],
  basins:[["basin_label","Lowland basin"],["length_km","Street below 15 m","km"]],
};
function popupHTML(kind, props){
  const spec = POPUP_FIELDS[kind] || [];
  let h = "";
  for(const [k, lab, how] of spec){
    if(!(k in props) || props[k] === null || props[k] === "") continue;
    h += `<div><b>${lab}:</b> ${fmt(props[k], how)}</div>`;
  }
  return h || "<div>(no attributes)</div>";
}

/* ---------------- analysis layers ---------------- */
const overlays = {};
function lineLayer(key, kind, styleFn, on){
  const gj = DATA.layers[key];
  if(!gj) return null;
  const lyr = L.geoJSON(gj, {
    style: styleFn,
    onEachFeature: (f, l) => l.bindPopup(popupHTML(kind, f.properties)),
  });
  overlays[kind] = lyr;
  if(on) lyr.addTo(map);
  return lyr;
}

const LAYER_DEFS = [
  {key:"neighborhoods", kind:"neighborhoods", label:"Neighborhood boundaries", on:true,
   style:()=>({color:"#8492a0", weight:1.1, opacity:0.8, fill:false, dashArray:"4,3"})},
  {key:"basins", kind:"basins", label:"Lowland basins (street below 15 m)", on:false,
   style:()=>({color:"#3f9b6d", weight:1.2, opacity:0.55})},
  {key:"grades_walk", kind:"grades", label:"Streets coloured by gradient", on:true,
   style:f=>({color:f.properties.colour,
              weight: f.properties.bucket >= 3 ? 1.6 : 1.1,
              opacity: f.properties.bucket >= 3 ? 0.88 : 0.78})},
  {key:"bike_network", kind:"bike_network", label:"Bicycle facilities (OSM-derived)", on:false,
   style:()=>({color:"#39d98a", weight:2.0, opacity:0.9})},
  {key:"low_stress", kind:"low_stress", label:"Car-free / living streets", on:false,
   style:()=>({color:"#c792ea", weight:2.6, opacity:0.95})},
  {key:"barriers", kind:"barriers", label:"Steep barriers (unavoidable climbs)", on:false,
   style:()=>({color:"#ff5f4d", weight:3.0, opacity:0.85})},
  {key:"corridors", kind:"corridors", label:"Flat corridors (discovered)", on:true,
   style:f=>({color: f.properties.mode === "bike" ? "#9be7ff" : "#2c7bb6",
              weight: 4.2 + 3.4 * Math.min((f.properties.total_score||0)/900, 1),
              opacity:0.95, lineCap:"round"})},
];

/* route layers sit above everything */
const cmpLine = L.polyline([], {color:"#8a97a4", weight:3, opacity:0.75,
                                dashArray:"6,5"});
// the selected route is white on a dark halo so it cannot be confused with
// the blue corridor layer underneath it
const routeHalo = L.polyline([], {color:"#01080d", weight:10, opacity:0.85});
const routeLine = L.polyline([], {color:"#ffffff", weight:4.4, opacity:1});
const endMarks = L.layerGroup();

/* ---------------- routing UI ---------------- */
const state = {mode:"walk", pref:"balanced", o:null, d:null};
const key = () => `${state.mode}|${state.pref}|${state.o}|${state.d}`;
const keyFor = p => `${state.mode}|${p}|${state.o}|${state.d}`;

function fillSelects(){
  const names = DATA.neighborhood_names;
  const o = document.getElementById("o"), d = document.getElementById("d");
  for(const n of names){ o.appendChild(new Option(n,n)); d.appendChild(new Option(n,n)); }
  state.o = names.includes("Mission") ? "Mission" : names[0];
  state.d = names.includes("Outer Sunset") ? "Outer Sunset" : names[names.length-1];
  o.value = state.o; d.value = state.d;
  o.onchange = () => { state.o = o.value; update(); };
  d.onchange = () => { state.d = d.value; update(); };
  document.getElementById("swap").onclick = () => {
    const t = state.o; state.o = state.d; state.d = t;
    o.value = state.o; d.value = state.d; update();
  };
}
function bindSeg(id, field){
  const box = document.getElementById(id);
  box.querySelectorAll("button").forEach(b => {
    b.onclick = () => {
      box.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed","false"));
      b.setAttribute("aria-pressed","true");
      state[field] = b.dataset.v;
      update();
    };
  });
}

function clearRoute(msg){
  document.getElementById("res").innerHTML = `<p class="sub">${msg}</p>`;
  routeLine.setLatLngs([]); routeHalo.setLatLngs([]); cmpLine.setLatLngs([]);
  endMarks.clearLayers();
  drawProfile(null);
}

function update(){
  if(state.o === state.d){ clearRoute("Choose two different neighborhoods."); return; }
  const r = DATA.routes[key()];
  if(!r){
    clearRoute("No route is available for this pair in this travel mode. "
      + "Some points (the Presidio in particular) have no bicycle-legal "
      + "connection to the rest of the network.");
    return;
  }
  const sh = DATA.routes[keyFor("shortest")];
  const line = decodePolyline(r.p);
  routeHalo.setLatLngs(line); routeLine.setLatLngs(line);
  cmpLine.setLatLngs(sh && state.pref !== "shortest" ? decodePolyline(sh.p) : []);

  endMarks.clearLayers();
  const pts = DATA.points[state.mode] || {};
  const mk = (ll, colour, label) => L.circleMarker([ll[1], ll[0]],
      {radius:7, color:"#04121a", weight:2, fillColor:colour, fillOpacity:1})
      .bindPopup(`<b>${label}</b>`);
  if(pts[state.o]) mk(pts[state.o], "#7fcdbb", "Origin: " + state.o).addTo(endMarks);
  if(pts[state.d]) mk(pts[state.d], "#e3492e", "Destination: " + state.d).addTo(endMarks);

  const mi = r.d / 1609.344, ft = r.g * 3.28084, ftl = r.l * 3.28084;
  let h = `<div class="big">${mi.toFixed(2)} mi &middot; ${Math.round(ft).toLocaleString()} ft climb</div><table>`;
  h += `<tr><td class="k">Distance</td><td class="v">${mi.toFixed(2)} mi / ${(r.d/1000).toFixed(2)} km</td></tr>`;
  h += `<tr><td class="k">Elevation gain</td><td class="v">${Math.round(ft)} ft / ${r.g.toFixed(0)} m</td></tr>`;
  h += `<tr><td class="k">Elevation loss</td><td class="v">${Math.round(ftl)} ft / ${r.l.toFixed(0)} m</td></tr>`;
  h += `<tr><td class="k">Steepest climb</td><td class="v">${(r.mx*100).toFixed(1)}%</td></tr>`;
  h += `<tr><td class="k">Mean gradient</td><td class="v">${(r.ag*100).toFixed(1)}%</td></tr>`;
  DATA.thresholds.forEach((t,i) => {
    h += `<tr><td class="k">Distance climbing over ${t}%</td><td class="v">${Math.round(r.th[i]).toLocaleString()} m</td></tr>`;
  });
  h += `</table>`;
  if(state.pref !== "shortest" && sh && r.sd > 0){
    const dMi = (r.d - r.sd)/1609.344, dFt = (r.sg - r.g)*3.28084;
    h += `<div class="cmp">
      <div class="card"><div class="t">Extra distance</div>
        <div class="n">${dMi >= 0 ? "+" : ""}${dMi.toFixed(2)} mi
        (${(100*(r.d/r.sd - 1)).toFixed(0)}%)</div></div>
      <div class="card"><div class="t">Climbing saved</div>
        <div class="n ${dFt > 0 ? "good" : "bad"}">${dFt >= 0 ? "" : "+"}${Math.abs(Math.round(dFt))} ft</div></div>
    </div>
    <div class="note">Shortest route, shown dashed: ${(r.sd/1609.344).toFixed(2)} mi
      with ${Math.round(r.sg*3.28084)} ft of climbing.</div>`;
  }
  document.getElementById("res").innerHTML = h;
  drawProfile(r);
  map.fitBounds(L.latLngBounds(line).pad(0.08));
}

/* ---------------- elevation profile ---------------- */
function drawProfile(r){
  const cv = document.getElementById("prof");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.clearRect(0,0,W,H);
  const note = document.getElementById("profnote");
  if(!r){ note.textContent = ""; return; }
  const e = r.pe, n = e.length;
  // the x-axis is regenerated as evenly spaced stations over the route length
  const d = Array.from({length:n}, (_,i) => r.d * i/(n-1));
  const emin = Math.min(...e), emax = Math.max(...e);
  const span = Math.max(emax - emin, 8);
  const padL = 46, padR = 16, padT = 16, padB = 30;
  const x = i => padL + (d[i]/d[n-1]) * (W - padL - padR);
  const y = v => padT + (1 - (v - emin)/span) * (H - padT - padB);

  for(let i = 1; i < n; i++){
    const dz = e[i] - e[i-1], dd = Math.max(d[i] - d[i-1], 1e-6);
    const g = Math.abs(dz/dd);
    ctx.fillStyle = g < 0.03 ? "#2c7bb6" : g < 0.05 ? "#7fcdbb" : g < 0.08 ? "#fed976"
                  : g < 0.10 ? "#fd8d3c" : g < 0.15 ? "#e3492e" : "#7f1d1d";
    ctx.globalAlpha = 0.8;
    ctx.beginPath();
    ctx.moveTo(x(i-1), y(e[i-1])); ctx.lineTo(x(i), y(e[i]));
    ctx.lineTo(x(i), H - padB); ctx.lineTo(x(i-1), H - padB);
    ctx.closePath(); ctx.fill();
  }
  ctx.globalAlpha = 1;
  ctx.strokeStyle = "#e9eef3"; ctx.lineWidth = 1.6;
  ctx.beginPath();
  for(let i = 0; i < n; i++){ i ? ctx.lineTo(x(i), y(e[i])) : ctx.moveTo(x(i), y(e[i])); }
  ctx.stroke();
  ctx.strokeStyle = "#39434e"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(padL, H - padB); ctx.lineTo(W - padR, H - padB); ctx.stroke();
  ctx.fillStyle = "#93a1af"; ctx.font = "11px sans-serif"; ctx.textAlign = "right";
  ctx.fillText(Math.round(emax*3.28084) + " ft", padL - 5, padT + 9);
  ctx.fillText(Math.round(emin*3.28084) + " ft", padL - 5, H - padB - 1);
  ctx.fillText((d[n-1]/1609.344).toFixed(2) + " mi", W - padR, H - 9);
  ctx.textAlign = "left"; ctx.fillText("0", padL, H - 9);
  note.textContent = `Elevation ${Math.round(emin*3.28084)}–${Math.round(emax*3.28084)} ft. `
    + `Shading shows the gradient along the route.`;
}

/* ---------------- controls ---------------- */
function buildControls(){
  const box = document.getElementById("layers");
  const rows = LAYER_DEFS.filter(L0 => DATA.layers[L0.key])
                         .map(L0 => ({kind:L0.kind, label:L0.label, on:L0.on}));
  if(overlays.passes) rows.push({kind:"passes", label:"Critical passes / saddles", on:true});
  for(const row of rows){
    const wrap = document.createElement("label");
    const id = "chk_" + row.kind;
    wrap.innerHTML = `<input type="checkbox" id="${id}" ${row.on ? "checked" : ""}> ${row.label}`;
    box.appendChild(wrap);
    wrap.querySelector("input").onchange = ev => {
      const lyr = overlays[row.kind];
      if(!lyr) return;
      ev.target.checked ? lyr.addTo(map) : map.removeLayer(lyr);
      if(ev.target.checked) raiseRoute();
    };
  }
  const k = document.getElementById("key");
  for(const b of DATA.buckets){
    const el = document.createElement("div");
    el.className = "key";
    el.innerHTML = `<i style="background:${b.colour}"></i> ${b.label}`;
    k.appendChild(el);
  }
  const legend = L.control({position:"bottomleft"});
  legend.onAdd = () => {
    const div = L.DomUtil.create("div","legend");
    div.innerHTML =
      `<div class="key"><i style="background:#ffffff;height:4px"></i> selected route</div>
       <div class="key"><i style="background:#8a97a4"></i> shortest route (comparison)</div>
       <div class="key"><i style="background:#2c7bb6;height:5px"></i> flat corridor</div>
       <div class="key"><i style="background:#ff5f4d"></i> steep barrier</div>
       <div class="key"><i style="background:#ffd166;height:9px;width:9px;border-radius:50%"></i> critical pass</div>`;
    return div;
  };
  legend.addTo(map);
}
function raiseRoute(){
  [cmpLine, routeHalo, routeLine].forEach(l => l.bringToFront());
  endMarks.eachLayer(l => l.bringToFront());
}

/* ---------------- boot ---------------- */
for(const L0 of LAYER_DEFS) lineLayer(L0.key, L0.kind, L0.style, L0.on);
if(DATA.layers.passes){
  overlays.passes = L.geoJSON(DATA.layers.passes, {
    onEachFeature: (f,l) => l.bindPopup(popupHTML("passes", f.properties)),
    style: () => ({color:"#4a3b10", weight:1}),
    pointToLayer: (f, ll) => L.circleMarker(ll, {radius:6}),
    filter: () => true,
  });
  // passes are line features: render a marker at each midpoint instead
  overlays.passes = L.layerGroup(
    DATA.layers.passes.features.map(f => {
      const c = f.geometry.coordinates;
      const flat = (Array.isArray(c[0][0]) ? c[0] : c);
      const mid = flat[Math.floor(flat.length/2)];
      return L.circleMarker([mid[1], mid[0]],
        {radius:6, color:"#4a3b10", weight:1.4, fillColor:"#ffd166",
         fillOpacity:0.95}).bindPopup(popupHTML("passes", f.properties));
    })
  ).addTo(map);
}
cmpLine.addTo(map); routeHalo.addTo(map); routeLine.addTo(map); endMarks.addTo(map);
buildControls();
fillSelects();
bindSeg("mode","mode");
bindSeg("pref","pref");
update();
raiseRoute();
</script>
</body>
</html>
"""


def _render_html(payload: dict) -> str:
    """Inline Leaflet, the stylesheet and the analysis payload into one file."""
    head = _HTML_HEAD.replace("/*__LEAFLET_CSS__*/", _vendor("leaflet-1.9.4.css"))
    head = head.replace("/*__LEAFLET_JS__*/", _vendor("leaflet-1.9.4.min.js"))
    head = head.replace("/*__DATA__*/", json.dumps(payload, separators=(",", ":")))
    return head + _HTML_TAIL
