# GIGAIPC Product Catalog Scraper & Validator

Scrapes the GIGAIPC industrial-PC product catalog
(https://www.gigaipc.com/en/products) and audits an existing catalog CSV
against the live site, field by field.

## Setup

```
pip install -r requirements.txt
```

## Usage

**Scrape the full catalog:**

```
python scrape_products.py --output output/gigaipc_products.csv
```

Crawls the paginated `/en/products` listing to discover every product URL,
then fetches each product-detail page and extracts `model_name`, `category`,
`specifications` (dict), `features_summary` (set), and `image_url`.

**Audit an existing catalog CSV against the live site:**

```
python validate_products.py --input output/gigaipc_products.csv
```

Re-crawls the live catalog and compares it against the given CSV row by row,
field by field. Writes:

- `gigaipc_match_report.csv` — one row per product URL (from the CSV, the
  live site, or both), with a per-field CSV-vs-live comparison
  (`MATCHED` / `UNMATCHED` / `MISSING_FROM_CSV` / `MISSING_FROM_LIVE_SITE`)
  and a `notes` column explaining any diff.
- `gigaipc_missing_products_report.csv` — full rows (in the catalog's own
  column format) for products that exist live but are absent from the CSV.

Both scripts accept `--workers` and `--delay` to tune request concurrency /
politeness, and `--limit` to cap the run for testing.

## Output

`output/` holds the current verified snapshot:

- `gigaipc_products.csv` — 270 products, every field verified against the
  live site (see `gigaipc_match_report.csv`: 270/270 `MATCHED`).
- `gigaipc_match_report.csv` — the full audit trail behind that verification.
- `gigaipc_missing_products_report.csv` — logs one anomaly found on the live
  site: `products-detail/test123` (model name `愛貝斯測試`), an empty
  placeholder/test page with no category or specifications. Not a real
  product, intentionally left out of the catalog.

## Notes

- `specifications` and `features_summary` are stored as Python literal
  `dict`/`set` reprs (e.g. `{'CPU': ['...'], ...}`), matching the format of
  the original catalog export this tool was built to audit. Parse them with
  `ast.literal_eval`, not JSON.
- `category` is taken from the top-level breadcrumb link on each product
  page (e.g. "Industrial Motherboards"), not the narrower sub-series tag
  shown next to the product title.
