"""End-to-end test of the route page (the shareable map).

Drives ``outputs/atl_flat_route_finder.html`` in a headless browser: the page
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
from sf_flat_routes.viz_interactive import SIMPLE_HTML, SITE_INDEX

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

_SEARCHES = ["10th & peachtree", "675 ponce de leon", "piedmont park", "ponce city market",
             "boulevard and north ave", "five points", "grant park", "arts center",
             "peachtree st ne & 10th st ne", "10th street northeast and peachtree street northeast",
             "moreland ave & euclid ave", "ponce de leon ave",
             "650 Ponce De Leon Ave NE, Atlanta, GA 30308", "22 14th St NW",
             "675 Ponce de Leon Ave NE Atlanta GA", "600 mlk jr dr sw", "whole foods"]

_SCRIPT = """(queries) => {
    const fam = App.family, g = App.graph;
    const alphas = [0, 14, 120];
    const members = fam.unique.map(u => ({
        id: u.id, distance_m: u.stats.distance_m, gain: u.stats.elev_gain_m,
        arcs: u.arcs.map(a => [g.arcEdge[a], (g.arcFlags[a] & 4) ? 1 : 0]),
        costs: alphas.map(a => g.pathCost(u.arcs, App.state.mode, App.weights(a))),
    }));
    const search = {};
    for (const q of queries) search[q] = App.index.search(q).map(r => [r.name, r.kind]);
    // Georgia Tech is nearer the 14th Street Whole Foods than any other
    const gt = [-84.39881, 33.77609];
    const nearest = App.index.search('whole foods', 8, gt).map(r => [r.name, r.where]);
    const sl = document.getElementById('sl');
    sl.value = 0; sl.dispatchEvent(new Event('input'));
    const atZero = App.shown.id;
    sl.value = 1; sl.dispatchEvent(new Event('input'));
    const atOne = App.shown.id;
    sl.value = 0.5; sl.dispatchEvent(new Event('input'));
    const half = App.shown.id;
    return {
        from: App.state.from, to: App.state.to, members, alphas,
        search, nearest, atZero, atOne, half, hash: location.hash,
        places: App.index.places.length, intersections: App.index.intersections.length,
        hasAddresses: !!App.index.addr,
        frontier: { solutions: App._search.solutions.length, labels: App._search.labels,
                    expanded: App._search.expanded, truncated: App._search.truncated },
        snap: (() => { const p = App.pointAt(-84.25, 33.775); const g = App.graph;
            return p ? { node: p.node, pinLon: p.lon, pinLat: p.lat,
                         nodeLon: g.nodeLon(p.node), nodeLat: g.nodeLat(p.node) } : { node: -1 }; })(),
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
            "window.App && App.family && !App.family.partial"
            " && !document.getElementById('result').hidden",
            timeout=240_000)
        out = page.evaluate(_SCRIPT, _SEARCHES)
        # change the destination without touching the slider: the line on
        # the map must be the new trip's, not the old one's
        page.evaluate("""() => {
            const hit = App.index.search('piedmont park')[0];
            window._hit = hit;
            App.setPoint('to', App.pointAt(hit.lon, hit.lat, hit.name), false);
            App.recompute('auto');
        }""")
        page.wait_for_function("App.family && !App.family.partial", timeout=120_000)
        out["retarget"] = page.evaluate("""() => {
            const hit = window._hit, before = 0;
            const u = App.shown, line = App._line.getLatLngs();
            const end = line[line.length - 1];
            return { before, after: line.length, sameAsShown: line.length === u.latlngs.length,
                     member: App.family.unique.includes(u),
                     endsAtTarget: Math.abs(end.lat - hit.lat) < 0.004 && Math.abs(end.lng - hit.lon) < 0.004 };
        }""")
        # bike mode, Boulevard at Memorial Drive to Boulevard at Ponce de
        # Leon: with calm streets on the ride goes up Parkway Drive and
        # Jackson Street (no bikeway, but quiet); off, straight up
        # Boulevard, the shortest line and a busy arterial
        page.evaluate("""() => {
            document.querySelector('#mode button[data-v=bike]').click();
            App.setPoint('from', App.pointAt(-84.3684, 33.7465, 'Boulevard & Memorial'), false);
            App.setPoint('to', App.pointAt(-84.3718, 33.7738, 'Boulevard & Ponce'), false);
            App.recompute('auto');
        }""")
        page.wait_for_function("App.family && !App.family.partial", timeout=120_000)
        streets = """() => {
            const g = App.graph, u = App.family.shortest, km = {};
            for (const a of u.arcs) {
                const n = App.geom.edgeInfo(g.arcEdge[a]).name || '?';
                km[n] = (km[n] || 0) + g.arcLen[a] / g.DM;
            }
            return { streets: km, distance_m: u.stats.distance_m, stress_m: u.stats.stress_m,
                     calm: App.state.calm, token: App.token(), n: App.family.unique.length,
                     rowHidden: document.getElementById('calmrow').hidden,
                     monotone: App.family.unique.every((m, i, arr) => i === 0
                        || (m.stats[App.lenKey()] >= arr[i - 1].stats[App.lenKey()] - 1e-6
                            && m.stats.elev_gain_m <= arr[i - 1].stats.elev_gain_m + 1e-6)) };
        }"""
        out["calm_on"] = page.evaluate(streets)
        page.evaluate("() => document.getElementById('calm').click()")
        page.wait_for_function("App.family && !App.family.partial && !App.state.calm", timeout=120_000)
        out["calm_off"] = page.evaluate(streets)
        # loop mode: the engine's loops, then the toggle, then a shared link
        out["loops"] = page.evaluate("""() => {
            const g = App.graph, src = App.nearestNode(-84.37189, 33.78905);   // Piedmont Park
            const s = g.loops(src, 'walk', { targetM: 5 * 1609.344 });
            while (!s.step(1e9)) {}
            const tail = g.reverse().tail;
            return { src, ms: s.ms, accepted: s.accepted.length, median: s.medianGain,
              loops: s.loops.map(r => ({ length: r.length, gain: r.gain, overlap: r.overlap, round: r.round,
                first: tail[r.arcs[0]], last: g.head[r.arcs[r.arcs.length - 1]],
                connected: r.arcs.every((a, i) => i === 0 || tail[a] === g.head[r.arcs[i - 1]]) })) };
        }""")
        out["outback"] = page.evaluate("""() => {
            const g = App.graph, src = App.nearestNode(-84.36405, 33.75652);   // Krog Street Market
            const run = (opts) => { const s = g.loops(src, 'walk', Object.assign({ targetM: 4 * 1609.344 }, opts));
              while (!s.step(1e9)) {} return s.loops.map(r => ({ gain: r.gain, kind: r.kind, overlap: r.overlap, length: r.length })); };
            return { loops: run({}), ob: run({ outBack: true }) };
        }""")
        page.evaluate("""() => {
            document.querySelector('#mode button[data-v=walk]').click();
            App.setPoint('from', App.pointAt(-84.39881, 33.77609, 'Georgia Tech'), false);
            App.setPoint('to', App.pointAt(-84.36405, 33.75652, 'Krog Street Market'), false);
            App.recompute('auto');
        }""")
        page.wait_for_function("App.family && !App.family.partial", timeout=120_000)
        page.click("#loopbtn")
        page.wait_for_function("App.family && App.family.loop", timeout=60_000)
        page.wait_for_timeout(400)
        out["loop_ui"] = page.evaluate("""() => ({
            looping: document.getElementById('card').classList.contains('looping'),
            toWidth: document.getElementById('tofield').getBoundingClientRect().width,
            pressed: document.getElementById('loopbtn').getAttribute('aria-pressed'),
            slMax: +document.getElementById('sl').max, hash: location.hash,
            stats: App.shown.stats.distance_m, target: App.family.targetM,
            delta: document.getElementById('delta').textContent,
            markers: App.markers.getLayers().length })""")
        page.click("#loopbtn")
        page.wait_for_function("App.family && !App.family.loop && !App.family.partial", timeout=120_000)
        page.wait_for_timeout(400)
        out["loop_off"] = page.evaluate("""() => ({ to: document.getElementById('to').value,
            toWidth: document.getElementById('tofield').getBoundingClientRect().width,
            hash: location.hash, slMax: +document.getElementById('sl').max })""")
        # a shared loop link reopens the same loop
        page.goto(SIMPLE_HTML.resolve().as_uri() + "#l~-84.37189~33.78905~w~3.5~1~Piedmont_20Park",
                  wait_until="load", timeout=240_000)
        page.reload(wait_until="load", timeout=240_000)
        page.wait_for_function("window.App && App.family && App.family.loop", timeout=240_000)
        out["loop_link"] = page.evaluate("""() => ({ loop: App.state.loop, mi: App.state.loopMi,
            idx: App.state.loopIdx, from: document.getElementById('from').value,
            n: App.family.unique.length, slVal: +document.getElementById('sl').value })""")
        out["cameras"] = _drive_cameras(page)
        out["crime"] = _drive_crime(page)
        browser.close()
    return out, errors


_CAM_STATE = """() => { const g = App.graph, C = App.cameras, f = App.family.unique;
    return { avoid: App.state.avoid, n: f.length, cams: f.map(u => u.cams),
        watched: f.map(u => u.arcs.filter(a => C.mask[g.arcEdge[a]]).length),
        opened: f.map(u => u.arcs.filter(a => C.mask[g.arcEdge[a]] && (g.arcFlags[a] & 1)).length),
        miles: f.map(u => u.stats.distance_m / 1609.344), gain: f.map(u => u.stats.elev_gain_m),
        delta: document.getElementById('delta').textContent, token: App.token(),
        rings: App._touchLayer ? App._touchLayer.getLayers().length : 0,
        shownCams: App.shown.camIds.slice() }; }"""


_CRIME_STATE = """() => { const g = App.graph, f = App.family.unique;
    return { safe: App.state.safe, avoid: App.state.avoid, n: f.length, hot: f.map(u => u.hotM), cams: f.map(u => u.cams),
        miles: f.map(u => u.stats.distance_m / 1609.344), gain: f.map(u => u.stats.elev_gain_m),
        delta: document.getElementById('delta').textContent, token: App.token(),
        marks: App._touchLayer ? App._touchLayer.getLayers().length : 0 }; }"""


def _drive_crime(page) -> dict:
    """The default trip, and one that ends inside the high-crime blocks."""
    ready = "App.family && !App.family.partial"
    page.goto("about:blank")
    page.goto(SIMPLE_HTML.resolve().as_uri(), wait_until="load", timeout=240_000)
    page.wait_for_function("window.App && " + ready
                           + " && !document.getElementById('result').hidden", timeout=240_000)
    out = {"row": page.evaluate("""() => ({ shown: !document.getElementById('saferow').hidden,
        note: document.getElementById('safenote').textContent,
        closed: App.crimeMask.reduce((a, b) => a + b, 0), edges: App.crimeMask.length })""")}
    out["default_off"] = page.evaluate(_CRIME_STATE)
    page.click("#safe")
    page.wait_for_function("App.state.safe && " + ready, timeout=120_000)
    page.wait_for_timeout(600)
    out["default_on"] = page.evaluate(_CRIME_STATE)
    # Georgia Tech to Five Points: the station is in the middle of them
    trip = """(on) => { if (App.state.safe !== on) document.getElementById('safe').click();
        App.setPoint('from', App.pointAt(-84.39881, 33.77609, 'Georgia Tech'), false);
        App.setPoint('to', App.pointAt(-84.3916, 33.75389, 'Five Points'), false);
        App.recompute('auto'); }"""
    for on, key in ((False, "downtown_off"), (True, "downtown_on")):
        page.evaluate(trip, on)
        page.wait_for_function("(on) => App.state.safe === on && " + ready, arg=on, timeout=120_000)
        page.wait_for_timeout(600)
        out[key] = page.evaluate(_CRIME_STATE)
    page.click("#cams")                                  # and cameras as well
    page.wait_for_function("App.state.avoid && " + ready, timeout=120_000)
    page.wait_for_timeout(600)
    out["both"] = page.evaluate(_CRIME_STATE)
    return out


def _drive_cameras(page) -> dict:
    """The default trip and one with no camera-free way, box off and on."""
    ready = "App.family && !App.family.partial"
    page.goto(SIMPLE_HTML.resolve().as_uri(), wait_until="load", timeout=240_000)
    page.reload(wait_until="load", timeout=240_000)
    page.wait_for_function("window.App && " + ready
                           + " && !document.getElementById('result').hidden", timeout=240_000)
    out = {"model": page.evaluate("""() => { const C = App.cameras, s = App.camSectors;
        const north = { sectors: s('0') }, round = { sectors: s('') };
        return { row: !document.getElementById('camrow').hidden, note: document.getElementById('camnote').textContent,
            n: C.list.length, watching: C.watching, masked: C.mask.reduce((a, b) => a + b, 0), edges: C.mask.length,
            sectors: [s('180'), s('70;340'), s('270-315'), s('350-10'), s('NW'), s(''), s('bogus')],
            ahead: C.sees(north, 0, 30), behind: C.sees(north, 0, -30), under: C.sees(north, 0, -8),
            beyond: C.sees(north, 0, 50), side: C.sees(north, 30, 5), anyway: C.sees(round, -25, -25) }; }""")}
    out["default_off"] = page.evaluate(_CAM_STATE)
    page.click("#cams")
    page.wait_for_function("App.state.avoid && " + ready, timeout=120_000)
    page.wait_for_timeout(600)
    out["default_on"] = page.evaluate(_CAM_STATE)
    # Five Points to Lenox Square: every way north out of Buckhead's grid
    # into the mall is watched by something
    trip = """(on) => { if (App.state.avoid !== on) document.getElementById('cams').click();
        App.setPoint('from', App.pointAt(-84.3916, 33.75389, 'Five Points'), false);
        App.setPoint('to', App.pointAt(-84.3622, 33.84652, 'Lenox Square'), false);
        App.recompute('auto'); }"""
    for on, key in ((False, "lenox_off"), (True, "lenox_on")):
        page.evaluate(trip, on)
        page.wait_for_function("(on) => App.state.avoid === on && " + ready, arg=on, timeout=120_000)
        page.wait_for_timeout(600)
        out[key] = page.evaluate(_CAM_STATE)
    # a link made with the box ticked reopens with it ticked
    token = out["lenox_on"]["token"]
    page.goto(SIMPLE_HTML.resolve().as_uri() + "#" + token, wait_until="load", timeout=240_000)
    page.reload(wait_until="load", timeout=240_000)
    page.wait_for_function("window.App && " + ready, timeout=240_000)
    out["link"] = page.evaluate("""() => ({ avoid: App.state.avoid, checked: document.getElementById('cams').checked,
        cams: App.family.unique.map(u => u.cams), token: App.token() })""")
    return out


def test_loops_close_on_themselves_and_are_flat(page_results):
    out, _ = page_results
    r = out["loops"]
    assert r["accepted"] >= 10 and len(r["loops"]) >= 2, r
    target = 5 * 1609.344
    for lp in r["loops"]:
        assert lp["first"] == r["src"] and lp["last"] == r["src"] and lp["connected"], lp
        assert abs(lp["length"] - target) <= 0.12 * target, lp
        assert lp["overlap"] <= 0.3 and lp["round"] >= 0.2, lp
    # the flattest loop climbs under what a typical loop from here does
    # (by less than in San Francisco, where it was a quarter: Piedmont Park
    # has rolling ground on every side and no promenade to hide on)
    assert r["loops"][0]["gain"] < 0.8 * r["median"], r
    assert r["ms"] < 5000, r


def test_out_and_backs_are_offered_only_when_allowed_and_are_flatter(page_results):
    out, _ = page_results
    r = out["outback"]
    assert all(lp["kind"] != "outback" for lp in r["loops"])
    best = r["ob"][0]
    # from Krog Street the flattest run is the BeltLine there and back
    # (overlap counts the second pass over a street, so there and back is 0.5)
    assert best["kind"] == "outback" and best["overlap"] > 0.4, best
    assert best["gain"] < 0.85 * r["loops"][0]["gain"], (best, r["loops"][0])
    assert abs(best["length"] - 4 * 1609.344) <= 0.25 * 1609.344


def test_the_loop_button_folds_the_destination_away_and_back(page_results):
    out, errors = page_results
    on, off, link = out["loop_ui"], out["loop_off"], out["loop_link"]
    assert not errors, errors[:4]
    assert on["looping"] and on["pressed"] == "true" and on["toWidth"] < 2
    assert on["slMax"] == 15 and on["hash"].startswith("#l~")
    assert on["markers"] == 1
    assert abs(on["stats"] - on["target"]) <= 0.15 * on["target"]
    assert "loop" in on["delta"]
    assert off["to"] == "Krog Street Market" and off["toWidth"] > 100
    assert off["slMax"] == 1 and off["hash"].startswith("#t~")
    assert link["loop"] and link["mi"] == 3.5 and link["from"] == "Piedmont Park"
    assert link["slVal"] == 3.5 and link["idx"] == min(1, link["n"] - 1)


def test_calm_streets_keep_a_bike_off_boulevard(page_results):
    out, _ = page_results
    on, off = out["calm_on"], out["calm_off"]
    assert on["calm"] and not off["calm"]
    assert not on["rowHidden"]
    assert on["token"].split("~")[5] == "b" and off["token"].split("~")[5] == "bx"
    assert off["streets"].get("Boulevard Northeast", 0) > 1500
    assert on["streets"].get("Boulevard Northeast", 0) < 300
    assert on["streets"].get("Parkway Drive Northeast", 0) > 1000
    # calm costs a little real distance and buys a lot of comfort
    assert on["distance_m"] < off["distance_m"] * 1.15
    assert on["stress_m"] < off["stress_m"]
    # the family stays a frontier in the units it was searched in
    assert on["monotone"] and off["monotone"]
    assert on["n"] >= 2 and off["n"] >= 2


def test_the_page_loads_and_routes_its_default_trip(page_results):
    out, errors = page_results
    assert not errors, errors[:4]
    assert out["from"] and out["to"]
    assert len(out["members"]) >= 2, "the default trip should offer a real choice"
    f = out["frontier"]
    assert f["solutions"] >= 2 and not f["truncated"], f
    assert f["labels"] < 4_000_000, f


def test_search_finds_intersections_addresses_and_places(page_results):
    out, _ = page_results
    s = out["search"]
    assert out["intersections"] > 5000 and out["places"] > 5000 and out["hasAddresses"]
    corner = ["10th Street Northeast & Peachtree Street Northeast", "intersection"]
    assert s["10th & peachtree"][0] == corner
    assert s["boulevard and north ave"][0][1] == "intersection"
    # the avenue that has the number, not the cul-de-sac that shares its name
    assert s["675 ponce de leon"][0] == ["675 Ponce de Leon Avenue NE", "address"]
    assert s["piedmont park"][0] == ["Piedmont Park", "park"]
    assert s["grant park"][0] == ["Grant Park", "park"]
    assert s["five points"][0] == ["Five Points", "station"]
    assert any(n == "Arts Center" and k == "station" for n, k in s["arts center"])
    assert any("Ponce City Market" in n for n, _ in s["ponce city market"])
    # abbreviations, quadrants and full words match the same corners
    assert s["peachtree st ne & 10th st ne"][0] == corner
    assert s["10th street northeast and peachtree street northeast"][0] == corner
    assert s["moreland ave & euclid ave"][0][1] == "intersection"
    assert "Moreland Avenue Northeast" in s["moreland ave & euclid ave"][0][0]
    assert any(k == "intersection" and "Ponce de Leon Avenue" in n
               for n, k in s["ponce de leon ave"])


def test_addresses_are_found_the_way_people_type_them(page_results):
    out, _ = page_results
    s = out["search"]
    # pasted from somewhere else, with the city, state and zip on the end
    assert s["650 Ponce De Leon Ave NE, Atlanta, GA 30308"][0] == \
        ["650 Ponce de Leon Avenue NE", "address"]
    assert s["675 Ponce de Leon Ave NE Atlanta GA"][0] == ["675 Ponce de Leon Avenue NE", "address"]
    # a house number followed by a numbered street
    assert s["22 14th St NW"][0] == ["22 14th Street NW", "address"]
    # the abbreviation everybody uses
    assert "Martin Luther King Jr Drive SW" in s["600 mlk jr dr sw"][0][0]


def test_every_branch_of_a_chain_is_offered_nearest_first(page_results):
    out, _ = page_results
    assert sum(n == "Whole Foods Market" for n, _ in out["search"]["whole foods"]) == 4
    names, wheres = zip(*out["nearest"])
    assert names[:4] == ("Whole Foods Market",) * 4
    assert len(set(wheres[:4])) == 4, wheres          # each says where it is
    assert "14th St NW" in wheres[0], wheres          # nearest to Georgia Tech


def test_a_camera_sees_down_its_direction_and_a_little_all_round(page_results):
    m = page_results[0]["cameras"]["model"]
    assert m["sectors"][0] == [[180, 35]]
    assert m["sectors"][1] == [[70, 35], [340, 35]]
    assert m["sectors"][2] == [[292.5, 32.5]]            # a range, padded by 10 degrees
    assert m["sectors"][3] == [[0, 20]]                  # a range across north
    assert m["sectors"][4] == [[315, 35]]
    assert m["sectors"][5] == [] and m["sectors"][6] == []
    assert m["ahead"] and m["under"] and m["anyway"]
    assert not m["behind"] and not m["beyond"] and not m["side"]
    # the snapshot is loaded, and watches a small share of the city's blocks
    assert m["row"] and "DeFlock" in m["note"]
    assert m["n"] > 300 and 0.8 * m["n"] < m["watching"] <= m["n"]
    assert 0.01 < m["masked"] / m["edges"] < 0.10


def test_avoiding_cameras_comes_before_everything_else(page_results):
    """With the box ticked every route on the slider is clear of cameras,
    and shortest against flattest is only traded among those."""
    c = page_results[0]["cameras"]
    off, on = c["default_off"], c["default_on"]
    assert not off["avoid"] and on["avoid"]
    assert max(off["cams"]) >= 1, "the plain routes should pass a camera somewhere"
    # nothing about cameras is drawn until asked, and then nothing if none is passed
    assert off["rings"] == 0 and "In view of" in off["delta"] and "ringed" not in off["delta"]
    assert on["n"] >= 2 and set(on["cams"]) == {0} and set(on["watched"]) == {0}
    assert on["rings"] == 0 and "no camera" in on["delta"]
    # still a frontier: sliding right is never shorter and never climbs more
    for a, b in zip(on["miles"], on["miles"][1:]):
        assert b >= a - 1e-9
    for a, b in zip(on["gain"], on["gain"][1:]):
        assert b <= a + 1e-6
    # and it costs something: no camera-free route beats the plain shortest
    assert on["miles"][0] >= off["miles"][0] - 1e-9
    assert off["token"].split("~")[5] == "w" and on["token"].split("~")[5] == "wc"


def test_where_no_route_is_clear_every_route_passes_the_fewest(page_results):
    c = page_results[0]["cameras"]
    off, on = c["lenox_off"], c["lenox_on"]
    assert min(off["cams"]) >= 2
    # one count for the whole slider, and it is below anything the plain search found
    assert len(set(on["cams"])) == 1 and 1 <= on["cams"][0] < min(off["cams"])
    assert "cannot be avoided" in on["delta"]
    # the cameras passed are ringed, and nothing else on the route is watched
    # except the blocks that had to be opened for them
    assert on["rings"] >= 2 * len(on["shownCams"]) and len(on["shownCams"]) == on["cams"][-1]
    assert on["watched"] == on["opened"]
    # with the box off the same trip draws nothing, though it passes more
    assert off["rings"] == 0 and len(off["shownCams"]) > len(on["shownCams"])
    link = c["link"]
    assert link["avoid"] and link["checked"] and link["token"] == on["token"]
    assert set(link["cams"]) == set(on["cams"])


def test_high_crime_blocks_are_avoided_before_distance_and_climbing(page_results):
    c = page_results[0]["crime"]
    assert c["row"]["shown"] and "Atlanta Police" in c["row"]["note"]
    assert 0.02 < c["row"]["closed"] / c["row"]["edges"] < 0.15
    off, on = c["default_off"], c["default_on"]
    assert not off["safe"] and on["safe"]
    assert max(off["hot"]) > 100, "the plain routes should cross some high-crime blocks"
    assert on["n"] >= 2 and max(on["hot"]) == 0 and "Clear of high-crime blocks" in on["delta"]
    assert off["marks"] == 0 and on["marks"] == 0        # nothing to mark
    for a, b in zip(on["miles"], on["miles"][1:]):
        assert b >= a - 1e-9
    for a, b in zip(on["gain"], on["gain"][1:]):
        assert b <= a + 1e-6
    assert on["miles"][0] >= off["miles"][0] - 1e-9
    assert off["token"].split("~")[5] == "w" and on["token"].split("~")[5] == "ws"


def test_a_trip_into_high_crime_blocks_spends_the_least_distance_on_them(page_results):
    c = page_results[0]["crime"]
    off, on, both = c["downtown_off"], c["downtown_on"], c["both"]
    # the least any plain route spends there is more than every avoiding route does
    assert 0 < max(on["hot"]) < min(off["hot"])
    assert "cannot be avoided" in on["delta"] and on["marks"] >= 1 and off["marks"] == 0
    assert "through high-crime blocks" in off["delta"]
    # cameras and crime together: both letters on the link, neither made worse
    assert both["token"].split("~")[5] == "wcs"
    assert max(both["hot"]) <= max(on["hot"]) + 30 and max(both["cams"]) <= max(on["cams"])


def test_the_slider_ends_are_the_shortest_and_the_flattest(page_results):
    out, _ = page_results
    m = out["members"]
    first, last = m[0], m[-1]
    assert first["distance_m"] <= min(u["distance_m"] for u in m) + 1e-6
    assert last["gain"] <= min(u["gain"] for u in m) + 1e-6
    assert out["atZero"] == first["id"] and out["atOne"] == last["id"]
    assert 0 < out["half"] < len(m) - 1
    # the family is deduplicated: no two members share an arc sequence
    seqs = [tuple(map(tuple, u["arcs"])) for u in m]
    assert len(set(seqs)) == len(seqs)
    # and no member is dominated by another on both counts
    for a in m:
        for b in m:
            if a is b:
                continue
            assert not (b["distance_m"] <= a["distance_m"] - 1e-6
                        and b["gain"] <= a["gain"] - 1e-6), (a, b)


def test_the_family_is_monotone_in_distance_and_climbing(page_results):
    """Sliding right never shortens the route and never adds climbing: the
    defining property of a distance/climbing frontier sorted by distance,
    and the behaviour the slider's end labels promise."""
    out, _ = page_results
    seq = out["members"]
    for a, b in zip(seq, seq[1:]):
        assert b["distance_m"] >= a["distance_m"] - 1e-6
        assert b["gain"] <= a["gain"] + 1e-6


def test_a_new_trip_replaces_the_drawn_route_at_once(page_results):
    """Changing an endpoint redraws without the slider being touched."""
    out, _ = page_results
    r = out["retarget"]
    assert r["member"] and r["sameAsShown"] and r["endsAtTarget"], r


def test_a_click_outside_the_city_snaps_to_the_nearest_corner(page_results):
    """The pin and the route start must agree, even for a click far
    outside the street network."""
    out, _ = page_results
    r = out["snap"]
    assert r["node"] >= 0
    assert abs(r["pinLon"] - r["nodeLon"]) < 1e-9 and abs(r["pinLat"] - r["nodeLat"]) < 1e-9
    # Decatur, 4 km east of the city limits, lands on the city's eastern edge
    assert r["nodeLon"] > -84.30 and abs(r["nodeLat"] - 33.775) < 0.03


def test_the_share_link_carries_the_trip(page_results):
    out, _ = page_results
    h = out["hash"]
    # one bare token that survives any host or chat client
    assert h.startswith("#t~") and "~0.500~" in h
    assert all(c.isalnum() or c in "._~-" for c in h[1:]), h


def test_the_frontier_contains_every_weighted_optimum(page_results):
    """Every route a weighted sum length + alpha * climbing would choose is
    a frontier point, so for each alpha the family's best member must cost
    no more, under Python's own evaluation, than Python's route for that
    alpha between the same two nodes. This pins the browser's frontier
    search to the analysis's cost model."""
    from sf_flat_routes.config import ROUTING_PROFILES, with_alpha
    from sf_flat_routes.pipeline import build_context
    from sf_flat_routes.routing import route
    from sf_flat_routes.utils import configure_gdal_for_proxy

    out, _ = page_results
    configure_gdal_for_proxy()
    ctx = build_context(modes=("walk",))
    graph = ctx.graphs["walk"]
    t = graph.table
    key = {(int(e), d): i for i, (e, d) in enumerate(zip(t["edge_id"], t["direction"]))}
    first = out["members"][0]
    arcs0 = [key[(e, "rev" if r else "fwd")] for e, r in first["arcs"]]
    src = t["from_node"].iloc[arcs0[0]]
    dst = t["to_node"].iloc[arcs0[-1]]
    for k, alpha in enumerate(out["alphas"]):
        w = with_alpha(ROUTING_PROFILES["shortest"], alpha)
        cost = graph.build_costs(w)
        py_arcs, _ = route(graph, src, dst, w, arc_cost=cost)
        py_cost = float(cost[py_arcs].sum())
        best = min(out["members"], key=lambda u: u["costs"][k])
        arcs = [key[(e, "rev" if r else "fwd")] for e, r in best["arcs"]]
        js_cost = float(cost[arcs].sum())
        # the search merges frontier points within 0.5 m of climbing and
        # tolerates 10 cm per node inside; quantisation adds ~5 cm per arc
        tol = alpha * (0.5 + 0.1 * len(arcs)) + 0.05 * len(arcs) + 1.0
        assert js_cost <= py_cost + tol, (alpha, js_cost, py_cost, tol)


# ------------------------------------------------------------- the site
@pytest.fixture(scope="module")
def served_site():
    """The static site over HTTP, as GitHub Pages serves it."""
    import functools
    import http.server
    import threading

    if not SITE_INDEX.exists():
        pytest.skip("site not built")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(SITE_INDEX.parent))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def test_the_site_loads_its_graph_over_http(served_site):
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
        failed: list = []
        page.on("requestfailed", lambda r: failed.append(r.url)
                if served_site in r.url else None)
        page.goto(served_site, wait_until="load", timeout=240_000)
        page.wait_for_function(
            "window.App && App.family && !document.getElementById('result').hidden",
            timeout=240_000)
        out = page.evaluate("""() => ({
            inline: !!window.DATA.bundle, url: window.DATA.bundle_url,
            cameras: window.DATA.cameras && window.DATA.cameras.url, camCount: App.cameras.list.length,
            hillshade: window.DATA.hillshade && window.DATA.hillshade.url,
            shade: !!document.querySelector('img.hillshade') && document.querySelector('img.hillshade').naturalWidth,
            routes: App.family.unique.length, status: document.getElementById('status').textContent,
        })""")
        browser.close()
    assert not errors, errors[:4]
    assert not failed, failed
    assert not out["inline"] and out["url"].startswith("data/graph-")
    assert out["hillshade"].startswith("data/hillshade-") and out["shade"] > 1000
    assert out["routes"] >= 2
    assert out["cameras"].startswith("data/cameras-") and out["camCount"] > 300
    import re
    index = SITE_INDEX.read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="([^"]+)"', index)
    local = [r for r in refs if not r.startswith("http")]
    assert any(re.match(r"app-[0-9a-f]{10}\.js$", r) for r in local), local
    for name in local + [".nojekyll", out["url"], out["hillshade"], out["cameras"]]:
        assert (SITE_INDEX.parent / name).exists(), name


