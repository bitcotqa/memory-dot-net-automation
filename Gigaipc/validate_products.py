"""
Audit an existing GIGAIPC product CSV against the live site.

Re-crawls https://www.gigaipc.com/en/products, re-fetches every product-detail
page, and compares each field against what is stored in the local CSV. Writes:

  - gigaipc_match_report.csv:       one row per product URL (from the CSV,
    the live site, or both), with a per-field CSV-vs-live comparison.
  - gigaipc_missing_products_report.csv: full rows (in the CSV's own column
    format) for products that exist live but are absent from the CSV.

Usage:
    python validate_products.py --input gigaipc_products_complete.csv
                                 [--match-report gigaipc_match_report.csv]
                                 [--missing-report gigaipc_missing_products_report.csv]
                                 [--workers 5] [--delay 0.4]
"""

import argparse
import ast
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from gigaipc_lib import (
    detail_url_from_slug,
    fetch,
    list_product_urls,
    make_session,
    parse_product_detail,
    CSV_FIELDS,
)

COMPARE_FIELDS = ["model_name", "category", "specifications", "features_summary", "image_url"]
MATCH_REPORT_FIELDS = (
    ["url", "status", "row_match"]
    + [f"{f}_{suffix}" for f in COMPARE_FIELDS for suffix in ("csv", "actual", "match")]
    + ["notes"]
)


def load_csv(path):
    """Load the existing products CSV into {url: row_dict}, keyed by the
    canonical (trailing-slash-stripped) URL."""
    products = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            url = row.get("url", "").strip().rstrip("/")
            if url:
                products[url] = row
    return products


def parse_literal(value, fallback):
    """Best-effort ast.literal_eval of a stored dict/set repr string."""
    if not value:
        return fallback
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return fallback


def compare_simple(csv_val, actual_val):
    csv_val = (csv_val or "").strip()
    actual_val = (actual_val or "").strip()
    if csv_val == actual_val:
        return "YES", None
    return "NO", f"value differs: '{csv_val}' vs '{actual_val}'"


def compare_set_field(field_name, csv_val, actual_val):
    csv_set = parse_literal(csv_val, set())
    actual_set = parse_literal(actual_val, set())
    if csv_set == actual_set:
        return "YES", None
    only_csv = sorted(csv_set - actual_set)
    only_actual = sorted(actual_set - csv_set)
    parts = []
    if only_csv:
        parts.append(f"only in CSV: {only_csv}")
    if only_actual:
        parts.append(f"only on live site: {only_actual}")
    return "NO", f"{field_name} differ ({'; '.join(parts)})"


def compare_dict_field(field_name, csv_val, actual_val):
    csv_dict = parse_literal(csv_val, {})
    actual_dict = parse_literal(actual_val, {})
    if csv_dict == actual_dict:
        return "YES", None
    only_csv_keys = sorted(set(csv_dict) - set(actual_dict))
    only_actual_keys = sorted(set(actual_dict) - set(csv_dict))
    changed_keys = sorted(
        k for k in set(csv_dict) & set(actual_dict) if csv_dict[k] != actual_dict[k]
    )
    parts = []
    if only_csv_keys:
        parts.append(f"keys only in CSV: {only_csv_keys}")
    if only_actual_keys:
        parts.append(f"keys only on live site: {only_actual_keys}")
    if changed_keys:
        parts.append(f"changed keys: {changed_keys}")
    return "NO", f"{field_name} differ ({'; '.join(parts)})"


def compare_row(csv_row, actual_row):
    """Compare one product's CSV row vs freshly scraped actual row (actual
    row values are already-parsed python objects for specifications /
    features_summary). Returns (report_dict_fields, all_match_bool)."""
    fields = {}
    notes = []
    all_match = True
    for field in COMPARE_FIELDS:
        csv_val = csv_row.get(field, "") if csv_row else ""
        actual_val = actual_row.get(field, "") if actual_row else ""

        if csv_row is None or actual_row is None:
            match = "N/A"
        elif field == "specifications":
            actual_str = str(actual_val)
            match, note = compare_dict_field(field, csv_val, actual_str)
            actual_val = actual_str
            if note:
                notes.append(note)
        elif field == "features_summary":
            actual_str = str(actual_val)
            match, note = compare_set_field(field, csv_val, actual_str)
            actual_val = actual_str
            if note:
                notes.append(note)
        else:
            match, note = compare_simple(csv_val, actual_val)
            if note:
                notes.append(note)

        if match == "NO":
            all_match = False

        fields[f"{field}_csv"] = csv_val
        fields[f"{field}_actual"] = actual_val
        fields[f"{field}_match"] = match

    return fields, all_match, notes


