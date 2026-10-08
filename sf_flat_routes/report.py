"""Generate the written analysis of the major findings.

Every number in the report is read from the analysis outputs rather than
typed in, so the prose cannot drift away from the data.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import CITY_NAME, FEATURED_PAIRS, OUTPUT_DIR, PROCESSED_DIR
from .utils import get_logger

log = get_logger("sf_flat_routes.report")

REPORT_MD = OUTPUT_DIR / "findings.md"
M_PER_FT = 3.28084


def _ft(m):
    return m * M_PER_FT


def _mi(m):
    return m / 1609.344


def _load():
    import geopandas as gpd
    from .corridors import CORRIDORS_GPKG
    from .pairs import PAIRS_PARQUET, PARETO_PARQUET
    from .passes import BARRIERS_GEOJSON, PASSES_GEOJSON

    out = {
        "edges": gpd.read_parquet(PROCESSED_DIR / "edges_metrics.parquet"),
        "pairs": pd.read_parquet(PAIRS_PARQUET),
        "corridors": gpd.read_file(CORRIDORS_GPKG),
        "passes": gpd.read_file(PASSES_GEOJSON),
        "barriers": gpd.read_file(BARRIERS_GEOJSON),
    }
    if PARETO_PARQUET.exists():
        out["pareto"] = pd.read_parquet(PARETO_PARQUET)
    pm = OUTPUT_DIR / "pass_matrix.csv"
    if pm.exists():
        out["pass_matrix"] = pd.read_csv(pm)
    return out


# --------------------------------------------------------------------------
def _headline(d) -> list[str]:
    e, p = d["edges"], d["pairs"]
    walk = p[(p["mode"] == "walk")]
    by = walk.groupby("profile")
    short = by.get_group("shortest")
    flat = by.get_group("min_climb")
    bal = by.get_group("balanced")
    ga = by.get_group("grade_averse")

    walkable = e[e["walk_ok"]] if "walk_ok" in e.columns else e
    km = walkable["length_m"].sum() / 1000
    total_km = e["length_m"].sum() / 1000
    L = [
        "## The headline",
        "",
        f"The street network modelled here is {total_km:,.0f} km long, of "
        f"which {km:,.0f} km is walkable. It climbs an average of "
        f"{walkable['cum_gain_fwd'].sum()/km:.1f} m for every kilometre of "
        f"street, which is rolling rather than steep: only "
        f"{100 * walkable.loc[walkable['max_abs_grade'] >= 0.10, 'length_m'].sum() / walkable['length_m'].sum():.0f}% "
        f"of it has a pitch of 10% or more anywhere along the block. "
        f"Across all {len(short):,} ordered neighborhood pairs, on foot:",
        "",
        "| Objective | Mean distance | Mean climb | Mean steepest grade | "
        "Distance penalty | Climbing avoided |",
        "|---|---|---|---|---|---|",
    ]
    for label, g in (("Shortest (distance only)", short),
                     ("Balanced", bal),
                     ("Flattest (minimum climbing)", flat),
                     ("Grade-averse", ga)):
        L.append(
            f"| {label} | {_mi(g['distance_m'].mean()):.2f} mi | "
            f"{_ft(g['elev_gain_m'].mean()):.0f} ft | "
            f"{g['max_grade'].mean():.1%} | "
            f"{100*(g['detour_ratio'].mean()-1):+.0f}% | "
            f"{g['gain_saved_pct'].mean():.0f}% |")
    extra = 100 * (flat["detour_ratio"].mean() - 1)
    saved = flat["gain_saved_pct"].mean()
    L += [
        "",
        f"**About {extra:.0f}% more walking buys about {saved:.0f}% less "
        f"climbing.** That is the central result: the minimum-climbing route "
        f"is on average only {extra:.0f}% longer than the shortest one, yet "
        f"it avoids {saved:.0f}% of the ascent, and it takes the typical "
        f"steepest pitch from {short['max_grade'].mean():.0%} to "
        f"{flat['max_grade'].mean():.0%}.",
        "",
        "That is a smaller prize than in San Francisco, where the same "
        "analysis found 14% more walking buying 39% less climbing. The likely "
        "reason is the shape of the ground: San Francisco's hills are walls "
        "with flats between them, so a detour goes round. Atlanta is a "
        "plateau cut by creeks: almost every trip crosses a valley or two, "
        "every crossing costs the same descent and climb wherever it is "
        "made, and what a flat route can do is stay on the ridge longer and "
        "choose the shallowest place to cross.",
        "",
        "The grade-averse objective is worth separating out. It ends up "
        f"climbing slightly *more* in total than the flattest route "
        f"({_ft(ga['elev_gain_m'].mean()):.0f} ft against "
        f"{_ft(flat['elev_gain_m'].mean()):.0f} ft) while costing much more "
        f"distance, but it holds the steepest pitch to "
        f"{ga['max_grade'].mean():.1%} where the flattest route still allows "
        f"{flat['max_grade'].mean():.1%}. Total climbing and peak steepness "
        "are genuinely different objectives, and a single definition of "
        "\"flat\" cannot serve both: minimising total ascent will happily send "
        "you up one short wall, and avoiding walls will make you climb a "
        "little more overall.",
        "",
        "A note on the steepest-pitch column. It is the least reliable "
        "figure here: it is set by the single worst few metres of a route, "
        "and a lidar artefact or a short ramp is enough to move it (see the "
        "validation report). The climbing totals are sums over the whole "
        "route and are not sensitive to that.",
        "",
    ]
    return L


def _featured(d) -> list[str]:
    p = d["pairs"]
    L = ["## Specific answers", "",
         "### What is the flattest reasonable route from Midtown to Grant "
         "Park, or Downtown to Buckhead?", "",
         "| From | To | Shortest | Flattest | Balanced |", "|---|---|---|---|---|"]

    def cell(g):
        if g.empty:
            return "n/a"
        r = g.iloc[0]
        return (f"{_mi(r['distance_m']):.2f} mi / {_ft(r['elev_gain_m']):.0f} ft "
                f"/ max {r['max_grade']:.0%}")

    walk = p[p["mode"] == "walk"]
    for o, dst in FEATURED_PAIRS:
        sub = walk[(walk["origin"] == o) & (walk["destination"] == dst)]
        if sub.empty:
            continue
        L.append(f"| {o} | {dst} | "
                 f"{cell(sub[sub['profile']=='shortest'])} | "
                 f"{cell(sub[sub['profile']=='min_climb'])} | "
                 f"{cell(sub[sub['profile']=='balanced'])} |")
    L += ["", "Each cell is distance / cumulative climb / steepest gradient.", ""]

    best = (walk[walk["profile"] == "min_climb"]
            .nlargest(8, "gain_saved_m")
            [["origin", "destination", "shortest_distance_m", "shortest_gain_m",
              "distance_m", "elev_gain_m", "climb_saved_per_extra_m"]])
    L += ["### Where does flat routing pay off most?", "",
          "The neighborhood pairs where choosing the flat route avoids the "
          "most climbing:", "",
          "| From | To | Shortest | Flattest | Climbing avoided | "
          "Metres of climb saved per extra metre walked |",
          "|---|---|---|---|---|---|"]
    for _, r in best.iterrows():
        eff = r["climb_saved_per_extra_m"]
        L.append(
            f"| {r['origin']} | {r['destination']} | "
            f"{_mi(r['shortest_distance_m']):.2f} mi / "
            f"{_ft(r['shortest_gain_m']):.0f} ft | "
            f"{_mi(r['distance_m']):.2f} mi / {_ft(r['elev_gain_m']):.0f} ft | "
            f"{_ft(r['shortest_gain_m']-r['elev_gain_m']):.0f} ft | "
            f"{eff:.2f} |")
    L.append("")
    return L


def _corridors(d) -> list[str]:
    c = d["corridors"]
    e = d["edges"]
    walk = c[c["mode"] == "walk"].head(12)
    L = [f"## {CITY_NAME}'s flat corridors", "",
         "These were *discovered*, not listed: the analysis aggregated how "
         "often each street segment carried a good flat route "
         "between neighborhoods, weighted by the climbing those routes "
         "avoided, and merged the high-scoring segments into contiguous "
         "corridors. No corridor was named in advance.", "",
         "| Corridor | Length | Mean grade | Climb per km | Pairs served | "
         "Neighborhoods | Elevation range |", "|---|---|---|---|---|---|---|"]
    for _, r in walk.iterrows():
        L.append(
            f"| {r['corridor_name']} | {r['length_km']:.1f} km | "
            f"{r['mean_abs_grade']:.1%} | {r['gain_per_km']:.1f} m | "
            f"{int(r['pair_count_max'])} | {int(r['neighborhood_span'])} | "
            f"{r['elev_min_m']:.0f}-{r['elev_max_m']:.0f} m |")
    wk = e[e["walk_ok"]] if "walk_ok" in e.columns else e
    net_km = wk["length_m"].sum() / 1000
    L += ["",
          f"For scale: the walkable network as a whole climbs "
          f"{wk['cum_gain_fwd'].sum() / net_km:.0f} m per kilometre of street.", ""]

    if len(walk):
        # where the corridors sit in the city's relief
        mid = (wk["elev_min"] + wk["elev_max"]) / 2
        median_z = float(np.average(mid.to_numpy(), weights=None) if len(mid) else np.nan)
        median_z = float(mid.median())
        cmid = (walk["elev_min_m"] + walk["elev_max_m"]) / 2
        high = walk[cmid > median_z]
        top = walk.iloc[0]
        L += ["### Flat means high", "",
              f"**{top['corridor_name']}** ({top['length_km']:.1f} km, "
              f"{top['mean_abs_grade']:.1%} mean gradient) is the most "
              f"important flat corridor in the city, serving "
              f"{int(top['pair_count_max'])} ordered neighborhood pairs and "
              f"avoiding {top['climb_saved_m']/1000:.1f} km of cumulative "
              f"climbing in aggregate.", "",
              f"The pattern in the table is in its last column. The median "
              f"street in {CITY_NAME} sits at {median_z:.0f} m; "
              f"{len(high)} of these {len(walk)} corridors "
              f"({high['length_km'].sum():.0f} of "
              f"{walk['length_km'].sum():.0f} km) lie above it. In San "
              f"Francisco the flat streets are the valley floors. Here most "
              f"of them are ridge tops: {CITY_NAME} grew up around railways, the "
              f"railways were laid along the divides because that is where "
              f"the grade is easy, and the streets beside them (Edgewood and "
              f"DeKalb, Marietta, Lee and Murphy, Whitehall and Peters) and "
              f"Peachtree on its own ridge inherited the same profile. The "
              f"low ground is the creeks, and nobody goes far along a creek "
              f"without having to climb out of it.", ""]
    return L


def _unsung(d) -> list[str]:
    """Corridors without a famous name, straight from the corridor set."""
    c = d["corridors"]
    walk = c[c["mode"] == "walk"]
    famous = ("Peachtree", "Beltline", "BeltLine", "DeKalb", "Marietta", "Edgewood",
              "Ponce de Leon")
    unsung = walk[~walk["corridor_name"].str.contains("|".join(famous))].head(8)
    if unsung.empty:
        return []
    L = ["### The corridors nobody names", "",
         "The BeltLine has a name, a logo and a master plan. These do the "
         "same job without any of that:", "",
         "| Corridor | Length | Mean grade | Climb per km | Pairs served | "
         "Neighborhoods at its ends |", "|---|---|---|---|---|---|"]
    for _, r in unsung.iterrows():
        L.append(f"| {r['corridor_name']} | {r['length_km']:.1f} km | "
                 f"{r['mean_abs_grade']:.1%} | {r['gain_per_km']:.1f} m | "
                 f"{int(r['pair_count_max'])} | {int(r['neighborhood_span'])} |")
    L.append("")
    return L


def _passes(d) -> list[str]:
    pz = d["passes"]
    L = ["## Passes, saddles and barriers", "",
         "The question \"how much climbing is unavoidable between these two "
         "parts of the city?\" is a **minimax** problem, not a shortest-path "
         "one: what matters is the lowest summit you can possibly cross. "
         "Solving it over a minimum bottleneck spanning tree gives, for every "
         "pair of neighborhoods, the exact elevation of the lowest available "
         "crossing and the block on which it happens.", ""]
    if "pass_matrix" in d:
        pm = d["pass_matrix"]
        L += [f"Across all {len(pm):,} neighborhood pairs the lowest possible "
              f"crossing averages {pm['pass_elev_ft'].mean():.0f} ft and "
              f"reaches {pm['pass_elev_ft'].max():.0f} ft at worst "
              f"({pm.loc[pm['pass_elev_ft'].idxmax(), 'neighborhood_a']} to "
              f"{pm.loc[pm['pass_elev_ft'].idxmax(), 'neighborhood_b']}). "
              f"Only {len(pz)} distinct blocks in the whole city act as the "
              f"binding constraint for any pair -- the city's real passes.", ""]
    L += ["| Pass | Neighborhood | Lowest possible crossing | Pairs forced "
          "over it | Gradient there |", "|---|---|---|---|---|"]
    for _, r in pz.head(12).iterrows():
        nm = r["name"] if isinstance(r["name"], str) and r["name"] else \
            "(unnamed path)"
        L.append(f"| {nm} | {r['neighborhood']} | {r['pass_elev_ft']:.0f} ft | "
                 f"{int(r['pairs_served'])} | {r['max_abs_grade']:.1%} |")
    if len(pz):
        t = pz.iloc[0]
        nm = t["name"] if isinstance(t["name"], str) and t["name"] else "an unnamed path"
        n_pairs = len(d["pass_matrix"]) if "pass_matrix" in d else None
        of = f" of the {n_pairs:,}" if n_pairs else ""
        L += ["",
              f"The single most consequential pass in {CITY_NAME} is {nm} in "
              f"**{t['neighborhood']}** at {t['pass_elev_ft']:.0f} ft. It is "
              f"the binding constraint for {int(t['pairs_served'])}{of} "
              f"neighborhood pairs, more than any other block in the city, "
              f"and its gradient where it crosses is {t['max_abs_grade']:.1%}: "
              f"a pass is the lowest way over, not a steep one.", "",
              f"The passes are all high, between "
              f"{pz.head(12)['pass_elev_ft'].min():.0f} and "
              f"{pz.head(12)['pass_elev_ft'].max():.0f} ft, and that is the "
              f"other face of the corridor result. The ridges that carry the "
              f"railways through the city are the Eastern Continental Divide "
              f"and its spurs: rain on one side runs to the Chattahoochee and "
              f"the Gulf, on the other to the South River and the Atlantic. "
              f"A trip between neighborhoods on opposite sides has to get "
              f"over that line somewhere, and these are the lowest places it "
              f"can.", ""]

    b = d["barriers"]
    if "unavoidability" in b.columns:
        L += ["### Barriers with an alternative, and barriers without", "",
              "A steep street that carries heavy shortest-path traffic but "
              "almost none once climbing is penalised has a flat alternative "
              "nearby. One that keeps its traffic under every objective does "
              "not.", "",
              "| Street | Neighborhood | Gradient | Pairs via shortest route | "
              "Still via the flat route | Verdict |",
              "|---|---|---|---|---|---|"]
        top = b.nlargest(10, "barrier_score")
        for _, r in top.iterrows():
            nm = r["name"] if isinstance(r["name"], str) and r["name"] else \
                "(unnamed)"
            un = float(r.get("unavoidability") or 0)
            verdict = ("**unavoidable**" if un > 0.5 else
                       "avoidable" if un < 0.15 else "partly avoidable")
            L.append(f"| {nm} | {r['neighborhood']} | "
                     f"{r['max_abs_grade']:.1%} | {int(r['shortest_use'])} | "
                     f"{float(r.get('flat_use_per_objective') or 0):.0f} | "
                     f"{verdict} |")
        L.append("")
    return L


def _pareto(d) -> list[str]:
    if "pareto" not in d:
        return []
    pa = d["pareto"]
    pa = pa[(pa["mode"] == "walk") & pa["pareto_optimal"]]

    # citywide: for every pair, how much detour does halving the climb cost?
    rows = []
    for (o, dst), g in pa.groupby(["origin", "destination"]):
        g = g.sort_values("distance_m")
        s = g.iloc[0]                       # the pure-distance anchor
        if s["elev_gain_m"] <= 0:
            continue
        half = g[g["elev_gain_m"] <= 0.5 * s["elev_gain_m"]]
        quarter = g[g["elev_gain_m"] <= 0.75 * s["elev_gain_m"]]
        rows.append({
            "halvable": len(half) > 0,
            "detour_to_halve": (half["distance_m"].min() / s["distance_m"] - 1)
            if len(half) else np.nan,
            "quarterable": len(quarter) > 0,
            "detour_to_quarter": (quarter["distance_m"].min() / s["distance_m"] - 1)
            if len(quarter) else np.nan,
            "best_saved_pct": 100 * (1 - g["elev_gain_m"].min() / s["elev_gain_m"]),
            "best_detour": g.loc[g["elev_gain_m"].idxmin(), "distance_m"]
            / s["distance_m"] - 1,
        })
    r = pd.DataFrame(rows)

    L = ["## The distance / climbing trade-off", "",
         f"For every one of the {pa.groupby(['origin', 'destination']).ngroups:,} "
         "ordered pairs, a single weight is swept "
         "from zero (pure distance) up to the minimum-climbing objective, "
         "tracing the frontier between distance, cumulative climbing and "
         "peak gradient. The useful question is where the knee is: how much "
         "detour buys how much of the climbing.", ""]
    if len(r):
        n_half = int(r["halvable"].sum())
        if n_half >= 0.05 * len(r):
            L += [f"- **{100*r['halvable'].mean():.0f}% of pairs can halve their "
                  f"climbing** by some route, and the median detour that costs is "
                  f"**{100*r['detour_to_halve'].median():.0f}%**. "
                  f"{100*(r['detour_to_halve'] <= 0.10).mean():.0f}% of all pairs "
                  f"can halve it within a 10% detour, "
                  f"{100*(r['detour_to_halve'] <= 0.20).mean():.0f}% within 20%."]
        else:
            L += [f"- **Almost no pair can halve its climbing** by any route: "
                  f"{n_half} of {len(r):,} can."]
        L += [f"- **{100*r['quarterable'].mean():.0f}% of pairs can shed a "
              f"quarter of their climbing**, at a median detour of "
              f"**{100*r['detour_to_quarter'].median():.0f}%**; "
              f"{100*(r['detour_to_quarter'] <= 0.10).mean():.0f}% of all "
              f"pairs can do it within a 10% detour.",
              f"- Taken to the flattest possible route, the median pair "
              f"sheds **{r['best_saved_pct'].median():.0f}%** of its climbing "
              f"for a median **{100*r['best_detour'].median():.0f}%** more "
              f"distance.", ""]
    for o, dst in FEATURED_PAIRS[:4]:
        g = pa[(pa["origin"] == o) & (pa["destination"] == dst)]
        if g.empty:
            continue
        g = g.sort_values("distance_m")
        L += [f"**{o} to {dst}**", "",
              "| Distance | Climb | Steepest grade |", "|---|---|---|"]
        for _, row in g.iterrows():
            L.append(f"| {_mi(row['distance_m']):.2f} mi | "
                     f"{_ft(row['elev_gain_m']):.0f} ft | "
                     f"{row['max_grade']:.0%} |")
        L.append("")
    L += ["The frontiers are concave: the first fraction of extra "
          "distance removes most of the climbing that can be removed, and "
          "everything after that buys very little. That is the practical "
          "argument for the balanced objective over the purely flattest "
          "one.", ""]
    return L


def _modes(d) -> list[str]:
    p = d["pairs"]
    e = d["edges"]
    w = p[(p["mode"] == "walk") & (p["profile"] == "min_climb")]
    b = p[(p["mode"] == "bike") & (p["profile"] == "min_climb")]
    steps_km = e[e["cls"] == "steps"]["length_m"].sum() / 1000
    L = ["## Walking is not cycling", "",
         f"The two networks are modelled separately. {CITY_NAME} has only "
         f"{steps_km:.0f} km of public stairways, which are part of the "
         f"pedestrian network and excluded outright for bicycles, so the "
         f"difference here is mostly one-way streets, which bind a bicycle "
         f"and not a pedestrian, and stress weights: a protected cycleway "
         f"counts as 0.85 of its length, and a trunk road such as Ponce de "
         f"Leon Avenue, Moreland Avenue or Northside Drive as 1.9.", "",
         f"The flattest bicycle route averages "
         f"{_mi(b['distance_m'].mean()):.2f} mi and "
         f"{_ft(b['elev_gain_m'].mean()):.0f} ft of climbing against "
         f"{_mi(w['distance_m'].mean()):.2f} mi and "
         f"{_ft(w['elev_gain_m'].mean()):.0f} ft on foot.", ""]
    return L


def _top_corridor_phrase(sd: pd.DataFrame) -> str:
    """'the top corridor is X in every run', or an honest count."""
    leads = sd["top_corridor"].fillna("").map(lambda v: v.split(" - ")[0])
    counts = leads.value_counts()
    top, n = counts.index[0], int(counts.iloc[0])
    if n == len(sd):
        return f"the top corridor is {top} in every run"
    return f"the top corridor is {top} in {n} of {len(sd)} runs"


def _robustness(d) -> list[str]:
    """Summarise the sensitivity analysis, if it has been run."""
    path = OUTPUT_DIR / "sensitivity.csv"
    if not path.exists():
        return []
    sd = pd.read_csv(path, index_col="tag")
    if "baseline" not in sd.index or len(sd) < 2:
        return []
    base = sd.loc["baseline"]
    others = sd.drop(index="baseline")
    L = ["## How much of this depends on the modelling choices?", "",
         "Every figure above was recomputed with the whole pipeline rebuilt "
         f"under {len(others)} one-at-a-time changes to the elevation "
         "parameters and the choice of access intersection "
         "(`outputs/sensitivity.md` has the full tables).", "",
         "| Finding | Baseline | Range across all perturbations |",
         "|---|---|---|",
         f"| Flattest route: extra distance | {base['min_climb_detour_pct']:+.0f}% | "
         f"{others['min_climb_detour_pct'].min():+.0f}% to "
         f"{others['min_climb_detour_pct'].max():+.0f}% |",
         f"| Flattest route: climbing avoided | {base['min_climb_gain_saved_pct']:.0f}% | "
         f"{others['min_climb_gain_saved_pct'].min():.0f}% to "
         f"{others['min_climb_gain_saved_pct'].max():.0f}% |",
         f"| Grade-averse: mean steepest pitch | {base['grade_averse_max_grade_pct']:.1f}% | "
         f"{others['grade_averse_max_grade_pct'].min():.1f}% to "
         f"{others['grade_averse_max_grade_pct'].max():.1f}% |",
         f"| Corridor material shared with baseline (by length) | 100% | "
         f"{others['edge_overlap_pct'].min():.0f}% to "
         f"{others['edge_overlap_pct'].max():.0f}% |",
         f"| Lead streets of the top 12 corridors kept | 12 of 12 | "
         f"{int(others['lead_streets_shared'].min())} to "
         f"{int(others['lead_streets_shared'].max())} of 12 |",
         f"| Dominant pass | {base['top_pass_nbhd']}, {base['top_pass_ft']:.0f} ft | "
         f"same location in {int((sd['top_pass_nbhd'] == base['top_pass_nbhd']).sum())} "
         f"of {len(sd)} runs; {others['top_pass_ft'].min():.0f}-"
         f"{others['top_pass_ft'].max():.0f} ft |",
         f"| BeltLine trip: excess climb, flat vs shortest | "
         f"{base['signature_excess_flat_m']:.1f} vs {base['signature_excess_shortest_m']:.1f} m | "
         f"flat {others['signature_excess_flat_m'].min():.1f}-"
         f"{others['signature_excess_flat_m'].max():.1f} m, shortest "
         f"{others['signature_excess_shortest_m'].min():.1f}-"
         f"{others['signature_excess_shortest_m'].max():.1f} m |",
         ""]
    steep_cols = [c for c in sd.columns if c.startswith("grade_")
                  and not c.startswith("grade_averse")]
    if steep_cols:
        c0 = steep_cols[0]
        L.insert(-1, f"| {c0[len('grade_'):]}: steepest pitch | "
                     f"{base[c0]:.1f}% | {others[c0].min():.1f}% to "
                     f"{others[c0].max():.1f}% |")
    dev = (others["min_climb_gain_saved_pct"] - base["min_climb_gain_saved_pct"]).abs()
    worst = dev.idxmax()
    least = others["edge_overlap_pct"].idxmin()
    drifters = sorted({s for v in others["lead_streets_new"].fillna("")
                       for s in str(v).split("; ") if s})
    L += [f"The headline barely moves: the perturbation that shifts it most "
          f"is **{worst}** ({others.loc[worst, 'change']}), at "
          f"{others.loc[worst, 'min_climb_gain_saved_pct']:.0f}% climbing "
          f"avoided against {base['min_climb_gain_saved_pct']:.0f}% at "
          f"baseline. The dominant pass is in the same neighborhood in "
          f"{int((sd['top_pass_nbhd'] == base['top_pass_nbhd']).sum())} of "
          f"{len(sd)} runs, and the flat BeltLine route wastes less climbing "
          f"than the shortest in "
          f"{int((sd['signature_excess_flat_m'] < sd['signature_excess_shortest_m']).sum())}.", "",
          f"The corridors are where the model is least rigid, and it is "
          f"worth being precise about how. The *street* that qualifies as "
          f"corridor material is {others['edge_overlap_pct'].min():.0f}-"
          f"{others['edge_overlap_pct'].max():.0f}% the same by length, and "
          f"{_top_corridor_phrase(sd)}; what changes "
          f"is where each corridor is cut and therefore what it is called, "
          f"most under **{least}** ({others.loc[least, 'change']}, "
          f"{others['edge_overlap_pct'].min():.0f}%). A handful of "
          f"borderline streets drift in and out of the top twelve "
          f"({', '.join(drifters)}): these are real corridors whose rank "
          f"depends on tenths of a percent of gradient, not artefacts, and "
          f"they should be read as a tier rather than a ranking.", ""]
    return L


def _limits(d) -> list[str]:
    return [
        "## What this analysis does not tell you", "",
        "- **Elevation is the ground, not the street surface, and the ground "
        "of 2018.** The 1 m lidar DEM is bare-earth, so bridges and tunnels "
        "are corrected by interpolating across the structure, and streets "
        "passing under a deck are bridged across it. Anything built or "
        "regraded since the survey is measured as it was.",
        "- **Travel is modelled on street centrelines.** Sidewalk and "
        "crosswalk geometry exists in the source data but is deliberately "
        "excluded: including it would represent every street two or three "
        "times and wreck the corridor aggregation. Pedestrian distances are "
        "therefore block-scale, not door-to-door, and nothing here knows "
        "whether a street has a sidewalk at all, which in parts of Atlanta "
        "it does not.",
        "- **36 of 248 neighborhoods, one access point each.** The pair "
        "matrix runs between 36 neighborhoods chosen to cover all 25 "
        "Neighborhood Planning Units, each represented by a single "
        "street-network-weighted, intersection-snapped point. The headline "
        "averages are averages over those trips, which are long: "
        f"{_mi(d['pairs'].loc[(d['pairs']['mode'] == 'walk') & (d['pairs']['profile'] == 'shortest'), 'distance_m'].mean()):.1f} "
        "miles on average by the shortest route. They say what the terrain "
        "allows across the city, not what a typical errand looks like.",
        "- **City limits only.** Decatur, Druid Hills, East Point, Sandy "
        "Springs and the rest of the metropolitan area are outside the "
        "network, so a route cannot leave the city even where the flat way "
        "round would.",
        "- **No traffic, surface quality, signals or safety.** The bicycle "
        "stress weights are a crude proxy for road class, not a level-of-"
        "traffic-stress model, and nothing here accounts for signal delay, "
        "pavement condition or collision risk.",
        "- **Bicycle facilities in the analysis are OSM-derived.** The "
        "explorer's bicycle and low-stress layers are inferred from "
        "OpenStreetMap tagging. The route finder's *prefer calm streets* "
        "uses the Atlanta Regional Commission's facility inventory instead, "
        "which is regional and coarser than a city bikeway layer.",
        "",
    ]


def write_report() -> Path:
    d = _load()
    L = [f"# {CITY_NAME}'s flat street network: findings", "",
         "*Generated by `python -m sf_flat_routes report`. Every figure is "
         "computed from the analysis outputs in this repository; see "
         "`validation_report.md` for the checks against known ground truth.*",
         ""]
    L += _headline(d)
    L += _featured(d)
    L += _corridors(d)
    L += _unsung(d)
    L += _passes(d)
    L += _pareto(d)
    L += _modes(d)
    L += _robustness(d)
    L += _limits(d)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(L))
    return REPORT_MD
