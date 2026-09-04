import json
import urllib.error

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

import ecc_lookup
from ecc_lookup import (
    _ensure_ring_winding,
    _fetch_registry,
    _jurisdiction_status,
    _roster_for,
    _signed_area,
    lookup_county,
    lookup_county_to_geojson,
    to_geojson,
)

# A simple square in WGS84 - doesn't need to be the true county shape, just
# valid geometry, since tests exercise the join/shape logic, not cartography.
_SQUARE = Polygon([(-118.0, 33.0), (-117.0, 33.0), (-117.0, 34.0), (-118.0, 34.0), (-118.0, 33.0)])


def _make_county_row(*, geoid, name, namelsad, stusps, lsad, aland=1000, awater=10):
    return {
        "GEOID": geoid,
        "NAME": name,
        "NAMELSAD": namelsad,
        "STUSPS": stusps,
        "LSAD": lsad,
        "ALAND": aland,
        "AWATER": awater,
    }


@pytest.fixture
def la_county_fixture():
    row = _make_county_row(
        geoid="06037",
        name="Los Angeles",
        namelsad="Los Angeles County",
        stusps="CA",
        lsad="06",
    )
    return gpd.GeoDataFrame([row], geometry=[_SQUARE], crs="EPSG:4326")


def test_lookup_county_geoid_returns_geometry_and_registry_unavailable(la_county_fixture):
    # No registry fixture injected and the network is blocked (conftest
    # autouse fixture), so the registry fetch fails as it would during a
    # real FCC outage - and geometry must still come back per spec.
    result = lookup_county("06037", counties=la_county_fixture)
    assert result["query"] == "06037"
    assert result["county"]["geoid"] == "06037"
    assert result["county"]["name"] == "Los Angeles"
    assert result["county"]["state"] == "CA"
    assert result["county"]["county_equivalent_type"] == "county"
    assert result["county"]["boundary_type"] == "county_footprint"
    assert result["geometry"]["type"] == "MultiPolygon"
    assert result["jurisdiction_scope"]["status"] == "unavailable"
    assert result["jurisdiction_scope"]["eccs"] == []
    assert "NOT an ECC service area" in result["disclaimer"]
    assert len(result["sources"]) == 2
    json.dumps(result)  # must be JSON-serialisable


def test_lookup_county_name_state_query(la_county_fixture):
    result = lookup_county("Los Angeles County, CA", counties=la_county_fixture)
    assert result["county"]["geoid"] == "06037"


def test_lookup_county_malformed_query_raises_value_error(la_county_fixture):
    with pytest.raises(ValueError):
        lookup_county("Los Angeles", counties=la_county_fixture)  # bare name, no state


def test_lookup_county_no_match_raises_lookup_error(la_county_fixture):
    with pytest.raises(LookupError):
        lookup_county("06099", counties=la_county_fixture)


# --- to_geojson -----------------------------------------------------------


def test_signed_area_and_winding_helpers():
    ccw_square = [[-118.0, 33.0], [-117.0, 33.0], [-117.0, 34.0], [-118.0, 34.0], [-118.0, 33.0]]
    cw_square = list(reversed(ccw_square))
    assert _signed_area(ccw_square) > 0
    assert _signed_area(cw_square) < 0
    assert _ensure_ring_winding(cw_square, clockwise=False) == ccw_square
    assert _ensure_ring_winding(ccw_square, clockwise=True) == cw_square


def test_to_geojson_structure_and_key_order(la_county_fixture, tmp_path):
    result = lookup_county("06037", counties=la_county_fixture)
    out_path = tmp_path / "la.geojson"
    to_geojson(result, out_path)

    with open(out_path, encoding="utf-8") as f:
        raw = f.read()
    doc = json.loads(raw)

    assert doc["type"] == "FeatureCollection"
    assert len(doc["features"]) == 1
    feature = doc["features"][0]
    assert list(feature.keys()) == ["type", "properties", "geometry"]

    props = feature["properties"]
    assert list(props.keys()) == [
        "name",
        "state",
        "geoid",
        "boundary_type",
        "aland_sq_m",
        "awater_sq_m",
        "ecc_count",
        "jurisdiction_scope_status",
        "disclaimer",
        "jurisdiction_scope",
        "sources",
    ]
    assert props["boundary_type"] == "county_footprint"
    assert props["ecc_count"] == 0
    assert props["jurisdiction_scope_status"] == "unavailable"
    assert "NOT an ECC service area" in props["disclaimer"]

    assert "crs" not in doc
    assert "bbox" not in doc
    assert "id" not in feature

    assert feature["geometry"]["type"] == "MultiPolygon"
    assert raw.endswith("\n")


