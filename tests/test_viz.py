"""Tests for map-output helpers."""
import json

import numpy as np
import pytest

from sf_flat_routes.viz_interactive import (_round_geometry, encode_polyline)
from sf_flat_routes.viz_static import hillshade


def decode_polyline(s, precision=5):
    """Reference decoder, mirroring the JavaScript in the map."""
    factor = 10 ** precision
    index = lat = lon = 0
    out = []
    while index < len(s):
        for which in ("lat", "lon"):
            shift = result = 0
            while True:
                b = ord(s[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if (result & 1) else (result >> 1)
            if which == "lat":
                lat += d
            else:
                lon += d
        out.append((lon / factor, lat / factor))
    return out


def test_polyline_round_trips_within_precision():
    coords = [(-122.41942, 37.77493), (-122.42500, 37.77812),
              (-122.43111, 37.78001), (-122.41000, 37.76000)]
    back = decode_polyline(encode_polyline(coords))
    assert len(back) == len(coords)
    for (x0, y0), (x1, y1) in zip(coords, back):
        assert abs(x0 - x1) < 2e-5 and abs(y0 - y1) < 2e-5


def test_polyline_of_a_single_point():
    assert decode_polyline(encode_polyline([(-122.4, 37.8)])) == [(-122.4, 37.8)]


def test_polyline_handles_negative_and_zero_deltas():
    coords = [(-122.4, 37.8), (-122.4, 37.8), (-122.5, 37.7)]
    back = decode_polyline(encode_polyline(coords))
    assert len(back) == 3
    assert back[1] == pytest.approx(back[0])


def test_round_geometry_shortens_coordinates():
    geo = {"type": "LineString",
           "coordinates": [(-122.419421234567, 37.774931234567),
                           (-122.425001234567, 37.778121234567)]}
    r = _round_geometry(geo, 5)
    assert r["coordinates"][0] == [-122.41942, 37.77493]
    assert len(json.dumps(r)) < len(json.dumps(geo))


def test_round_geometry_handles_multilinestring():
    geo = {"type": "MultiLineString",
           "coordinates": [[(-122.1234567, 37.1234567), (-122.2, 37.2)],
                           [(-122.3, 37.3)]]}
    r = _round_geometry(geo, 4)
    assert r["coordinates"][0][0] == [-122.1235, 37.1235]
    assert r["type"] == "MultiLineString"


def test_hillshade_is_bounded_and_flat_ground_is_uniform():
    flat = np.zeros((20, 20))
    hs = hillshade(flat, res=1.0)
    assert hs.shape == flat.shape
    assert np.all((hs >= 0) & (hs <= 1))
    assert np.ptp(hs) == pytest.approx(0.0, abs=1e-9)


def test_hillshade_distinguishes_slope_direction():
    ramp = np.tile(np.arange(20.0), (20, 1))          # rises to the east
    hs = hillshade(ramp, res=1.0)
    other = hillshade(-ramp, res=1.0)
    assert not np.allclose(hs, other)
