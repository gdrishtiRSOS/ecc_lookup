# ecc_lookup

A single-module Python library. One US county in, boundary geometry and known ECC/PSAP jurisdiction scope out.

```python
from ecc_lookup import lookup_county

result = lookup_county("Los Angeles County, CA")
result = lookup_county("06037")   # 5-digit county FIPS/GEOID, equivalent
```

**This does not produce ECC service-area boundaries, and never claims to.** ECC/PSAP service areas are operational jurisdictions defined by local agreements and frequently don't follow county lines; authoritative polygons for them aren't reliably available. What this returns is a **county footprint** — "where is this county," not "what area does this ECC serve" — plus which ECCs are *known to operate in* that county, per the FCC's registry. Every result carries a `disclaimer` field; it is not optional.

**Informational only — never authoritative for 9-1-1 routing, dispatch, or emergency response.**

This is a component, not an application: it returns data rather than writing files, it doesn't own a cache directory or a config file, and the CLI below is a convenience, not the primary interface.

## Install

```bash
pip install -r requirements.txt   # one dependency: geopandas
```

Python 3.11+ is the target (this repo was actually built and tested against 3.10 — nothing here needs 3.11-only syntax, but treat the interpreter upgrade as an open item).

## Usage

### Look up a county

```python
from ecc_lookup import lookup_county

result = lookup_county("Los Angeles County, CA")
# or: lookup_county("06037")

result["county"]                # {"name": "Los Angeles", "state": "CA", "geoid": "06037", ...}
result["geometry"]               # GeoJSON MultiPolygon geometry, WGS84
result["jurisdiction_scope"]     # {"status": "ok" | "unavailable" | "no_entries", "eccs": [...]}
result["sources"]                # provenance: name, url, retrieved_at, vintage, license
result["disclaimer"]             # always present, never optional
```

Query format: a 5-digit county GEOID, or `"<Name>, <ST>"` with the state required (e.g. `"St. Louis city, MO"`, `"Orleans Parish, LA"`, `"Capitol Planning Region, CT"`). No bare names, no regions, no multi-county merging, no batch input, no fuzzy matching — an unresolvable query raises `LookupError`, a malformed one raises `ValueError`.

`county.name` is the short Census name (e.g. `"St. Louis"`), not the full `NAMELSAD` (`"St. Louis County"`) — two different county-equivalents can share the same `name` (St. Louis city vs. St. Louis County; Baltimore city vs. Baltimore County). Use `geoid` and `county_equivalent_type` to disambiguate, not `name` alone.

### Write a GeoJSON file

```python
from ecc_lookup import lookup_county, to_geojson

result = lookup_county("Los Angeles County, CA")
to_geojson(result, "los_angeles_county.geojson")   # FeatureCollection, exactly one Feature
```

Or resolve and write in one call:

```python
from ecc_lookup import lookup_county_to_geojson

result = lookup_county_to_geojson("Los Angeles County, CA", "out/")
# writes out/06037.geojson (named by GEOID); creates out/ if it doesn't exist
# still returns the same dict lookup_county would return
```

`to_shapefile(result, path)` also exists in the API surface but is currently unimplemented (`NotImplementedError`) — per the design spec, add it only if a host project actually needs `.shp` output; GeoJSON covers most cases.

### Reuse a loaded county layer across many lookups

```python
from ecc_lookup import _load_counties, lookup_county   # _load_counties is "internal" but stable enough to reuse deliberately

counties = _load_counties(vintage=2025, cache_dir=None)
la = lookup_county("Los Angeles County, CA", counties=counties)
sf = lookup_county("San Francisco County, CA", counties=counties)
```

Passing `counties=` skips the network/cache path entirely — useful for a host project doing many lookups that wants to control its own caching.

### Command line

```bash
python ecc_lookup.py "Los Angeles County, CA"
python ecc_lookup.py "06037" --output-dir out          # also writes out/06037.geojson
python ecc_lookup.py "Los Angeles County, CA" --cache-dir .cache --output-dir out
```

Always prints the full JSON result to stdout; `--output-dir`, if given, additionally writes `<geoid>.geojson` there via `lookup_county_to_geojson`. On error (`LookupError`/`ValueError`) it prints `error: ...` to stderr and exits non-zero.

## Data sources

- **Geometry**: US Census Bureau cartographic boundary files, national county layer, pinned to the 2025 vintage. Public domain, no key, no rate limit. Cached on first use (per `cache_dir`).
- **Jurisdiction scope**: FCC 911 Master PSAP Registry. **Note:** FCC's own registry page is behind bot protection and not programmatically reachable from this environment; the implementation uses the `opendata.fcc.gov` Socrata mirror instead, which is confirmed **stale since 2019-02-26** — recorded honestly in `result["sources"]` on every call, never hidden. If the registry is unreachable, `lookup_county` still returns geometry, with `jurisdiction_scope.status == "unavailable"` — it never raises for a registry outage.

## Testing

```bash
python -m pytest
```

24 tests, fully offline (a `conftest.py` autouse fixture blocks real network access and isolates each test's cache directory). Covers the return contract, GeoJSON structure/winding/rounding, and every named join-failure mode: Connecticut planning regions, the Alaska Valdez-Cordova split, St. Louis city vs. county, Baltimore city vs. county, Virginia independent cities, and Louisiana parishes.

## Project layout

```
ecc_lookup.py            # the whole library — copy this one file into a host project
requirements.txt         # one line: geopandas
tests/
├── conftest.py          # blocks network access, isolates cache dirs, for every test
└── test_ecc_lookup.py
```

No package directory, no `__init__.py`, no `pyproject.toml` — dropping this into a larger project means copying `ecc_lookup.py`.
