"""Reported violent crime per block (crime.py)."""
from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

from sf_flat_routes import crime


def _row(offense, place="HIGHWAY_ROAD_ALLEY_STREET_SIDEWALK", family="NO", lon=-84.39, lat=33.75):
    return {"NIBRS_Offense": offense, "LocationType": place, "GAFamilyViolenceIndicator": family,
            "OccurredFromDate": 1791000000000, "Longitude": lon, "Latitude": lat}


def test_only_violent_crime_in_public_between_strangers_counts(tmp_path):
    rows = [
        _row("Robbery"), _row("Aggravated Assault", place="PARKING_DROP_LOT_GARAGE"),
        _row("Simple Assault", place="BAR_NIGHTCLUB"),
        _row("Aggravated Assault", family="YES"),               # family violence
        _row("Robbery", place="RESIDENCE_HOME"),                # at home
        _row("Simple Assault", place="APARTMENT"),
        _row("Theft From Motor Vehicle"),                       # property crime
        _row("Intimidation"),                                   # not on the list
        _row("Robbery", lon=None, lat=None),                    # nowhere
    ]
    path = tmp_path / "crime.json"
    path.write_text(json.dumps({"source": "t", "since": "2024-10-09", "until": "2026-10-09",
                                "incidents": rows}))
    lon, lat, w, meta = crime.street_incidents(path)
    assert list(w) == [1.0, 1.0, 0.5]
    assert len(lon) == len(lat) == 3 and meta["until"] == "2026-10-09"


def test_density_peaks_on_the_block_where_the_incidents_are():
    # three parallel blocks in UTM 16N metres, 300 m apart, near Five Points
    x0, y0 = 741_800.0, 3_737_700.0
    edges = gpd.GeoDataFrame({"length_m": [200.0] * 3}, geometry=[
        LineString([(x0, y0 + dy), (x0 + 200, y0 + dy)]) for dy in (0, 300, 2000)], crs="EPSG:26916")
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:26916", "EPSG:4326", always_xy=True)
    lon, lat = tr.transform([x0 + 100] * 12, [y0] * 12)        # twelve incidents on the first
    d = crime.edge_density(edges, np.array(lon), np.array(lat), np.ones(12), years=2)
    # the peak of a Gaussian of weight W is W / (2 pi sigma^2), here per km2 per year
    peak = 12 / (2 * np.pi * (crime.SIGMA_M / 1000) ** 2) / 2
    assert d[0] == pytest.approx(peak, rel=0.1)
    assert d[0] > crime.HIGH_CRIME_DENSITY > d[1]              # 300 m is four sigma away
    assert d[2] == 0


def test_no_incidents_is_no_density():
    edges = gpd.GeoDataFrame({"length_m": [100.0]}, geometry=[LineString([(0, 0), (100, 0)])],
                             crs="EPSG:26916")
    assert list(crime.edge_density(edges, np.array([]), np.array([]), np.array([]))) == [0.0]
    assert crime.build_crime(edges, crime.CRIME_JSON.with_name("missing.json")) is None


@pytest.mark.skipif(not crime.CRIME_JSON.exists(), reason="crime data not downloaded")
def test_the_real_data_closes_a_few_percent_of_the_city():
    from sf_flat_routes.config import PROCESSED_DIR
    path = PROCESSED_DIR / "edges_metrics.parquet"
    if not path.exists():
        pytest.skip("network not built")
    edges = gpd.read_parquet(path, columns=["length_m", "geometry"])
    out = crime.build_crime(edges)
    assert out["edge_crime"].dtype == np.dtype("<u1") and len(out["edge_crime"]) == len(edges)
    m = out["meta"]
    assert 2000 < m["incidents"] < 30000 and m["years"] == crime.WINDOW_YEARS
    assert 0.01 < m["share"] < 0.10
    # the byte the page reads says the same thing as the density it came from
    hot = out["edge_crime"] >= m["threshold"] / m["step"]
    share = edges["length_m"].to_numpy()[hot].sum() / edges["length_m"].sum()
    assert share == pytest.approx(m["share"], abs=0.003)
