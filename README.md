# ppr_analysis

Local Python CLI that treats the [Property Price Register](https://www.propertypriceregister.ie/website/npsra/pprweb.nsf/PPRDownloads) as the source of truth for Drogheda (Co. Louth) sales, then enriches those rows with beds, baths, asking price, floor area, BER, and house type from Daft’s sold-properties index.

This is a personal research tool. It does not republish Daft photos or listing copy. A local map site lives in `web/`.

## What the sources actually contain

**PPR** is the official stamp-duty register (price and deed date). The bulk zip is used instead of the HTML search form. Encoding is Windows-1252. There are no bedrooms, bathrooms, asking prices, or detached/semi-d types. PSRA does not clean address errors. `Not Full Market Price` and `VAT Exclusive` matter for analysis.

**Daft sold** is not a second register. Cards already show a PPR-style sold price plus listing attributes. Coverage is only homes that were listed (or otherwise sourced) on Daft; unmatched PPR rows are expected. `drogheda-louth` still returns Meath addresses (Colpe East, Bryanstown Wood, Grangerath, Julianstown). This pipeline keeps `county == Louth` from PPR and drops Daft rows whose address county is Meath.

Daft’s partner SOAP API is not a public sold-history API. HTML/JSON responses are cached, throttled, and fetched with a contactable user-agent. Do not use this commercially without a licence.

## Setup

Python 3.12+. From the repo root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Pipeline

Default warehouse: `data/warehouse.sqlite`. Default exports: `data/exports/`.

```bash
.venv/bin/ppr-analysis ingest-ppr --years 5
.venv/bin/ppr-analysis ingest-daft --delay 2
.venv/bin/ppr-analysis match
.venv/bin/ppr-analysis export
```

Or one shot: `.venv/bin/ppr-analysis run --years 5`.

Useful flags:

- `--csv` / `--zip` to ingest a local PPR extract instead of downloading `PPR-ALL.zip`
- `--max-pages N` to cap the Daft crawl while testing
- `--refresh` to ignore the HTTP cache
- `--fallback-search` on `match` to query Daft by address only for unmatched PPR rows that already have a house/apartment number and an Eircode

Matching is fuzzy (RapidFuzz `token_set_ratio`) plus house/apartment id, with sold-price and date corroboration. Statuses: `exact`, `high`, `review`, `unmatched`. Unmatched PPR rows stay in the export.

Market summaries (median €/m², sale vs asking, median price by type and beds) exclude `not_full_market_price` by default.

## Map site

Addresses are geocoded once with Nominatim (cached in SQLite; 1 request/second; OSM attribution required). Points outside a Drogheda bounding box are omitted from the map.

```bash
.venv/bin/ppr-analysis geocode
.venv/bin/ppr-analysis export-web
cd web && npm install && npm run dev
```

That writes `web/public/data/sales.geojson` and `summary.json`. Filter by house type, bedrooms, and floor-area band. Marker popups link to Daft when a match exists. Later you can host `web/dist` as a static site.

## Information note

PPR figures are public register extracts. They can contain errors and are facts about *price and date*, not a complete property description. Daft attributes are ToS-constrained: cache, throttle, personal use only.
