"""End-to-end test of the route page (the shareable map).

Drives ``outputs/sf_flat_route_finder.html`` in a headless browser: the page
must load without errors, route its default trip, answer searches for an
intersection, an address and a park, and produce a route family whose ends
are what the slider labels promise -- the left end is the true shortest
path and the right end has the least climbing. One member is also checked
against Python under the same scaled weights, so the slider positions
cannot drift from the analysis.

Skipped unless Playwright, a Chromium build and a built page are present.
"""
from __future__ import annotations

import glob
import os

import pytest

from sf_flat_routes.config import PROCESSED_DIR
from sf_flat_routes.viz_interactive import SIMPLE_HTML

playwright = pytest.importorskip("playwright.sync_api",
                                 reason="playwright is not installed")


def _chromium() -> str | None:
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                    os.path.expanduser(
                        "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


pytestmark = [
    pytest.mark.skipif(not SIMPLE_HTML.exists(),
                       reason="route page not built; run `python -m sf_flat_routes map`"),
    pytest.mark.skipif(not (PROCESSED_DIR / "edges_directed.parquet").exists(),
                       reason="processed data not built"),
]

_SEARCHES = ["24th & mission", "1234 valencia", "golden gate park", "ferry building",
             "church st and 24th st", "coit tower", "ocean beach", "caltrain"]

_SCRIPT = """(queries) => {
    const fam = App.family, g = App.graph;
    const members = fam.unique.map(u => ({
        id: u.id, distance_m: u.stats.distance_m, gain: u.stats.elev_gain_m,
        arcs: u.arcs.map(a => [g.arcEdge[a], (g.arcFlags[a] & 4) ? 1 : 0]),
        cost_shortest: g.pathCost(u.arcs, App.state.mode, App.weights(0)),
        cost_half: g.pathCost(u.arcs, App.state.mode, App.weights(App.lambdas[App.stepAt(0.5)])),
    }));
    const search = {};
    for (const q of queries) search[q] = App.index.search(q).map(r => [r.name, r.kind]);
    const sl = document.getElementById('sl');
    sl.value = 0; sl.dispatchEvent(new Event('input'));
    const atZero = App.shown.id;
    sl.value = 1; sl.dispatchEvent(new Event('input'));
    const atOne = App.shown.id;
    sl.value = 0.5; sl.dispatchEvent(new Event('input'));
    const half = App.shown.id;
    return {
        from: App.state.from, to: App.state.to, steps: fam.steps, members,
        lambdaHalf: App.lambdas[App.stepAt(0.5)],
        search, atZero, atOne, half, hash: location.hash,
        places: App.index.places.length, intersections: App.index.intersections.length,
        hasAddresses: !!App.index.addr,
    };
}"""


@pytest.fixture(scope="module")
def page_results():
    from playwright.sync_api import sync_playwright
    errors: list = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=_chromium(),
                                         args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:                            # pragma: no cover
            pytest.skip(f"no usable Chromium: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and "fonts.g" not in m.text
                and "ERR_" not in m.text else None)
        page.goto(SIMPLE_HTML.resolve().as_uri(), wait_until="load", timeout=240_000)
        page.wait_for_function(
            "window.App && App.family && !document.getElementById('result').hidden",
            timeout=240_000)
        out = page.evaluate(_SCRIPT, _SEARCHES)
        browser.close()
    return out, errors


def test_the_page_loads_and_routes_its_default_trip(page_results):
    out, errors = page_results
    assert not errors, errors[:4]
    assert out["from"] and out["to"]
    assert len(out["members"]) >= 2, "the default trip should offer a real choice"
    assert len(out["steps"]) == 17          # 16 lambda stops plus the min-climb anchor


def test_search_finds_intersections_addresses_and_places(page_results):
    out, _ = page_results
    s = out["search"]
    assert out["intersections"] > 5000 and out["places"] > 5000 and out["hasAddresses"]
    assert s["24th & mission"][0] == ["24th Street & Mission Street", "intersection"]
    assert s["church st and 24th st"][0][1] == "intersection"
    assert s["1234 valencia"][0] == ["1234 Valencia St", "address"]
    assert s["golden gate park"][0] == ["Golden Gate Park", "park"]
    assert s["coit tower"][0] == ["Coit Tower", "viewpoint"]
    assert s["ocean beach"][0] == ["Ocean Beach", "beach"]
    assert any(n == "Caltrain" and k == "station" for n, k in s["caltrain"])
    assert any("Ferry Building" in n for n, _ in s["ferry building"])


def test_the_slider_ends_are_the_shortest_and_the_flattest(page_results):
    out, _ = page_results
    m = {u["id"]: u for u in out["members"]}
    first, last = m[out["steps"][0]], m[out["steps"][-1]]
    assert first["distance_m"] <= min(u["distance_m"] for u in m.values()) + 1e-6
    assert last["gain"] <= min(u["gain"] for u in m.values()) + 1e-6
    assert out["atZero"] == out["steps"][0] and out["atOne"] == out["steps"][-1]
    # the family is deduplicated: no two members share an arc sequence
    seqs = [tuple(map(tuple, u["arcs"])) for u in m.values()]
    assert len(set(seqs)) == len(seqs)
    # and each member is the best of the family under its own weights
    assert first["cost_shortest"] <= min(u["cost_shortest"] for u in m.values()) + 1e-6
    half = m[out["half"]]
    assert half["cost_half"] <= min(u["cost_half"] for u in m.values()) + 1e-6


def test_the_share_link_carries_the_trip(page_results):
    out, _ = page_results
    h = out["hash"]
    # one bare token: the artifact host passes nothing else through
    assert h.startswith("#t~") and "~0.500~" in h
    assert all(c.isalnum() or c in "._~-" for c in h[1:]), h


def test_a_slider_position_matches_python(page_results):
    """The browser's route at the slider's midpoint costs no more, under
    Python's own evaluation of the same scaled weights, than Python's route
    between the same two nodes."""
    from sf_flat_routes.config import ROUTING_PROFILES, with_scale
    from sf_flat_routes.pipeline import build_context
    from sf_flat_routes.routing import route
    from sf_flat_routes.utils import configure_gdal_for_proxy

    out, _ = page_results
    half = next(u for u in out["members"] if u["id"] == out["half"])
    configure_gdal_for_proxy()
    ctx = build_context(modes=("walk",))
    graph = ctx.graphs["walk"]
    w = with_scale(ROUTING_PROFILES["balanced"], out["lambdaHalf"])
    cost = graph.build_costs(w)
    t = graph.table
    key = {(int(e), d): i for i, (e, d) in enumerate(zip(t["edge_id"], t["direction"]))}
    arcs = [key[(e, "rev" if r else "fwd")] for e, r in half["arcs"]]
    js_cost = float(cost[arcs].sum())
    src = t["from_node"].iloc[arcs[0]]
    dst = t["to_node"].iloc[arcs[-1]]
    py_arcs, _ = route(graph, src, dst, w, arc_cost=cost)
    py_cost = float(cost[py_arcs].sum())
    # quantisation in the packed graph is ~5 cm per arc
    assert js_cost <= py_cost + 0.05 * len(arcs) + 1.0
    assert abs(js_cost - half["cost_half"]) < 0.02 * js_cost + 5.0
