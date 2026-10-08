"""The route page's offline place index."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sf_flat_routes import places
from sf_flat_routes.config import CITY_BBOX
from sf_flat_routes.download import ADDRESSES_PARQUET, BASE_PARQUETS, PLACES_PARQUET


def test_street_names_are_title_cased_with_suffixes_kept_short():
    assert places._title_street("JOHN MUIR DR") == "John Muir Dr"
    assert places._title_street("24TH ST") == "24th St"
    assert places._title_street("VAN NESS AVE") == "Van Ness Ave"
    assert places._title_street("DR CARLTON B GOODLETT PL") == "Dr Carlton B Goodlett Pl"


def test_title_street_reads_atlanta_addresses_with_their_quadrant():
    t = places._title_street
    assert t("PEACHTREE Street Northeast") == "Peachtree Street NE"
    assert t("PONCE DE LEON Avenue Northeast") == "Ponce de Leon Avenue NE"
    assert t("10TH Street Northwest") == "10th Street NW"
    assert t("West PEACHTREE Street Northwest") == "West Peachtree Street NW"
    assert t("MARTIN LUTHER KING JR Drive Southwest") == "Martin Luther King Jr Drive SW"
    # a quadrant word that is the street's own name stays spelled out
    assert t("NORTHWEST Drive Northwest") == "Northwest Drive NW"


def test_core_strips_a_trailing_city_name():
    assert places._core("Piedmont Park, Atlanta") == "piedmont park"
    assert places._core("Fox Theatre, ATL") == "fox theatre"
    assert places._core("Grant Park - Atlanta, GA") == "grant park"
    assert places._core("Freedom Park") == "freedom park"
    # a suffix that is part of the name is not a city suffix
    assert places._core("Cafe Georgia") == "cafe georgia"


def test_support_counts_nearby_records_that_mention_the_name():
    names = pd.Series(["Dolores Park", "Dolores Park Cafe", "Dolores Park Tennis",
                       "Dolores Park", "Nowhere"])
    lon = np.array([-84.372, -84.3715, -84.3725, -84.359, -84.445])
    lat = np.array([33.7866, 33.7870, 33.7863, 33.811, 33.727])
    sup = places._support(names, lon, lat, names, lon, lat)
    assert sup[0] == 2          # the real park: two neighbours mention it
    assert sup[3] == 0          # the stray copy across town: none


def test_variant_pruning_keeps_different_kinds_and_distant_namesakes():
    df = pd.DataFrame({
        "name": ["Dolores Park", "Dolores Park, Atlanta", "Dolores Park Cafe",
                 "Golden Gate Park", "Golden Gate Park - East", "Golden Gate Park Carousel"],
        "group": ["park", "park", "food", "park", "park", "landmark"],
        "lon": [-84.372, -84.366, -84.3709, -84.427, -84.403, -84.403],
        "lat": [33.7866, 33.8006, 33.7883, 33.7964, 33.7961, 33.7961],
        "conf": [0.97, 0.72, 0.99, 0.98, 0.9, 0.9],
        "support": [3, 0, 0, 5, 0, 0],
    })
    out = places._prune_variants(df)
    kept = set(out["name"])
    assert "Dolores Park" in kept
    assert "Dolores Park, Atlanta" not in kept            # city suffix, any distance
    assert "Dolores Park Cafe" in kept                    # a different kind of place
    assert "Golden Gate Park" in kept
    assert "Golden Gate Park Carousel" in kept            # different kind
    assert "Golden Gate Park - East" not in kept          # same kind, unsupported variant


needs_places = pytest.mark.skipif(not PLACES_PARQUET.exists(),
                                  reason="Overture places not downloaded")
needs_base = pytest.mark.skipif(not all(p.exists() for p in BASE_PARQUETS.values()),
                                reason="Overture base theme not downloaded")
needs_addresses = pytest.mark.skipif(not ADDRESSES_PARQUET.exists(),
                                     reason="Overture addresses not downloaded")


@pytest.fixture(scope="module")
def index():
    return places.build_places()


@needs_places
@needs_base
def test_place_index_is_compact_and_inside_the_city(index):
    n = len(index["names"])
    assert 5000 < n < 20000
    assert len(index["group"]) == n == len(index["lon"]) == len(index["lat"])
    assert max(index["group"]) < len(index["groups"])
    assert min(index["lon"]) >= CITY_BBOX[0] and max(index["lon"]) <= CITY_BBOX[1]
    assert min(index["lat"]) >= CITY_BBOX[2] and max(index["lat"]) <= CITY_BBOX[3]
    # the study box is mostly not Atlanta: nothing from Decatur's square
    # (well inside the box) may survive the clip to the city limits
    near_decatur = [(lo, la) for lo, la in zip(index["lon"], index["lat"])
                    if abs(lo + 84.2963) < 0.004 and abs(la - 33.7748) < 0.004]
    assert not near_decatur
    assert len(set(index["names"])) == n or len(set(zip(index["names"], index["group"]))) == n


@needs_places
@needs_base
def test_mapped_features_win_over_the_poi_feed(index):
    """The mapped park is the only record that remains under its name: the
    POI feed's own 'Piedmont Park' points are superseded by the outline."""
    hits = {(n, index["groups"][g]): (lo, la) for n, g, lo, la in
            zip(index["names"], index["group"], index["lon"], index["lat"])}
    assert [k for k in hits if k[0] == "Piedmont Park"] == [("Piedmont Park", "park")]
    # famous things sit where they belong
    for name, kind, lon, lat in [("Piedmont Park", "park", -84.3733, 33.7867),
                                 ("Grant Park", "park", -84.3715, 33.7368),
                                 ("Centennial Olympic Park", "park", -84.3932, 33.7603),
                                 ("Five Points", "station", -84.3916, 33.7539),
                                 ("Atlanta Botanical Garden", "garden", -84.3726, 33.7899)]:
        assert (name, kind) in hits, name
        lo, la = hits[(name, kind)]
        assert abs(lo - lon) < 0.004 and abs(la - lat) < 0.004, name


@needs_addresses
def test_addresses_pack_into_sorted_uint16_offsets():
    a = places.build_addresses()
    n = len(a["number"])
    assert n > 100_000
    assert a["street"].dtype == np.dtype("<u2") and a["number"].dtype == np.dtype("<u2")
    assert a["lon"].dtype == np.dtype("<u2") and a["lat"].dtype == np.dtype("<u2")
    # sorted by street then number, so the browser can binary-search
    key = a["street"].astype(np.int64) * 100_000 + a["number"]
    assert np.all(np.diff(key) > 0)
    lon = a["origin"][0] + a["lon"] * 1e-5
    lat = a["origin"][1] + a["lat"] * 1e-5
    assert lon.min() >= CITY_BBOX[0] and lon.max() <= CITY_BBOX[1] + 1e-4
    assert lat.min() >= CITY_BBOX[2] and lat.max() <= CITY_BBOX[3] + 1e-4
    assert "Ponce de Leon Avenue NE" in a["streets"] and "Peachtree Street NE" in a["streets"]
