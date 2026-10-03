"""Sensitivity analysis: how much do the findings depend on the parameters?

Every number in the report rests on a handful of modelling choices -- how
densely the DEM is sampled, how hard it is smoothed, the dead-band, and which
intersection stands in for each neighborhood.  A sceptical reader's first
question is whether the headline moves when those change.  This harness
answers it by rebuilding the whole pipeline under a one-at-a-time grid around
the baseline and tabulating what comes out.

Each configuration runs in its own subprocess with ``SFFR_RUN_DIR`` and
``SFFR_OVERRIDES`` set, so the main results are never touched, and the
baseline DEM mosaic is reused by symlink because it does not depend on any of
the parameters.  Runs are cached: a configuration whose summary already
exists is not rebuilt.

    python -m sf_flat_routes sensitivity            # ~30 min on 4 cores
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ANALYSIS, DATA_DIR, ELEVATION, OUTPUT_DIR, PROJECT_ROOT
from .utils import get_logger, step

log = get_logger("sf_flat_routes.sensitivity")

RUNS_DIR = DATA_DIR / "sensitivity"
SENS_CSV = OUTPUT_DIR / "sensitivity.csv"
SENS_MD = OUTPUT_DIR / "sensitivity.md"

BASELINE = {
    "sample_spacing_m": ELEVATION.sample_spacing_m,
    "smooth_window_m": ELEVATION.smooth_window_m,
    "gain_deadband_m": ELEVATION.gain_deadband_m,
    "dem_sigma_m": ELEVATION.dem_sigma_m,
    "point_rank": ANALYSIS.point_rank,
}

#: One-at-a-time perturbations around the baseline, with the reason each is
#: worth testing.
GRID = [
    ("baseline", {}, "the configuration used for every published figure"),
    ("spacing_2.5m", {"sample_spacing_m": 2.5}, "denser DEM sampling"),
    ("spacing_10m", {"sample_spacing_m": 10.0}, "coarser DEM sampling"),
    ("window_12.5m", {"smooth_window_m": 12.5}, "half the profile smoothing"),
    ("window_50m", {"smooth_window_m": 50.0}, "double the profile smoothing"),
    ("deadband_0.25m", {"gain_deadband_m": 0.25}, "half the dead-band"),
    ("deadband_1m", {"gain_deadband_m": 1.0}, "double the dead-band"),
    ("sigma_0m", {"dem_sigma_m": 0.0}, "no spatial pre-filter on the DEM"),
    ("sigma_6m", {"dem_sigma_m": 6.0}, "double the spatial pre-filter"),
    ("point_rank_1", {"point_rank": 1}, "second-nearest access intersection"),
    ("point_rank_2", {"point_rank": 2}, "third-nearest access intersection"),
]

_STEEP = ["Filbert Street", "Jones Street", "22nd Street", "Bradford Street"]
_FLAT = ["The Embarcadero", "Valencia Street", "Market Street"]
_DRIVABLE = ("residential", "tertiary", "secondary", "primary", "unclassified",
             "living_street")


# --------------------------------------------------------------------------
# per-run summary (executed inside the subprocess, under the overrides)
# --------------------------------------------------------------------------
def summarize_current_run(tag: str) -> Path:
    """Write ``outputs/summary.json`` for the configuration now in force."""
    import geopandas as gpd
    from .config import MIN_RELIABLE_GRADE_LENGTH_M
    from .corridors import CORRIDORS_GPKG
    from .pairs import PAIRS_PARQUET
    from .passes import PASSES_GEOJSON
    from .pipeline import build_context
    from .utils import configure_gdal_for_proxy
    from .validate import run_validation

    configure_gdal_for_proxy()
    ctx = build_context()
    pairs = pd.read_parquet(PAIRS_PARQUET)
    cor = gpd.read_file(CORRIDORS_GPKG)
    passes = gpd.read_file(PASSES_GEOJSON)
    edges = ctx.edges

    out: dict = {"tag": tag,
                 "params": {"sample_spacing_m": ELEVATION.sample_spacing_m,
                            "smooth_window_m": ELEVATION.smooth_window_m,
                            "gain_deadband_m": ELEVATION.gain_deadband_m,
                            "dem_sigma_m": ELEVATION.dem_sigma_m,
                            "point_rank": ANALYSIS.point_rank}}

    walk = pairs[pairs["mode"] == "walk"]
    for pname, g in walk.groupby("profile"):
        out[f"{pname}_detour_pct"] = float(100 * (g["detour_ratio"].mean() - 1))
        out[f"{pname}_gain_saved_pct"] = float(g["gain_saved_pct"].mean())
        out[f"{pname}_gain_ft"] = float(g["elev_gain_m"].mean() * 3.28084)
        out[f"{pname}_max_grade_pct"] = float(100 * g["max_grade"].mean())
        out[f"{pname}_dist_mi"] = float(g["distance_m"].mean() / 1609.344)

    km = edges[edges["walk_ok"]]["length_m"].sum() / 1000
    out["network_gain_per_km"] = float(
        edges[edges["walk_ok"]]["cum_gain_fwd"].sum() / km)

    for nm in _STEEP:
        sub = edges[(edges["name"] == nm) & edges["cls"].isin(_DRIVABLE)
                    & (edges["length_m"] >= MIN_RELIABLE_GRADE_LENGTH_M)]
        out[f"grade_{nm}"] = float(sub["max_abs_grade"].max() * 100) if len(sub) else np.nan
    for nm in _FLAT:
        sub = edges[edges["name"] == nm]
        k = sub["length_m"].sum() / 1000
        out[f"gainkm_{nm}"] = float(sub["cum_gain_fwd"].sum() / k) if k else np.nan

    cw = cor[cor["mode"] == "walk"].sort_values("total_score", ascending=False)
    out["top_corridors"] = cw["corridor_name"].head(12).tolist()
    out["corridor_count"] = int(len(cw))
    out["top_corridor_km"] = float(cw["length_km"].iloc[0]) if len(cw) else np.nan

    if len(passes):
        p0 = passes.iloc[0]
        out["top_pass_ft"] = float(p0["pass_elev_ft"])
        out["top_pass_pairs"] = int(p0["pairs_served"])
        out["top_pass_nbhd"] = str(p0["neighborhood"])
    out["n_passes"] = int(len(passes))

    val = run_validation(ctx, cor, write=False)
    wig = val["flat"][val["flat"]["corridor"].str.contains("Wiggle")]
    if len(wig):
        w = wig.iloc[0]
        out["wiggle_excess_flat_m"] = float(w["excess_gain_m"])
        out["wiggle_excess_shortest_m"] = float(w["shortest_excess_gain_m"])
        out["wiggle_discovered"] = bool(w["discovered"])
    dem = val["dem"]
    if len(dem):
        out["dem_rms_m"] = float(np.sqrt((dem["diff"] ** 2).mean()))

    path = OUTPUT_DIR / "summary.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    log.info("wrote %s", path)
    return path


# --------------------------------------------------------------------------
# orchestration (executed in the parent, with the baseline configuration)
# --------------------------------------------------------------------------
def _run(tag: str, overrides: dict, force: bool = False) -> dict:
    run_dir = RUNS_DIR / tag
    summary = run_dir / "outputs" / "summary.json"
    if summary.exists() and not force:
        log.info("[%s] cached", tag)
        return json.loads(summary.read_text())

    (run_dir / "processed").mkdir(parents=True, exist_ok=True)
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    # the mosaic is parameter-independent and 20 s to rebuild: share it
    base_mosaic = DATA_DIR / "processed" / "dem_sf_1m.tif"
    link = run_dir / "processed" / "dem_sf_1m.tif"
    if base_mosaic.exists() and not link.exists():
        link.symlink_to(base_mosaic)

    env = dict(os.environ)
    env["SFFR_RUN_DIR"] = str(run_dir)
    env["SFFR_OVERRIDES"] = json.dumps(overrides)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    log_path = run_dir / "run.log"
    t0 = time.time()
    with open(log_path, "w") as fh:
        for cmd in (["build-network", "--force"], ["analyze", "--force"],
                    ["summarize", "--tag", tag]):
            rc = subprocess.call([sys.executable, "-m", "sf_flat_routes", *cmd],
                                 env=env, stdout=fh, stderr=subprocess.STDOUT,
                                 cwd=str(PROJECT_ROOT))
            if rc:
                raise RuntimeError(f"[{tag}] `{' '.join(cmd)}` failed (rc={rc}); "
                                   f"see {log_path}")
    log.info("[%s] done in %.0fs", tag, time.time() - t0)
    return json.loads(summary.read_text())


def run_sensitivity(force: bool = False, only: list[str] | None = None) -> pd.DataFrame:
    rows = []
    with step(f"sensitivity analysis over {len(GRID)} configurations", log):
        for tag, overrides, _why in GRID:
            if only and tag not in only:
                continue
            rows.append(_run(tag, overrides, force=force))
    df = pd.DataFrame(rows)
    df = df.set_index("tag")
    _write(df)
    return df


def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


def _write(df: pd.DataFrame) -> None:
    why = {tag: w for tag, _o, w in GRID}
    base = df.loc["baseline"] if "baseline" in df.index else None
    flat = df.drop(columns=["top_corridors", "params"], errors="ignore")
    flat.to_csv(SENS_CSV)

    L = ["# Sensitivity analysis", "",
         "Every configuration below rebuilds the full pipeline -- elevation "
         "sampling, metrics, 10,080 routes, corridors and passes -- with one "
         "parameter changed from the baseline. The question is whether the "
         "findings survive the modelling choices.", "",
         "Baseline: " + ", ".join(f"{k} = {v:g}" for k, v in BASELINE.items()), "",
         "## The headline (walking, all 1,260 ordered pairs)", "",
         "| Configuration | Change | Flattest: extra distance | Flattest: "
         "climbing avoided | Shortest: mean climb | Flattest: mean climb | "
         "Grade-averse: mean steepest |",
         "|---|---|---|---|---|---|---|"]
    for tag, r in df.iterrows():
        L.append(f"| {tag} | {why.get(tag, '')} | "
                 f"{r['min_climb_detour_pct']:+.0f}% | "
                 f"{r['min_climb_gain_saved_pct']:.0f}% | "
                 f"{r['shortest_gain_ft']:.0f} ft | "
                 f"{r['min_climb_gain_ft']:.0f} ft | "
                 f"{r['grade_averse_max_grade_pct']:.1f}% |")

    L += ["", "## Elevation model checks", "",
          "| Configuration | Filbert St | Jones St | 22nd St | Bradford St | "
          "Embarcadero climb/km | Valencia climb/km | Network climb/km | "
          "DEM RMS vs 1/3\" |", "|---|---|---|---|---|---|---|---|---|"]
    for tag, r in df.iterrows():
        L.append(f"| {tag} | {r['grade_Filbert Street']:.1f}% | "
                 f"{r['grade_Jones Street']:.1f}% | {r['grade_22nd Street']:.1f}% | "
                 f"{r['grade_Bradford Street']:.1f}% | "
                 f"{r['gainkm_The Embarcadero']:.1f} m | "
                 f"{r['gainkm_Valencia Street']:.1f} m | "
                 f"{r['network_gain_per_km']:.1f} m | "
                 f"{r.get('dem_rms_m', float('nan')):.2f} m |")
    L += ["", "Published: Filbert 31.5%, Jones 29%, 22nd 31.5%, Bradford 41%.", ""]

    L += ["## Corridors, passes and the Wiggle", "",
          "| Configuration | Corridors found | Top-12 overlap with baseline | "
          "Top corridor | Top pass | Wiggle excess climb (flat / shortest) |",
          "|---|---|---|---|---|---|"]
    for tag, r in df.iterrows():
        jac = _jaccard(r["top_corridors"], base["top_corridors"]) if base is not None else 1.0
        top = (r["top_corridors"][0].split(" - ")[0] if r["top_corridors"] else "")
        L.append(f"| {tag} | {int(r['corridor_count'])} | {100*jac:.0f}% | "
                 f"{top} ({r['top_corridor_km']:.1f} km) | "
                 f"{r.get('top_pass_nbhd','')} {r.get('top_pass_ft', float('nan')):.0f} ft, "
                 f"{int(r.get('top_pass_pairs', 0))} pairs | "
                 f"{r.get('wiggle_excess_flat_m', float('nan')):.1f} m / "
                 f"{r.get('wiggle_excess_shortest_m', float('nan')):.1f} m |")

    if base is not None and len(df) > 1:
        others = df.drop(index="baseline")
        L += ["", "## Reading it", "",
              f"- The headline trade (extra distance for climbing avoided on the "
              f"flattest route) ranges from "
              f"{others['min_climb_detour_pct'].min():+.0f}% / "
              f"{others['min_climb_gain_saved_pct'].min():.0f}% to "
              f"{others['min_climb_detour_pct'].max():+.0f}% / "
              f"{others['min_climb_gain_saved_pct'].max():.0f}% across every "
              f"perturbation, against {base['min_climb_detour_pct']:+.0f}% / "
              f"{base['min_climb_gain_saved_pct']:.0f}% at baseline.",
              f"- The top-12 corridor set keeps at least "
              f"{100*min(_jaccard(r['top_corridors'], base['top_corridors']) for _, r in others.iterrows()):.0f}% "
              f"overlap with the baseline under every perturbation.",
              f"- The dominant pass is in {base.get('top_pass_nbhd','')} in "
              f"{int((df['top_pass_nbhd'] == base.get('top_pass_nbhd')).sum())} of "
              f"{len(df)} configurations.",
              f"- The Wiggle is discovered as a corridor in "
              f"{int(df['wiggle_discovered'].fillna(False).sum())} of {len(df)} "
              f"configurations, and its flat route always wastes less climbing "
              f"than the shortest one: worst case "
              f"{df['wiggle_excess_flat_m'].max():.1f} m against "
              f"{df['wiggle_excess_shortest_m'].min():.1f} m.", ""]
    SENS_MD.write_text("\n".join(L))
    log.info("wrote %s and %s", SENS_CSV.name, SENS_MD.name)