def test_to_geojson_rounds_coordinates_to_6_decimals(la_county_fixture, tmp_path):
    result = lookup_county("06037", counties=la_county_fixture)
    out_path = tmp_path / "la.geojson"
    to_geojson(result, out_path)
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    coords = doc["features"][0]["geometry"]["coordinates"]
    for polygon in coords:
        for ring in polygon:
            for lon, lat in ring:
                assert round(lon, 6) == lon
                assert round(lat, 6) == lat


def test_to_geojson_rings_closed(la_county_fixture, tmp_path):
    result = lookup_county("06037", counties=la_county_fixture)
    out_path = tmp_path / "la.geojson"
    to_geojson(result, out_path)
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    for polygon in doc["features"][0]["geometry"]["coordinates"]:
        for ring in polygon:
            assert ring[0] == ring[-1]


def test_to_geojson_exterior_ring_winds_counterclockwise(tmp_path):
    # Deliberately clockwise-wound square, as Census shapefiles commonly are.
    cw_square = Polygon([(-118.0, 33.0), (-118.0, 34.0), (-117.0, 34.0), (-117.0, 33.0), (-118.0, 33.0)])
    row = _make_county_row(geoid="06037", name="Los Angeles", namelsad="Los Angeles County", stusps="CA", lsad="06")
    counties = gpd.GeoDataFrame([row], geometry=[cw_square], crs="EPSG:4326")
    result = lookup_county("06037", counties=counties)

    out_path = tmp_path / "la.geojson"
    to_geojson(result, out_path)
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    exterior_ring = doc["features"][0]["geometry"]["coordinates"][0][0]
    assert _signed_area(exterior_ring) > 0  # counterclockwise


def test_lookup_county_to_geojson_writes_file_named_by_geoid(la_county_fixture, tmp_path):
    result = lookup_county_to_geojson("06037", tmp_path, counties=la_county_fixture)

    out_path = tmp_path / "06037.geojson"
    assert out_path.exists()

    # Returned dict matches what lookup_county alone would return.
    direct_result = lookup_county("06037", counties=la_county_fixture)
    assert result["county"] == direct_result["county"]
    assert result["jurisdiction_scope"]["status"] == direct_result["jurisdiction_scope"]["status"]

    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["type"] == "FeatureCollection"
    assert len(doc["features"]) == 1
    feature = doc["features"][0]
    assert list(feature.keys()) == ["type", "properties", "geometry"]
    assert feature["properties"]["geoid"] == "06037"
    assert feature["properties"]["boundary_type"] == "county_footprint"


def test_lookup_county_to_geojson_creates_output_dir(la_county_fixture, tmp_path):
    nested_dir = tmp_path / "nested" / "output"
    assert not nested_dir.exists()

    lookup_county_to_geojson("06037", nested_dir, counties=la_county_fixture)

    assert nested_dir.exists()
    assert (nested_dir / "06037.geojson").exists()


# --- registry: _fetch_registry, _roster_for, _jurisdiction_status ---------


def _registry_row(*, fcc_psap_id, name, state, county, agency_type="unknown"):
    return {
        "fcc_psap_id": fcc_psap_id,
        "name": name,
        "state": state,
        "county": county,
        "agency_type": agency_type,
    }


# Census county-equivalent fixture rows covering every named join-failure
# mode, as plain dicts (pd.Series-like via __getitem__, sufficient for the
# pure registry functions - only lookup_county's end-to-end tests need a
# real GeoDataFrame with geometry).
CT_CAPITOL_PLANNING_REGION = _make_county_row(
    geoid="09110", name="Capitol", namelsad="Capitol Planning Region", stusps="CT", lsad="PL"
)
AK_CHUGACH = _make_county_row(geoid="02063", name="Chugach", namelsad="Chugach Census Area", stusps="AK", lsad="05")
AK_COPPER_RIVER = _make_county_row(
    geoid="02066", name="Copper River", namelsad="Copper River Census Area", stusps="AK", lsad="05"
)
MO_ST_LOUIS_COUNTY = _make_county_row(
    geoid="29189", name="St. Louis", namelsad="St. Louis County", stusps="MO", lsad="06"
)
MO_ST_LOUIS_CITY = _make_county_row(
    geoid="29510", name="St. Louis", namelsad="St. Louis city", stusps="MO", lsad="25"
)
VA_RICHMOND_CITY = _make_county_row(
    geoid="51760", name="Richmond", namelsad="Richmond city", stusps="VA", lsad="25"
)
LA_ORLEANS_PARISH = _make_county_row(geoid="22071", name="Orleans", namelsad="Orleans Parish", stusps="LA", lsad="15")


def test_roster_for_connecticut_planning_region_empty_roster_forces_unavailable():
    # Registry keyed to the LEGACY CT county name, as the real registry is.
    registry = [_registry_row(fcc_psap_id="9001", name="Hartford Test PSAP", state="CT", county="Hartford")]
    roster = _roster_for(registry, CT_CAPITOL_PLANNING_REGION)
    assert roster == []
    # An empty CT roster is a crosswalk failure, never a plain "ok" empty list.
    assert _jurisdiction_status("ok", CT_CAPITOL_PLANNING_REGION, roster) == "unavailable"


