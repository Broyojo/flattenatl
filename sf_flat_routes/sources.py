"""Registry of every external dataset used by the project.

Each entry records the URL, the access date, resolution/vintage, licence and
the limitations that matter for this analysis.  ``python -m sf_flat_routes
sources`` prints this table, and it is the single source of truth for the
data-provenance section of the README.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Date on which every URL below was last fetched and verified.
ACCESS_DATE = "2026-10-07"

#: Overture Maps release used for the street network.
OVERTURE_RELEASE = "2026-08-19.0"
OVERTURE_BUCKET = "https://overturemaps-us-west-2.s3.amazonaws.com"
OVERTURE_PREFIX = f"release/{OVERTURE_RELEASE}/theme=transportation"
#: Places and addresses themes of the same release, used only for the route
#: page's offline place search.
OVERTURE_PLACES_PREFIX = f"release/{OVERTURE_RELEASE}/theme=places"
OVERTURE_ADDRESSES_PREFIX = f"release/{OVERTURE_RELEASE}/theme=addresses"
OVERTURE_BASE_PREFIX = f"release/{OVERTURE_RELEASE}/theme=base"

#: USGS 3DEP 1 m lidar project covering Atlanta. The twelve 10 km tiles
#: below are the ones the city limits (plus a 250 m margin) touch; this
#: project covers all of that area on its own, so the neighbouring
#: ``GA_Central_2019_B19`` project, which overlaps the southern tiles, is
#: not needed.
TNM_BUCKET = "https://prd-tnm.s3.amazonaws.com"
LIDAR_PROJECT = "GA_Statewide_2018_B18_DRRA"
LIDAR_PREFIX = f"StagedProducts/Elevation/1m/Projects/{LIDAR_PROJECT}/TIFF"
LIDAR_TILES = tuple(
    f"USGS_1M_16_x{x}y{y}_{LIDAR_PROJECT}.tif"
    for x, y in (
        (72, 373), (72, 374), (72, 375),
        (73, 373), (73, 374), (73, 375), (73, 376),
        (74, 373), (74, 374), (74, 375), (74, 376),
        (75, 374),
    )
)

#: USGS 1/3 arc-second seamless DEM tile, used only to cross-validate the
#: lidar product (it is ~10 m and far too coarse for street grades).
SEAMLESS_DEM_URL = (
    f"{TNM_BUCKET}/StagedProducts/Elevation/13/TIFF/current/n34w085/"
    "USGS_13_n34w085.tif"
)


def _arcgis_geojson(layer_url: str, fields: str) -> str:
    """Query URL returning a whole ArcGIS feature layer as WGS84 GeoJSON."""
    from urllib.parse import urlencode
    return f"{layer_url}/query?" + urlencode(
        {"where": "1=1", "outFields": fields, "outSR": "4326", "f": "geojson"})


#: City of Atlanta official neighborhoods (248 polygons, each tagged with its
#: Neighborhood Planning Unit). The city's own open-data service
#: (gis.atlantaga.gov/dpcd .../OpenDataService) answered 404 on the access
#: date, so this is the copy Atlanta BeltLine, Inc. publishes.
NEIGHBORHOOD_LAYER = ("https://gis.beltline.org/server/rest/services/"
                      "COA_Neighborhoods_public/FeatureServer/0")
NEIGHBORHOOD_URL = _arcgis_geojson(NEIGHBORHOOD_LAYER, "name,npu")

#: City of Atlanta limits, from the city's Department of Transportation.
CITY_LIMITS_LAYER = ("https://services2.arcgis.com/zLeajbicrDRLQcny/arcgis/rest/"
                     "services/CityLimits_DPW/FeatureServer/0")
CITY_LIMITS_URL = _arcgis_geojson(CITY_LIMITS_LAYER, "NAME,SQMILES")

#: Atlanta Regional Commission inventory of existing bicycle and trail
#: facilities across the region (April 2026 edition).
BIKEWAYS_LAYER = ("https://services1.arcgis.com/Ug5xGQbHsD8zuZzM/arcgis/rest/"
                  "services/Existing_Facilities/FeatureServer/2")
BIKEWAYS_URL = _arcgis_geojson(BIKEWAYS_LAYER, "*")


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    publisher: str
    url: str
    accessed: str
    resolution: str
    licence: str
    limitations: str
    role: str
    local: str = ""
    notes: str = ""
    optional: bool = False
    substituted: bool = False
    substitution_reason: str = ""


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        key="overture_segments",
        title=f"Overture Maps transportation segments (release {OVERTURE_RELEASE})",
        publisher="Overture Maps Foundation (derived from OpenStreetMap)",
        url=f"{OVERTURE_BUCKET}/{OVERTURE_PREFIX}/type=segment/",
        accessed=ACCESS_DATE,
        resolution="Vector linestrings; OSM-equivalent positional accuracy (~1-5 m)",
        licence="ODbL 1.0 (OpenStreetMap contributors); Overture schema CDLA-Permissive 2.0",
        role="Routable street network: geometry, road class, per-mode access "
             "restrictions, bridge/tunnel flags and connector topology.",
        local="data/raw/overture_segments_atl.parquet",
        limitations=(
            "OSM-derived, so completeness and tagging quality vary by area. "
            "'trunk' and 'primary' in Atlanta are ordinary surface arterials "
            "with sidewalks (Peachtree Road, Ponce de Leon Avenue, Northside "
            "Drive, Moreland Avenue), so they cannot be excluded from walking "
            "or cycling; only 'motorway' is grade-separated freeway. The "
            "BeltLine and PATH trails are mapped under several spellings and "
            "interim alignments. Sidewalk and crosswalk geometry is present "
            "but of uneven completeness and is deliberately not used."
        ),
        notes="Read with Parquet row-group bbox pruning: only a dozen of the "
              "release's row groups intersect the study box, so the whole "
              "extract costs a few seconds and ~22 MB instead of 64 GB.",
    ),
    Dataset(
        key="overture_connectors",
        title=f"Overture Maps transportation connectors (release {OVERTURE_RELEASE})",
        publisher="Overture Maps Foundation (derived from OpenStreetMap)",
        url=f"{OVERTURE_BUCKET}/{OVERTURE_PREFIX}/type=connector/",
        accessed=ACCESS_DATE,
        resolution="Vector points",
        licence="ODbL 1.0; Overture schema CDLA-Permissive 2.0",
        role="Authoritative intersection nodes. Using connector IDs for graph "
             "topology avoids geometric snapping tolerances entirely.",
        local="data/raw/overture_connectors_atl.parquet",
        limitations="Connectors exist only where OSM ways share a node; "
                    "grade-separated crossings correctly do not connect.",
    ),
    Dataset(
        key="dem_1m",
        title=f"USGS 3DEP 1 metre bare-earth DEM, project {LIDAR_PROJECT}",
        publisher="U.S. Geological Survey, 3D Elevation Program",
        url=f"{TNM_BUCKET}/{LIDAR_PREFIX}/",
        accessed=ACCESS_DATE,
        resolution="1 m ground sample distance; NAD83/UTM 16N (EPSG:26916); "
                   "float32 metres above NAVD88; lidar flown 2018",
        licence="Public domain (U.S. Government work)",
        role="Primary elevation source for all grade and climbing metrics.",
        local="data/raw/dem/*.tif",
        limitations=(
            "Bare-earth interpolation leaves artefacts on bridges, tunnels and "
            "elevated structures, where the DEM samples the ground, railway "
            "or freeway underneath rather than the deck -- handled explicitly "
            "by interpolating elevation across segments flagged is_bridge or "
            "is_tunnel. Residual noise of a few decimetres from vehicles, "
            "curbs and vegetation misclassification is handled by "
            "Savitzky-Golay smoothing plus a gain dead-band. Atlanta's tree "
            "canopy makes the ground returns sparser than in a bare city. "
            "Anything built or regraded since 2018 (parts of the BeltLine, "
            "Westside Park) is measured as it was then. Twelve 10 km tiles "
            "(~3.6 GB total) are cloud-optimised GeoTIFFs."
        ),
    ),
    Dataset(
        key="dem_13",
        title="USGS 3DEP 1/3 arc-second seamless DEM, tile n34w085",
        publisher="U.S. Geological Survey, 3D Elevation Program",
        url=SEAMLESS_DEM_URL,
        accessed=ACCESS_DATE,
        resolution="1/3 arc-second (~10 m); EPSG:4269",
        licence="Public domain (U.S. Government work)",
        role="Independent cross-check on the 1 m lidar elevations (validation "
             "only -- too coarse for street grades).",
        local="data/raw/dem_13_n34w085.tif",
        limitations="~10 m posting smooths away street-scale relief and "
                    "systematically under-reports maximum grades.",
        optional=True,
    ),
    Dataset(
        key="neighborhoods",
        title="City of Atlanta official neighborhoods",
        publisher="City of Atlanta, Department of City Planning; served by "
                  "Atlanta BeltLine, Inc.",
        url=NEIGHBORHOOD_LAYER,
        accessed=ACCESS_DATE,
        resolution="Vector polygons, 248 features, each with its "
                   "Neighborhood Planning Unit (25 NPUs)",
        licence="Open data (City of Atlanta)",
        role="Neighborhood names for origin/destination selection and for "
             "saying where a corridor, pass or barrier is. The pair analysis "
             "runs between 36 of them (config.ANALYSIS_NEIGHBORHOODS).",
        local="data/raw/atl_neighborhoods.geojson",
        limitations=(
            "The polygons cover 333 of the city's 353 km2; rail yards, the "
            "river edge and recently annexed land belong to no neighborhood. "
            "The city's own open-data service (gis.atlantaga.gov "
            "OpenDataService) returned 404 on the access date, so this is "
            "the copy of the same layer that Atlanta BeltLine, Inc. serves; "
            "its vintage is not stated."
        ),
        substituted=True,
        substitution_reason=(
            "The City of Atlanta's own feature service for this layer was "
            "not answering; the BeltLine-hosted copy carries the same "
            "schema (name, NPU, legal area)."
        ),
    ),
    Dataset(
        key="city_limits",
        title="City of Atlanta limits",
        publisher="City of Atlanta, Department of Transportation",
        url=CITY_LIMITS_LAYER,
        accessed=ACCESS_DATE,
        resolution="One polygon, 136.3 sq mi; last edited 2025-07",
        licence="Open data (City of Atlanta)",
        role="Clips the street network, the place search and the hillshade "
             "to the city.",
        local="data/raw/atl_city_limits.geojson",
        limitations="Atlanta's limits are ragged and exclude places many "
                    "people think of as Atlanta: Decatur, most of Druid "
                    "Hills, East Point, Sandy Springs, Vinings and nearly "
                    "all of the airport.",
    ),
    Dataset(
        key="overture_places",
        title=f"Overture Maps places (release {OVERTURE_RELEASE})",
        publisher="Overture Maps Foundation (Meta and Microsoft POI data)",
        url=f"{OVERTURE_BUCKET}/{OVERTURE_PLACES_PREFIX}/type=place/",
        accessed=ACCESS_DATE,
        resolution="Point features with names, categories and a confidence score",
        licence="CDLA Permissive 2.0",
        role="Offline place search in the route page (parks, landmarks, "
             "transit, schools, shops, cafes).",
        local="data/raw/overture_places_atl.parquet",
        limitations="Point-of-interest coverage and naming are uneven; only "
                    "records with confidence >= 0.6 in routable categories "
                    "are kept. Not used by the analysis itself.",
        optional=True,
    ),
    Dataset(
        key="overture_base",
        title=f"Overture Maps base theme: land use, infrastructure, land (release {OVERTURE_RELEASE})",
        publisher="Overture Maps Foundation (derived from OpenStreetMap)",
        url=f"{OVERTURE_BUCKET}/{OVERTURE_BASE_PREFIX}/",
        accessed=ACCESS_DATE,
        resolution="Mapped outlines and points with names and OSM-derived classes",
        licence="ODbL 1.0 (OpenStreetMap contributors)",
        role="Mapped parks, schools, hospitals, plazas, stations, "
             "bridges, viewpoints and peaks for the route page's "
             "offline search; these outrank the POI feed, which places the "
             "same names unreliably.",
        local="data/raw/overture_{land_use,infrastructure,land}_atl.parquet",
        limitations="Only named features in a fixed class list are used. "
                    "Not used by the analysis itself.",
        optional=True,
    ),
    Dataset(
        key="overture_addresses",
        title=f"Overture Maps addresses (release {OVERTURE_RELEASE})",
        publisher="Overture Maps Foundation (OpenAddresses / county sources)",
        url=f"{OVERTURE_BUCKET}/{OVERTURE_ADDRESSES_PREFIX}/type=address/",
        accessed=ACCESS_DATE,
        resolution="Address points with street number and street name",
        licence="Open (OpenAddresses sources)",
        role="Offline street-address search in the route page.",
        local="data/raw/overture_addresses_atl.parquet",
        limitations="One point per (street, number) is kept; unit numbers "
                    "are dropped. Not used by the analysis itself.",
        optional=True,
    ),
    Dataset(
        key="apd_crime",
        title="Atlanta Police Department incident reports (NIBRS), 2021 to date",
        publisher="Atlanta Police Department, open data",
        url="https://services3.arcgis.com/Et5Qfajgiyosiw4d/arcgis/rest/services/"
            "OpenDataWebsite_Crime_view/FeatureServer/0",
        accessed=ACCESS_DATE,
        resolution="One point per report, geocoded to a street address, with "
                   "offense, location type and family-violence flag; about "
                   "310,000 reports, updated daily",
        licence="Open data (City of Atlanta)",
        role="Route finder: 'avoid high-crime areas' (see crime.py). Only "
             "the last two years of violent offenses in public places are "
             "fetched, about 5,800 reports.",
        local="data/raw/apd_crime.json",
        limitations=(
            "Reported crime, counted and not divided by how many people are "
            "about, so busy streets score high partly for being busy. "
            "Geocoded to an address, not to where on the block. Where "
            "reports are written follows where police are as well as where "
            "trouble is. City of Atlanta only."
        ),
        optional=True,
    ),
    Dataset(
        key="alpr_cameras",
        title="Automated license plate readers (DeFlock / OpenStreetMap)",
        publisher="OpenStreetMap contributors, mapped through DeFlock (deflock.me)",
        url="https://overpass-api.de/api/interpreter",
        accessed=ACCESS_DATE + " for the baked snapshot; the page asks again, "
                 "live, when the option is ticked",
        resolution="Point per camera (man_made=surveillance, "
                   "surveillance:type=ALPR) with the direction it faces; "
                   "about 1,360 in the study box, 9 in 10 Flock Safety",
        licence="ODbL 1.0 (OpenStreetMap contributors)",
        role="Route finder: 'avoid Flock cameras' (see cameras.py).",
        local="data/raw/alpr_cameras.json",
        limitations=(
            "Crowd-sourced: a camera nobody has mapped is not avoided, and a "
            "removed one may linger. What a camera sees is modelled, not "
            "known: 40 m along the direction it faces and 10 m all round. "
            "DeFlock's own CDN is the fallback source at build time; it "
            "sends no CORS header, so the page asks the Overpass API."
        ),
        optional=True,
    ),
    Dataset(
        key="bike_network",
        title="Existing bicycle and trail facilities, Atlanta region (April 2026)",
        publisher="Atlanta Regional Commission",
        url=BIKEWAYS_LAYER,
        accessed=ACCESS_DATE,
        resolution="846 lines across the 19-county region, about 210 of them "
                   "in the City of Atlanta, with facility type (unprotected "
                   "lane, protected lane, greenway, sidepath, park trail), "
                   "buffer and barrier material",
        licence="CC BY 4.0",
        role="Bike-mode comfort weighting on the route page ('prefer calm "
             "streets'); see bikeways.py.",
        local="data/raw/arc_bike_facilities.geojson",
        limitations=(
            "Hand-drawn lines with no key into Overture, so they are matched "
            "to graph edges geometrically (within 12 m and 25 degrees, over "
            "at least half the edge). There is no signed-route or sharrow "
            "class, so a street is either a lane, a protected lane, a trail "
            "or nothing. A regional inventory is coarser than a city's own "
            "bikeway layer would be."
        ),
        optional=True,
    ),
)

DATASETS_BY_KEY = {d.key: d for d in DATASETS}


def format_table() -> str:
    """Human-readable provenance report."""
    lines = [f"Data sources (all URLs verified {ACCESS_DATE})", "=" * 78]
    for d in DATASETS:
        flag = " [OPTIONAL]" if d.optional else ""
        flag += " [SUBSTITUTED]" if d.substituted else ""
        lines += [
            f"\n{d.key}{flag}",
            f"  title       : {d.title}",
            f"  publisher   : {d.publisher}",
            f"  url         : {d.url}",
            f"  accessed    : {d.accessed}",
            f"  resolution  : {d.resolution}",
            f"  licence     : {d.licence}",
            f"  local cache : {d.local}",
            f"  role        : {d.role}",
            f"  limitations : {d.limitations}",
        ]
        if d.notes:
            lines.append(f"  notes       : {d.notes}")
        if d.substitution_reason:
            lines.append(f"  substitution: {d.substitution_reason}")
    return "\n".join(lines)


def markdown_table() -> str:
    """Compact markdown table for the README."""
    rows = ["| Dataset | Publisher | Resolution / vintage | Licence | Role |",
            "|---|---|---|---|---|"]
    for d in DATASETS:
        rows.append(
            f"| {d.title} | {d.publisher} | {d.resolution} | {d.licence} | {d.role} |"
        )
    return "\n".join(rows)
