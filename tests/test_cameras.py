"""License-plate cameras: the build-time snapshot (cameras.py)."""
from __future__ import annotations

import json

import pytest

from sf_flat_routes import cameras
from sf_flat_routes.config import CITY_BBOX


def test_the_overpass_query_asks_for_alpr_nodes_in_the_study_box():
    q = cameras.overpass_query()
    assert '"man_made"="surveillance"' in q and '"surveillance:type"="ALPR"' in q
    # Overpass wants south,west,north,east; the config is west,east,south,north
    lon_min, lon_max, lat_min, lat_max = CITY_BBOX
    assert f"({lat_min},{lon_min},{lat_max},{lon_max})" in q
    assert q.startswith("[out:json]")


def test_direction_falls_back_to_the_camera_direction_tag():
    assert cameras._direction({"direction": "180"}) == "180"
    assert cameras._direction({"camera:direction": "70;340"}) == "70;340"
    assert cameras._direction({}) == ""


def test_the_snapshot_keeps_cameras_in_the_city_and_their_directions(tmp_path):
    raw = {"source": "test", "asof": "2026-10-09T00:00:00Z", "cameras": [
        # Midtown, Flock; Downtown, another make; Decatur square, outside the city
        {"id": 1, "lon": -84.3857, "lat": 33.7816, "direction": "270-315", "manufacturer": "Flock Safety"},
        {"id": 2, "lon": -84.3916, "lat": 33.7539, "direction": "", "manufacturer": "Genetec"},
        {"id": 3, "lon": -84.2963, "lat": 33.7748, "direction": "90", "manufacturer": "Flock Safety"},
    ]}
    path = tmp_path / "cams.json"
    path.write_text(json.dumps(raw))
    out = cameras.build_cameras(path)
    assert out["asof"] == raw["asof"] and out["bbox"] == list(CITY_BBOX)
    assert out["query"] == cameras.overpass_query() and out["overpass"]
    from sf_flat_routes.download import CITY_LIMITS_GEOJSON
    if not CITY_LIMITS_GEOJSON.exists():
        pytest.skip("city limits not downloaded: the snapshot is not clipped")
    # every make is kept, not only Flock; the one in Decatur is not
    assert out["cams"] == [[-84.3916, 33.7539, ""], [-84.3857, 33.7816, "270-315"]]
    assert out["flock"] == 1


def test_no_snapshot_file_means_no_camera_option(tmp_path):
    assert cameras.build_cameras(tmp_path / "missing.json") is None


@pytest.mark.skipif(not cameras.CAMERAS_JSON.exists(), reason="cameras not downloaded")
def test_the_real_snapshot_is_mostly_flock_and_mostly_has_a_direction():
    out = cameras.build_cameras()
    n = len(out["cams"])
    assert 300 < n < 5000
    assert out["flock"] > 0.6 * n
    assert sum(1 for c in out["cams"] if c[2]) > 0.9 * n
    lon_min, lon_max, lat_min, lat_max = CITY_BBOX
    assert all(lon_min <= c[0] <= lon_max and lat_min <= c[1] <= lat_max for c in out["cams"])