def fetch_actual(session, url, delay):
    detail_url = detail_url_from_slug(url)
    html = fetch(session, detail_url)
    time.sleep(delay)
    row = parse_product_detail(html, detail_url)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="existing products CSV to audit")
    parser.add_argument("--match-report", default="gigaipc_match_report.csv")
    parser.add_argument("--missing-report", default="gigaipc_missing_products_report.csv")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--limit", type=int, default=None, help="only check the first N live URLs (for testing)")
    args = parser.parse_args()

    print(f"Loading existing catalog from {args.input}...")
    csv_products = load_csv(args.input)
    print(f"  {len(csv_products)} products in CSV")

    session = make_session()
    print("Discovering live product URLs from the catalog listing...")
    live_urls = list_product_urls(session, delay=args.delay, log=print)
    if args.limit:
        live_urls = live_urls[: args.limit]
    print(f"  {len(live_urls)} live product URLs")

    all_urls = sorted(set(csv_products) | set(live_urls))

    actuals = {}
    errors = []
    print(f"Fetching {len(all_urls)} live product pages with {args.workers} workers...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_url = {pool.submit(fetch_actual, session, u, args.delay): u for u in all_urls}
        done = 0
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            done += 1
            try:
                actuals[url] = future.result()
            except Exception as exc:
                errors.append((url, str(exc)))
                print(f"  [{done}/{len(all_urls)}] FAILED  {url}: {exc}", file=sys.stderr)
            else:
                print(f"  [{done}/{len(all_urls)}] OK      {url}")

    match_rows = []
    missing_rows = []

    for url in all_urls:
        csv_row = csv_products.get(url)
        actual_row = actuals.get(url)

        in_csv = csv_row is not None
        in_live = url in actuals and actual_row is not None
        fetch_failed = url in dict(errors)

        if in_csv and not in_live and fetch_failed:
            status = "LIVE_FETCH_FAILED"
        elif in_csv and not in_live:
            status = "MISSING_FROM_LIVE_SITE"
        elif not in_csv and in_live:
            status = "MISSING_FROM_CSV"
        else:
            status = None  # determined below from field comparison

        fields, all_match, notes = compare_row(csv_row, actual_row)

        if status is None:
            status = "MATCHED" if all_match else "UNMATCHED"

        row_match = "YES" if status == "MATCHED" else "NO"

        if status == "MISSING_FROM_CSV":
            notes = ["Product exists on live site but this row is absent from the CSV entirely"]
        elif status == "MISSING_FROM_LIVE_SITE":
            notes = ["Product is in the CSV but was not found on the live site (removed, renamed, or fetch error)"]

        report_row = {"url": url, "status": status, "row_match": row_match}
        report_row.update(fields)
        report_row["notes"] = "; ".join(notes)
        match_rows.append(report_row)

        if status == "MISSING_FROM_CSV" and actual_row:
            missing_rows.append(
                {
                    "model_name": actual_row["model_name"],
                    "category": actual_row["category"],
                    "url": actual_row["url"],
                    "specifications": str(actual_row["specifications"]),
                    "features_summary": str(actual_row["features_summary"]),
                    "image_url": actual_row["image_url"],
                }
            )

    with open(args.match_report, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=MATCH_REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(match_rows)

    with open(args.missing_report, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(missing_rows)

    from collections import Counter

    counts = Counter(r["status"] for r in match_rows)
    print("\nSummary:")
    for status, count in counts.most_common():
        print(f"  {status}: {count}")
    print(f"\nWrote {len(match_rows)} rows to {args.match_report}")
    print(f"Wrote {len(missing_rows)} rows to {args.missing_report}")


if __name__ == "__main__":
    main()
