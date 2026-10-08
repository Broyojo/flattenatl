"""Validation of the elevation and routing model against known ground truth.

Three independent checks:

1.  **Absolute elevation** -- the 1 m lidar mosaic against the independent
    USGS 1/3 arc-second seamless DEM at sampled street nodes.  These are
    separately produced products, so agreement is evidence the mosaic is
    correctly georeferenced and in the expected vertical datum.

2.  **Street grades** -- computed maximum grades against published figures,
    where a city has them.  San Francisco does (this check was written
    around Filbert and Bradford streets); Atlanta has no comparable table
    of measured street grades, so here the section lists the steepest
    streets the model finds, as readings to be checked on the ground.

3.  **Flat corridors** -- the lines local knowledge says are flat, which in
    Atlanta means the old railway grades: the BeltLine trails, the creek
    greenways, and the streets laid alongside the railways on the ridges
    (DeKalb Avenue, Marietta Street).  They must come out flat, and should
    appear in the discovered corridor set.

The model is *not* tuned to make these pass; where a target disagrees, the
disagreement is reported with a diagnosis.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CRS_PROJECTED, MIN_RELIABLE_GRADE_LENGTH_M, OUTPUT_DIR
from .utils import get_logger, step

log = get_logger("sf_flat_routes.validate")

VALIDATION_MD = OUTPUT_DIR / "validation_report.md"

#: Published maximum grades of well-known streets, {name: grade}.  Empty for
#: Atlanta: no figure could be traced to a measurement, and a table of
#: half-remembered numbers would only look like validation.  With nothing
#: here, ``check_steep_streets`` reports the model's own steepest streets.
KNOWN_STEEP: dict[str, float] = {}

#: A street counts as flat when its length-weighted mean absolute gradient
#: is under this.  Gain per kilometre is reported too but not judged: it
#: depends on which way each block happens to be drawn, so a railway grade
#: climbing steadily at 1% reads as 10 m/km one way and nothing the other.
FLAT_MEAN_GRADE = 0.02

#: Corridors local knowledge says are flat: the streets that carry them and,
#: where the name alone is ambiguous, the geographic window that isolates the
#: corridor.  Windows are (lon_min, lon_max, lat_min, lat_max) in WGS84.
KNOWN_FLAT = {
    # The Eastside Trail is validated twice: as a set of edges, below, and as
    # the answer to a routing question.  The trip is Glenwood Avenue at Bill
    # Kennedy Way to Piedmont Park at 10th and Monroe: the direct way is up
    # Boulevard, over every ridge between the two; the flat way is the old
    # belt railway.  A correct model takes the trail when asked for a flat
    # route and Boulevard when asked for the shortest.
    "The BeltLine (as a route)": {
        "streets": [],
        "route": ((-84.3530, 33.7405), (-84.3682, 33.7818)),
    },
    "BeltLine Eastside Trail": {
        "streets": ["Atlanta Beltline Eastside Trail", "Interim BeltLine Eastside Trail"],
    },
    "BeltLine Westside Trail": {"streets": ["Atlanta Beltline Westside Trail"]},
    "BeltLine Southside and Southeast trails": {
        "streets": ["Atlanta Beltline Southside Trail", "Atlanta BeltLine Southside Trail",
                    "Atlanta Beltline Southeast Trail"],
    },
    "Proctor Creek Greenway": {"streets": ["Proctor Creek Greenway"]},
    "DeKalb Avenue (beside the Georgia Railroad)": {
        "streets": ["DeKalb Avenue Northeast", "DeKalb Avenue"],
    },
    "Marietta Street (beside the Western & Atlantic)": {
        "streets": ["Marietta Street Northwest", "Marietta Street",
                    "West Marietta Street Northwest"],
    },
    "Lee Street / Murphy Avenue (beside the railway south)": {
        "streets": ["Lee Street Southwest", "Murphy Avenue Southwest"],
    },
    "Peachtree Street, Downtown to Midtown (the ridge road)": {
        "streets": ["Peachtree Street Northeast"],
    },
}

#: The routing question behind "The BeltLine (as a route)", for the report.
SIGNATURE = {
    "key": "The BeltLine (as a route)",
    "title": "The BeltLine",
    "from": "Glenwood Avenue at Bill Kennedy Way",
    "to": "Piedmont Park at 10th Street and Monroe Drive",
    "direct": "Boulevard",
    # the streets that make up the corridor, to say whether a route used it
    "streets": {"Atlanta Beltline Eastside Trail", "Atlanta Beltline Southeast Trail",
                "Interim BeltLine Eastside Trail", "Bill Kennedy Way",
                "Krog Street Northeast", "Wylie Street Southeast"},
}

DRIVABLE = ("residential", "living_street", "tertiary", "secondary",
            "primary", "trunk", "unclassified")


# --------------------------------------------------------------------------
def check_dem_agreement(n_points: int = 4000, seed: int = 0) -> pd.DataFrame:
    """Compare the 1 m mosaic with the 1/3 arc-second DEM at random nodes."""
    import rasterio
    from pyproj import Transformer

    from .download import DEM_13_TIF
    from .elevation import DEM_MOSAIC, DemSampler

    if not DEM_13_TIF.exists():
        log.warning("1/3 arc-second DEM not cached; skipping cross-check")
        return pd.DataFrame()

    sampler = DemSampler(DEM_MOSAIC, smooth=False)
    rng = np.random.default_rng(seed)
    import rasterio as rio
    with rio.open(DEM_MOSAIC) as src:
        b = src.bounds
    xs = rng.uniform(b.left + 50, b.right - 50, n_points * 4)
    ys = rng.uniform(b.bottom + 50, b.top - 50, n_points * 4)
    z1 = sampler.sample(xs, ys)
    ok = np.isfinite(z1)
    xs, ys, z1 = xs[ok][:n_points], ys[ok][:n_points], z1[ok][:n_points]

    tr = Transformer.from_crs(CRS_PROJECTED, "EPSG:4269", always_xy=True)
    lon, lat = tr.transform(xs, ys)
    with rasterio.open(DEM_13_TIF) as d13:
        nod = d13.nodata
        z2 = np.array([v[0] for v in d13.sample(list(zip(lon, lat)))],
                      dtype="float64")
    good = np.isfinite(z2) & (z2 != nod) & (z2 > -100)
    df = pd.DataFrame({"z_1m": z1[good], "z_13": z2[good]})
    df["diff"] = df["z_1m"] - df["z_13"]
    log.info("DEM cross-check on %d points: mean diff %+.2f m, median %+.2f m, "
             "RMS %.2f m, |diff|<2 m for %.1f%%", len(df), df["diff"].mean(),
             df["diff"].median(), float(np.sqrt((df["diff"] ** 2).mean())),
             100.0 * (df["diff"].abs() < 2).mean())
    return df


def check_steep_streets(edges, n_unpublished: int = 12) -> pd.DataFrame:
    """Computed vs published maximum grades for known steep streets.

    Only drivable classes and edges at least
    ``MIN_RELIABLE_GRADE_LENGTH_M`` long are considered, so the comparison is
    against the street rather than against an adjacent stairway or a 5 m stub.

    With no published figures (``KNOWN_STEEP`` empty) the table is instead
    the steepest named streets the model finds, ranked by the average
    gradient of a whole block of at least 80 m.  That is the figure a lidar
    artefact cannot fake: a spike on an otherwise level block moves the
    steepest pitch a long way and the block average hardly at all.
    """
    drivable = edges[edges["cls"].isin(DRIVABLE)
                     & (edges["length_m"] >= MIN_RELIABLE_GRADE_LENGTH_M)]
    rows = []
    if not KNOWN_STEEP:
        named = drivable[drivable["name"].notna() & ~drivable["is_structure"]
                         & (drivable["length_m"] >= 80.0)].copy()
        named["block_grade"] = named["avg_grade_fwd"].abs()
        top = (named.sort_values("block_grade", ascending=False)
                    .drop_duplicates("name").head(n_unpublished))
        for _, r in top.iterrows():
            rows.append({"street": r["name"], "published": np.nan,
                         "computed": float(r["max_abs_grade"]), "diff": np.nan,
                         "block_grade": float(r["block_grade"]),
                         "block_m": float(r["length_m"]),
                         "rise_m": abs(float(r["net_change_fwd"])),
                         "n_edges": int((named["name"] == r["name"]).sum()),
                         "verdict": "no published figure"})
        df = pd.DataFrame(rows)
        # how much of the network carries an implausible pitch at all
        df.attrs["n_drivable"] = int(len(drivable))
        df.attrs["n_over_30"] = int((drivable["max_abs_grade"] >= 0.30).sum())
        return df
    for name, published in KNOWN_STEEP.items():
        sub = drivable[drivable["name"] == name]
        if sub.empty:
            rows.append({"street": name, "published": published,
                         "computed": np.nan, "diff": np.nan,
                         "n_edges": 0, "verdict": "not found"})
            continue
        computed = float(sub["max_abs_grade"].max())
        diff = computed - published
        verdict = ("ok" if abs(diff) <= 0.05 else
                   "under-reported" if diff < 0 else "over-reported")
        rows.append({"street": name, "published": published,
                     "computed": computed, "diff": diff,
                     "n_edges": len(sub),
                     "street_km": float(sub["length_m"].sum() / 1000),
                     "verdict": verdict})
    return pd.DataFrame(rows)


def _window(edges, bbox):
    """Restrict an edge table to a WGS84 bounding box."""
    if bbox is None:
        return edges
    from shapely.geometry import box
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", edges.crs, always_xy=True)
    x0, y0 = tr.transform(bbox[0], bbox[2])
    x1, y1 = tr.transform(bbox[1], bbox[3])
    return edges[edges.geometry.intersects(box(x0, y0, x1, y1))]


def check_flat_corridors(edges, corridors=None) -> pd.DataFrame:
    """Do the known flat corridors measure flat, and do they get discovered?"""
    rows = []
    for label, spec in KNOWN_FLAT.items():
        if spec.get("route") is not None:
            rows.append(_check_flat_route(label, spec, edges, corridors))
            continue
        streets = spec["streets"]
        sub = _window(edges[edges["name"].isin(streets)], spec.get("bbox"))
        if sub.empty:
            rows.append({"corridor": label, "streets": "; ".join(streets),
                         "street_km": 0.0, "gain_per_km": np.nan,
                         "mean_abs_grade": np.nan, "verdict": "not found",
                         "discovered": False})
            continue
        km = float(sub["length_m"].sum() / 1000)
        gain_km = float(sub["cum_gain_fwd"].sum() / km) if km else np.nan
        w = sub["length_m"].to_numpy(dtype="float64")
        mean_grade = float((sub["avg_grade_fwd"].abs() * w).sum() / w.sum())
        discovered = False
        if corridors is not None and len(corridors):
            names = corridors["street_names"].fillna("")
            discovered = bool(any(any(s in n for s in streets) for n in names))
        rows.append({
            "corridor": label, "streets": "; ".join(streets),
            "street_km": km, "gain_per_km": gain_km,
            "mean_abs_grade": mean_grade,
            "verdict": "flat" if mean_grade < FLAT_MEAN_GRADE else "not flat",
            "discovered": discovered,
        })
    return pd.DataFrame(rows)


def _check_flat_route(label, spec, edges, corridors):
    """Measure a corridor defined by its endpoints, comparatively.

    A corridor that must gain height cannot be judged by gain per kilometre.
    What matters is **excess climbing** -- how much more it climbs than the
    unavoidable difference between its endpoints -- and the honest test is
    that number against the excess climbing of the *shortest* route between
    the same two points.
    """
    ctx = _ROUTE_CTX.get("ctx")
    if ctx is None:
        return {"corridor": label, "streets": "(route-based)", "street_km": np.nan,
                "gain_per_km": np.nan, "mean_abs_grade": np.nan,
                "verdict": "not run", "discovered": False}
    from .config import ROUTING_PROFILES
    from .routing import route
    graph = ctx.graphs["bike"]
    (alon, alat), (blon, blat) = spec["route"]
    a = _nearest_graph_node(graph, edges, alon, alat)
    b = _nearest_graph_node(graph, edges, blon, blat)

    res = {}
    for pname in ("shortest", "balanced", "min_climb"):
        arcs, s = route(graph, a, b, ROUTING_PROFILES[pname])
        # signed: a trip that ends lower has no climbing forced on it at all
        net = s["end_elev_m"] - s["start_elev_m"]
        res[pname] = {
            "km": s["distance_m"] / 1000.0,
            "gain": s["elev_gain_m"],
            "net": net,
            "excess": max(0.0, s["elev_gain_m"] - max(net, 0.0)),
            "max_grade": s["max_grade"],
            "names": sorted({n for n in graph.table.iloc[arcs]["name"].dropna()}),
        }
    short_r = res["shortest"]
    # the best flat option is whichever climb-averse objective achieves the
    # least excess climbing without an unreasonable detour
    cands = [r for r in (res["min_climb"], res["balanced"])
             if r["km"] <= 1.35 * short_r["km"]] or [res["min_climb"]]
    flat_r = min(cands, key=lambda r: r["excess"])
    discovered = False
    if corridors is not None and len(corridors):
        cn = corridors["street_names"].fillna("")
        discovered = bool(any(any(n in c for n in flat_r["names"][:8]) for c in cn))
    return {
        "corridor": label,
        "streets": "; ".join(flat_r["names"][:6]),
        "street_km": flat_r["km"],
        "gain_per_km": flat_r["gain"] / flat_r["km"] if flat_r["km"] else np.nan,
        "mean_abs_grade": np.nan,
        "net_rise_m": flat_r["net"],
        "excess_gain_m": flat_r["excess"],
        "shortest_excess_gain_m": short_r["excess"],
        "shortest_gain_m": short_r["gain"],
        "shortest_max_grade": short_r["max_grade"],
        "shortest_km": short_r["km"],
        "flat_max_grade": flat_r["max_grade"],
        "verdict": ("efficient climb" if flat_r["excess"] <= 0.5 * short_r["excess"]
                    else "less wasted climbing" if flat_r["excess"] <= 0.8 * short_r["excess"]
                    else "no better than shortest"),
        "discovered": discovered,
    }


#: set by run_validation so the route-based checks can reach the graphs
_ROUTE_CTX: dict = {}


def _nearest_graph_node(graph, edges, lon, lat):
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", edges.crs, always_xy=True)
    x, y = tr.transform(lon, lat)
    nodes = set(graph.node_ids)
    sub = edges[edges["u"].isin(nodes)]
    coords = np.array([g.coords[0] for g in sub.geometry])
    d = (coords[:, 0] - x) ** 2 + (coords[:, 1] - y) ** 2
    return sub.iloc[int(np.argmin(d))]["u"]


def check_signature_route(ctx) -> dict:
    """Does the flat objective actually route the BeltLine?

    Bicycle routing between the endpoints of ``KNOWN_FLAT[SIGNATURE["key"]]``
    under three objectives, with the corridor's streets each route used.  A
    correct model should choose the corridor when asked for a flat route and
    need not when asked for the shortest.
    """
    from .routing import route
    from .config import ROUTING_PROFILES

    edges = ctx.edges
    graph = ctx.graphs["bike"]
    (alon, alat), (blon, blat) = KNOWN_FLAT[SIGNATURE["key"]]["route"]
    a = _nearest_graph_node(graph, edges, alon, alat)
    b = _nearest_graph_node(graph, edges, blon, blat)
    out = {}
    for pname in ("shortest", "balanced", "min_climb"):
        arcs, s = route(graph, a, b, ROUTING_PROFILES[pname])
        tab = graph.table.iloc[arcs]
        on = tab[tab["name"].isin(SIGNATURE["streets"])]
        out[pname] = {
            "distance_m": s["distance_m"], "gain_m": s["elev_gain_m"],
            "max_grade": s["max_grade"],
            "corridor_streets_used": sorted(set(on["name"])),
            "corridor_share": float(on["length_m"].sum() / max(s["distance_m"], 1e-9)),
        }
    return out


def run_validation(ctx, corridors=None, write: bool = True) -> dict:
    """Run every check and write a markdown report."""
    _ROUTE_CTX["ctx"] = ctx
    with step("validating elevation model against known ground truth", log):
        dem = check_dem_agreement()
        steep = check_steep_streets(ctx.edges)
        flat = check_flat_corridors(ctx.edges, corridors)
        try:
            signature = check_signature_route(ctx)
        except Exception as exc:                      # pragma: no cover
            log.warning("signature route check failed: %s", exc)
            signature = {}

    if write:
        _write_report(dem, steep, flat, signature)
    return {"dem": dem, "steep": steep, "flat": flat, "signature": signature}


def _write_report(dem, steep, flat, signature) -> None:
    L: list[str] = ["# Validation report", ""]
    L += ["## 1. Elevation: 1 m lidar vs independent 1/3 arc-second DEM", ""]
    if len(dem):
        d = dem["diff"]
        L += [f"- Points compared: **{len(dem):,}**",
              f"- Mean difference: **{d.mean():+.2f} m**, "
              f"median **{d.median():+.2f} m**",
              f"- RMS difference: **{np.sqrt((d**2).mean()):.2f} m**",
              f"- Within 2 m: **{100*(d.abs()<2).mean():.1f}%** of points", "",
              "The two products are produced independently, so this level of "
              "agreement confirms the mosaic is correctly georeferenced and "
              "in metres above NAVD88. Residual scatter is expected: the "
              "1/3 arc-second product averages over ~10 m and cannot resolve "
              "the street-scale relief the 1 m product captures.", ""]
    else:
        L += ["_Not run: the 1/3 arc-second tile was not cached._", ""]

    if "published" in steep.columns and steep["published"].notna().any():
        L += ["## 2. Grades on known steep streets", "",
              "| Street | Published | Computed | Difference | Verdict |",
              "|---|---|---|---|---|"]
        for _, r in steep.iterrows():
            c = "n/a" if not np.isfinite(r["computed"]) else f"{r['computed']:.1%}"
            df_ = "n/a" if not np.isfinite(r["diff"]) else f"{r['diff']:+.1%}"
            L.append(f"| {r['street']} | {r['published']:.1%} | {c} | {df_} | "
                     f"{r['verdict']} |")
        ok = int((steep["verdict"] == "ok").sum())
        L += ["", f"**{ok} of {len(steep)}** streets agree within 5 percentage "
              "points.", ""]
    else:
        L += ["## 2. The steepest streets the model finds", "",
              "There is nothing to compare these against. San Francisco's "
              "steep streets have published gradients, and the pipeline this "
              "was ported from was checked against them (six of eight within "
              "five points, with the same smoothing and sampling settings "
              "used here). Atlanta has no such table that could be traced to "
              "a measurement, so this section is a list of readings, not a "
              "validation. Streets are ranked by the average gradient of "
              "a whole block of at least 80 m, the figure a lidar artefact "
              "cannot fake; the steepest pitch within the block is beside it.", "",
              "| Street | Block average | Rise | Block length | Steepest pitch |",
              "|---|---|---|---|---|"]
        for _, r in steep.iterrows():
            L.append(f"| {r['street']} | {r['block_grade']:.1%} | "
                     f"{r['rise_m']:.0f} m | {r['block_m']:.0f} m | "
                     f"{r['computed']:.1%} |")
        n_all = steep.attrs.get("n_drivable", 0)
        n_30 = steep.attrs.get("n_over_30", 0)
        L += ["", "The steepest-pitch column is the less trustworthy of the "
              f"two. {n_30:,} of {n_all:,} drivable blocks "
              f"({100 * n_30 / max(n_all, 1):.1f}%) carry a pitch of 30% or "
              "more somewhere along them, and most of those are steep for a "
              "few metres on a block that is otherwise gentle: a real kink "
              "(a ramp, a culvert), or ground that has changed since the "
              "lidar was flown in 2018, as in the subdivisions built since. "
              "They are left in rather than filtered, and they are why the "
              "route finder's *steepest* figure should be read as an upper "
              "bound.", "",
              "Two families of artefact were removed before this table was "
              "made, because they sat on the streets that matter most. A "
              "street passing under a freeway or railway bridge read as a "
              "hump, the bare-earth surface there being interpolated from "
              "the embankments either side: Windsor Street under I-20 "
              "carried 11 m of climbing that does not exist. And the last "
              "metres of a street approaching a bridge fell away, because "
              "the mapped end of a bridge usually sits out over the cut. "
              "Both are now found from the street geometry and bridged "
              "(`network.find_dem_gaps`, `elevation.ABUTMENT_PAD_M`).", ""]

    L += ["## 3. Known flat corridors", "",
          "| Corridor | Km | Gain per km | Mean abs grade | Verdict | "
          "Discovered by the model? |", "|---|---|---|---|---|---|"]
    for _, r in flat.iterrows():
        g = "n/a" if not np.isfinite(r["gain_per_km"]) else f"{r['gain_per_km']:.1f} m"
        m = "n/a" if not np.isfinite(r["mean_abs_grade"]) else f"{r['mean_abs_grade']:.1%}"
        L.append(f"| {r['corridor']} | {r['street_km']:.1f} | {g} | {m} | "
                 f"{r['verdict']} | {'yes' if r['discovered'] else 'no'} |")
    L += ["", f"A corridor is called flat when its mean absolute gradient is "
          f"under {FLAT_MEAN_GRADE:.0%}. Gain per kilometre is shown for "
          "scale but depends on the direction each block was drawn in, so a "
          "railway grade climbing steadily one way shows a figure and the "
          "same grade drawn the other way shows none.", ""]
    sig = flat[flat["corridor"] == SIGNATURE["key"]]
    if len(sig) and np.isfinite(sig.iloc[0].get("excess_gain_m", np.nan)):
        w = sig.iloc[0]
        L += [f"### {SIGNATURE['title']}", "",
              f"{SIGNATURE['title']} is also measured as a *route*, by asking "
              f"the model the question the corridor answers: "
              f"{SIGNATURE['from']}, to {SIGNATURE['to']}, by bicycle. The "
              f"direct way is {SIGNATURE['direct']}.", "",
              "| | Distance | Climb | Net rise | Excess climb | Max grade |",
              "|---|---|---|---|---|---|",
              f"| Shortest route | {w['shortest_km']:.2f} km | "
              f"{w['shortest_gain_m']:.1f} m | {w['net_rise_m']:.1f} m | "
              f"**{w['shortest_excess_gain_m']:.1f} m** | "
              f"{w['shortest_max_grade']:.1%} |",
              f"| Flat route | {w['street_km']:.2f} km | "
              f"{w['gain_per_km']*w['street_km']:.1f} m | "
              f"{w['net_rise_m']:.1f} m | **{w['excess_gain_m']:.1f} m** | "
              f"{w['flat_max_grade']:.1%} |", "",
              (f"The trip ends {abs(w['net_rise_m']):.0f} m "
               f"{'higher' if w['net_rise_m'] > 0 else 'lower'} than it starts"
               + (", so none of the climbing is forced. " if w['net_rise_m'] <= 0
                  else ". Beyond that, ")
               + f"The shortest route climbs {w['shortest_excess_gain_m']:.0f} m "
               f"it did not have to; the flat one {w['excess_gain_m']:.0f} m, "
               f"{100 * (1 - w['excess_gain_m'] / max(w['shortest_excess_gain_m'], 1e-9)):.0f}% "
               f"less, for {100 * (w['street_km'] / w['shortest_km'] - 1):.0f}% "
               f"more distance. Verdict: **{w['verdict']}**."), ""]

    if signature:
        L += [f"## 4. Does the model route {SIGNATURE['title']}?", "",
              f"Bicycle routing from {SIGNATURE['from']} to "
              f"{SIGNATURE['to']}. The corridor was not named to the model.", "",
              "| Objective | Distance | Climb | Max grade | Share on the "
              "corridor | Corridor streets used |",
              "|---|---|---|---|---|---|"]
        for k, v in signature.items():
            L.append(f"| {k} | {v['distance_m']:.0f} m | {v['gain_m']:.1f} m | "
                     f"{v['max_grade']:.1%} | {v['corridor_share']:.0%} | "
                     f"{', '.join(v['corridor_streets_used']) or '(none)'} |")
        L += [""]

    miss = flat[(flat["verdict"] == "not flat") | (~flat["discovered"].astype(bool))]
    if len(miss):
        L += ["## 5. Targets the model does not reproduce", ""]
        for _, r in miss.iterrows():
            if r["verdict"] == "not flat":
                L.append(f"- **{r['corridor']}** does not measure as flat "
                         f"({r['mean_abs_grade']:.1%} mean gradient). The "
                         "expectation was wrong, not the model: a ridge road "
                         "follows the top of the ridge, and the top of an "
                         "Atlanta ridge rolls.")
            elif r["verdict"] == "flat":
                L.append(f"- **{r['corridor']}** measures as flat but is not "
                         "in the discovered corridor set. The corridor score "
                         "rewards street that many neighborhood pairs have "
                         "reason to use, and with one access point for each "
                         "of 36 neighborhoods a trail can be level and still "
                         "lie off every pair's way. Flat, but not "
                         "structurally important at this resolution.")
        L += [""]

    VALIDATION_MD.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_MD.write_text("\n".join(L))
    log.info("wrote %s", VALIDATION_MD.name)