# ------------------------------------------------------------- follow me
_LOC = """() => { const l = App.loc, m = l.marker, cone = m && m.getElement().querySelector('.me-cone');
    const pt = m ? App.map.latLngToContainerPoint(m.getLatLng()) : null;
    const card = document.getElementById('card').getBoundingClientRect(), size = App.map.getSize();
    return { state: l.state, button: document.getElementById('locate').dataset.state,
        dot: m ? [m.getLatLng().lat, m.getLatLng().lng] : null, px: pt ? [pt.x, pt.y] : null,
        heading: l.heading, cone: cone ? cone.style.display : null, zoom: App.map.getZoom(),
        cardTop: card.top, size: [size.x, size.y], folded: document.getElementById('card').classList.contains('following'),
        profile: getComputedStyle(document.getElementById('prof')).display }; }"""


def test_follow_me_shows_a_dot_keeps_the_map_on_it_and_lets_go_when_dragged(served_site):
    """A simulated walk down Marietta Street on a phone-sized screen."""
    from playwright.sync_api import sync_playwright
    errors: list = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=_chromium(),
                                         args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:                            # pragma: no cover
            pytest.skip(f"no usable Chromium: {exc}")
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                  geolocation={"latitude": 33.76330, "longitude": -84.39560, "accuracy": 12},
                                  permissions=["geolocation"])
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(served_site, wait_until="load", timeout=240_000)
        page.wait_for_function(
            "window.App && App.family && !App.family.partial"
            " && !document.getElementById('result').hidden", timeout=240_000)
        before = page.evaluate(_LOC)
        page.click("#locate")
        page.wait_for_function("App.loc.marker", timeout=30_000)
        page.wait_for_timeout(1200)
        first = page.evaluate(_LOC)
        for lat, lon in ((33.76300, -84.39535), (33.76268, -84.39508), (33.76236, -84.39480)):
            ctx.set_geolocation({"latitude": lat, "longitude": lon, "accuracy": 8})
            page.wait_for_timeout(1300)
        walked = page.evaluate(_LOC)
        page.evaluate("() => { App.map.fire('dragstart'); App.map.panBy([150, 0], { animate: false }); }")
        ctx.set_geolocation({"latitude": 33.76205, "longitude": -84.39452, "accuracy": 8})
        page.wait_for_timeout(1400)
        dragged = page.evaluate(_LOC)
        page.click("#locate")
        page.wait_for_timeout(900)
        back = page.evaluate(_LOC)
        page.click("#locate")
        page.wait_for_timeout(400)
        off = page.evaluate(_LOC)
        browser.close()
    assert not errors, errors[:4]
    assert before["state"] == "off" and before["dot"] is None and not before["folded"]
    # a tap: the dot is where the phone says, in the middle of the map the card leaves clear
    assert first["state"] == first["button"] == "follow" and first["zoom"] >= 16
    assert abs(first["dot"][0] - 33.76330) < 1e-6 and abs(first["dot"][1] + 84.39560) < 1e-6
    assert abs(first["px"][0] - first["size"][0] / 2) < 12 and abs(first["px"][1] - first["cardTop"] / 2) < 12
    assert first["px"][1] < first["cardTop"] - 40, "the dot must not be under the card"
    assert first["heading"] is None and first["cone"] == "none"      # not moved yet, no compass
    assert first["folded"] and first["profile"] == "none"            # the card makes room
    # walking south-east: the arrow points that way and the map has come along
    assert 120 < walked["heading"] < 165 and walked["cone"] == "block"
    assert abs(walked["dot"][0] - 33.76236) < 1e-6
    assert abs(walked["px"][0] - walked["size"][0] / 2) < 12 and abs(walked["px"][1] - walked["cardTop"] / 2) < 12
    # dragged: the dot keeps up with the phone, the map stays where it was put
    assert dragged["state"] == dragged["button"] == "free"
    assert abs(dragged["dot"][0] - 33.76205) < 1e-6 and abs(dragged["px"][0] - dragged["size"][0] / 2) > 60
    # a tap comes back, another turns it off and unfolds the card
    assert back["state"] == "follow" and abs(back["px"][0] - back["size"][0] / 2) < 12
    assert off["state"] == off["button"] == "off" and off["dot"] is None and not off["folded"]
    assert off["profile"] != "none"


def test_follow_me_says_so_when_location_is_refused(served_site):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=_chromium(),
                                         args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:                            # pragma: no cover
            pytest.skip(f"no usable Chromium: {exc}")
        page = browser.new_context(viewport={"width": 1280, "height": 800}, permissions=[]).new_page()
        page.goto(served_site, wait_until="load", timeout=240_000)
        page.wait_for_function(
            "window.App && App.family && !App.family.partial"
            " && !document.getElementById('result').hidden", timeout=240_000)
        page.click("#locate")
        page.wait_for_function("document.getElementById('status').textContent.includes('Location')",
                               timeout=30_000)
        out = page.evaluate("() => ({ state: App.loc.state, dot: !!App.loc.marker,"
                            " status: document.getElementById('status').textContent })")
        browser.close()
    assert out["state"] == "off" and not out["dot"]
    assert "switched off" in out["status"]
