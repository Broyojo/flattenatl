"""Integration checks against the real processed data.

These are skipped automatically when the pipeline has not been run, so the
unit suite stays runnable on a fresh clone.
"""
import numpy as np
import pandas as pd
import pytest

from sf_flat_routes.config import MIN_RELIABLE_GRADE_LENGTH_M, PROCESSED_DIR

EDGES = PROCESSED_DIR / "edges_metrics.parquet"
DIRECTED = PROCESSED_DIR / "edges_directed.parquet"
PAIRS = PROCESSED_DIR / "neighborhood_pairs.parquet"

pytestmark = pytest.mark.skipif(
    not (EDGES.exists() and DIRECTED.exists()),
    reason="processed data not built; run `python -m sf_flat_routes all`")


@pytest.fixture(scope="module")
def edges():
    import geopandas as gpd
    return gpd.read_parquet(EDGES)


@pytest.fixture(scope="module")
def directed():
    return pd.read_parquet(DIRECTED)


def test_every_edge_has_two_directions(edges, directed):
    assert len(directed) == 2 * len(edges)
    counts = directed.groupby("edge_id").size()
    assert counts.min() == 2 and counts.max() == 2


def test_gain_and_loss_mirror_between_directions(directed):
    piv = directed.pivot_table(index="edge_id", columns="direction",
                               values=["cum_gain", "cum_loss"])
    assert np.allclose(piv[("cum_gain", "fwd")], piv[("cum_loss", "rev")],
                       atol=1e-6)
    assert np.allclose(piv[("cum_loss", "fwd")], piv[("cum_gain", "rev")],
                       atol=1e-6)


def test_net_change_is_antisymmetric(directed):
    piv = directed.pivot_table(index="edge_id", columns="direction",
                               values="net_change")
    assert np.allclose(piv["fwd"], -piv["rev"], atol=1e-6)


def test_gain_minus_loss_equals_net_change(directed):
    """Exact per-edge identity, guaranteed by protecting edge boundaries
    from the dead-band pruning."""
    d = directed
    assert np.allclose(d["cum_gain"] - d["cum_loss"], d["net_change"], atol=1e-3)


def test_no_edge_exceeds_the_plausible_grade_clip(edges):
    from sf_flat_routes.config import ELEVATION
    assert edges["max_abs_grade"].max() <= ELEVATION.max_plausible_grade + 1e-9


def test_elevations_are_in_a_sane_range_for_atlanta(edges):
    # the Chattahoochee leaves the city at about 229 m and the highest ground
    # is about 330 m; a tile in the wrong datum or a nodata leak shows here
    assert edges["elev_max"].max() < 345
    assert edges["elev_min"].min() > 215


def test_distance_above_thresholds_are_nested_and_bounded(directed):
    ths = [3, 5, 8, 10, 15]
    for a, b in zip(ths[:-1], ths[1:]):
        assert (directed[f"d_above_{a}"] >= directed[f"d_above_{b}"] - 1e-6).all()
    # tolerance covers float accumulation: interval lengths are summed,
    # while length_m comes from the geometry
    assert (directed["d_above_3"] <= directed["length_m"] * (1 + 1e-6) + 1e-3).all()


def test_stairways_are_never_bicycle_traversable(directed):
    steps = directed[directed["cls"] == "steps"]
    assert len(steps) > 0
    assert not steps["bike_traversable"].any()


def test_known_flat_and_steep_streets_are_correctly_separated(edges):
    def mean_grade(names):
        sub = edges[edges["name"].isin(names)]
        w = sub["length_m"].to_numpy()
        return float((sub["avg_grade_fwd"].abs().to_numpy() * w).sum() / w.sum())

    # old railway grades against the steepest sustained street in the city
    beltline = mean_grade(["Atlanta Beltline Eastside Trail", "Atlanta Beltline Westside Trail"])
    creek = mean_grade(["Proctor Creek Greenway"])
    steep = mean_grade(["Mary George Avenue Northwest"])
    assert beltline < 0.02, f"the BeltLine is a railway grade, got {beltline:.1%}"
    assert creek < 0.015, f"the Proctor Creek Greenway should be level, got {creek:.1%}"
    assert steep > 0.05, f"Mary George Avenue should be steep, got {steep:.1%}"
    assert steep > 3 * beltline


def test_a_street_under_a_freeway_bridge_has_no_phantom_hump(edges):
    """Windsor Street passes under I-20 on the level. Before crossings were
    bridged (network.find_dem_gaps) the bare-earth surface under the deck,
    interpolated from the embankments, gave each block 11 m of climbing."""
    sub = edges[(edges["name"] == "Windsor Street Southwest") & (edges["cls"] == "tertiary")]
    c = sub.geometry.centroid
    near = sub[(c.y - 3736943).abs().lt(30).to_numpy()]       # the two carriageways at I-20
    assert len(near) == 2
    assert near["cum_gain_fwd"].max() < 2.0, near[["length_m", "cum_gain_fwd"]]
    assert near["max_abs_grade"].max() < 0.05


def test_bridge_approaches_do_not_fall_off_a_ledge(edges):
    """No drivable arterial block touching a flagged bridge keeps the 30%+
    drop that Northside Drive, Lakewood Avenue and Ivan Allen Jr Boulevard
    showed where the mapped bridge end sits out over the cut."""
    nodes = set(edges.loc[edges["is_structure"], "u"]) | set(edges.loc[edges["is_structure"], "v"])
    appr = edges[~edges["is_structure"] & (edges["u"].isin(nodes) | edges["v"].isin(nodes))
                 & edges["cls"].isin(["trunk", "primary", "secondary", "tertiary"])
                 & (edges["length_m"] >= MIN_RELIABLE_GRADE_LENGTH_M)]
    assert len(appr) > 300
    assert (appr["max_abs_grade"] >= 0.30).mean() < 0.005


@pytest.mark.skipif(not PAIRS.exists(), reason="pair analysis not run")
def test_no_objective_beats_the_shortest_path_on_distance():
    p = pd.read_parquet(PAIRS)
    assert (p["detour_ratio"] >= 0.999).all()


@pytest.mark.skipif(not PAIRS.exists(), reason="pair analysis not run")
def test_climb_averse_objectives_actually_reduce_climbing():
    p = pd.read_parquet(PAIRS)
    means = p.groupby("profile")["elev_gain_m"].mean()
    assert means["min_climb"] < means["shortest"]
    assert means["balanced"] < means["shortest"]


@pytest.mark.skipif(not PAIRS.exists(), reason="pair analysis not run")
def test_grade_averse_lowers_maximum_gradient_most(p_=None):
    p = pd.read_parquet(PAIRS)
    means = p.groupby("profile")["max_grade"].mean()
    assert means["grade_averse"] < means["min_climb"]
    assert means["grade_averse"] < means["shortest"]


@pytest.mark.skipif(not PAIRS.exists(), reason="pair analysis not run")
def test_route_metrics_are_internally_consistent():
    p = pd.read_parquet(PAIRS)
    assert (p["elev_gain_m"] >= 0).all() and (p["elev_loss_m"] >= 0).all()
    net = p["elev_gain_m"] - p["elev_loss_m"]
    endpoint_net = p["end_elev_m"] - p["start_elev_m"]
    assert np.allclose(net, endpoint_net, atol=0.05)