def test_roster_for_alaska_valdez_cordova_split_chugach():
    registry = [
        _registry_row(fcc_psap_id="9002", name="Valdez-Cordova Test PSAP", state="AK", county="Valdez-cordova Census Area")
    ]
    roster = _roster_for(registry, AK_CHUGACH)
    assert len(roster) == 1
    assert roster[0]["fcc_psap_id"] == "9002"
    assert roster[0]["counties_listed"] == ["Valdez-cordova Census Area"]


def test_roster_for_alaska_valdez_cordova_split_copper_river():
    registry = [
        _registry_row(fcc_psap_id="9002", name="Valdez-Cordova Test PSAP", state="AK", county="Valdez-cordova Census Area")
    ]
    roster = _roster_for(registry, AK_COPPER_RIVER)
    assert len(roster) == 1
    assert roster[0]["fcc_psap_id"] == "9002"


def test_roster_for_st_louis_city_vs_county_exact_match_no_crosscontamination():
    registry = [
        _registry_row(fcc_psap_id="9003", name="St Louis County PSAP", state="MO", county="St. Louis"),
        _registry_row(fcc_psap_id="9004", name="St Louis City PSAP", state="MO", county="St. Louis City"),
    ]
    county_roster = _roster_for(registry, MO_ST_LOUIS_COUNTY)
    city_roster = _roster_for(registry, MO_ST_LOUIS_CITY)
    assert [e["fcc_psap_id"] for e in county_roster] == ["9003"]
    assert [e["fcc_psap_id"] for e in city_roster] == ["9004"]


def test_roster_for_virginia_independent_city_resolves():
    registry = [_registry_row(fcc_psap_id="9005", name="Richmond City PSAP", state="VA", county="Richmond City")]
    roster = _roster_for(registry, VA_RICHMOND_CITY)
    assert [e["fcc_psap_id"] for e in roster] == ["9005"]


def test_roster_for_louisiana_parish_resolves():
    registry = [_registry_row(fcc_psap_id="9006", name="Orleans Parish PSAP", state="LA", county="Orleans")]
    roster = _roster_for(registry, LA_ORLEANS_PARISH)
    assert [e["fcc_psap_id"] for e in roster] == ["9006"]


def test_roster_for_counties_listed_can_exceed_queried_county():
    registry = [
        _registry_row(fcc_psap_id="9006", name="Orleans Parish PSAP", state="LA", county="Orleans"),
        _registry_row(fcc_psap_id="9006", name="Orleans Parish PSAP", state="LA", county="Jefferson"),
    ]
    roster = _roster_for(registry, LA_ORLEANS_PARISH)
    assert len(roster) == 1
    assert sorted(roster[0]["counties_listed"]) == ["Jefferson", "Orleans"]


def test_roster_for_no_entries_case():
    registry = [_registry_row(fcc_psap_id="9999", name="Unrelated PSAP", state="TX", county="Travis")]
    roster = _roster_for(registry, LA_ORLEANS_PARISH)
    assert roster == []
    assert _jurisdiction_status("ok", LA_ORLEANS_PARISH, roster) == "no_entries"


def test_roster_for_never_fabricates_name_from_county():
    roster = _roster_for([], LA_ORLEANS_PARISH)
    assert roster == []


def test_jurisdiction_status_ok_when_roster_non_empty():
    registry = [_registry_row(fcc_psap_id="9006", name="Orleans Parish PSAP", state="LA", county="Orleans")]
    roster = _roster_for(registry, LA_ORLEANS_PARISH)
    assert _jurisdiction_status("ok", LA_ORLEANS_PARISH, roster) == "ok"


def test_fetch_registry_network_failure_returns_unavailable_and_empty(tmp_path):
    rows, status = _fetch_registry(str(tmp_path))
    assert rows == []
    assert status == "unavailable"


def test_lookup_county_registry_unavailable_still_returns_geometry(la_county_fixture):
    result = lookup_county("06037", counties=la_county_fixture)
    assert result["geometry"]["type"] == "MultiPolygon"
    assert result["jurisdiction_scope"]["status"] == "unavailable"
    assert result["jurisdiction_scope"]["eccs"] == []


def test_lookup_county_wires_real_roster_through_when_registry_available(monkeypatch, la_county_fixture):
    fixture_registry = [_registry_row(fcc_psap_id="9010", name="LA County Sheriff Comm", state="CA", county="Los Angeles")]
    monkeypatch.setattr(ecc_lookup, "_fetch_registry", lambda cache_dir: (fixture_registry, "ok"))

    result = lookup_county("06037", counties=la_county_fixture)
    assert result["jurisdiction_scope"]["status"] == "ok"
    assert [e["fcc_psap_id"] for e in result["jurisdiction_scope"]["eccs"]] == ["9010"]
