# GIGAIPC Product Catalog Scraper & Validator

Scrapes the GIGAIPC industrial-PC product catalog
(https://www.gigaipc.com/en/products) and audits an existing catalog CSV
against the live site, field by field.

## About This Task

GIGAIPC's product catalog (specs, categories, images) is maintained as a CSV
export used downstream for comparison/reporting. That export can drift from
the live site over time as GIGAIPC updates product pages, so this tool has
two jobs:

1. **Scrape** — build a fresh catalog CSV directly from the live site.
2. **Validate** — take an existing catalog CSV (e.g. one someone else
   delivered) and audit it against the live site, field by field, producing
   a report that shows exactly what's correct, what's changed, and what's
   missing on either side. That report is what gets shared back as QA
   feedback.

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
- Match reports and product-catalog CSVs are sorted differently (report
  rows are sorted alphabetically by URL; a hand-maintained catalog CSV may
  use its own order), so always cross-reference rows by `url` /
  `model_name`, never by row number.
