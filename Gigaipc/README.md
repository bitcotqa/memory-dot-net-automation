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
python scrape_products.py --output gigaipc_products.csv
```

Crawls the paginated `/en/products` listing to discover every product URL,
then fetches each product-detail page and extracts `model_name`, `category`,
`specifications` (dict), `features_summary` (set), and `image_url`.

**Audit an existing catalog CSV against the live site:**

```
python validate_products.py --input gigaipc_products.csv
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

Generated CSVs are run output, not part of this repo — run the scripts
locally to produce them.

## Notes

- `specifications` and `features_summary` are stored as Python literal
  `dict`/`set` reprs (e.g. `{'CPU': ['...'], ...}`), matching the format of
  the original catalog export this tool was built to audit. Parse them with
  `ast.literal_eval`, not JSON.
- `category` is taken from the top-level breadcrumb link on each product
  page (e.g. "Industrial Motherboards"), not the narrower sub-series tag
  shown next to the product title.
