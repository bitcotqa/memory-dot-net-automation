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

import argparse                                          # parses the CLI flags (--input, --match-report, etc.)
import ast                                                # ast.literal_eval turns a stored "{'a': [...]}" string back into a real dict/set
import csv                                                # reads the input catalog CSV and writes both report CSVs
import sys                                                # stderr output for per-URL fetch failures
import time                                               # time.sleep() for the polite per-request delay
from concurrent.futures import ThreadPoolExecutor, as_completed   # fetches live product pages concurrently

from gigaipc_lib import (                                 # the shared scraping/parsing helpers
    detail_url_from_slug,
    fetch,
    list_product_urls,
    make_session,
    parse_product_detail,
    CSV_FIELDS,
)

COMPARE_FIELDS = ["model_name", "category", "specifications", "features_summary", "image_url"]  # every field we diff (url is the join key, not compared)
MATCH_REPORT_FIELDS = (                                   # builds the match-report header: url, status, row_match,
    ["url", "status", "row_match"]                         # then <field>_csv / <field>_actual / <field>_match for
    + [f"{f}_{suffix}" for f in COMPARE_FIELDS for suffix in ("csv", "actual", "match")]   # each field in COMPARE_FIELDS,
    + ["notes"]                                             # and finally a free-text notes column
)


def load_csv(path):
    """Load the existing products CSV into {url: row_dict}, keyed by the
    canonical (trailing-slash-stripped) URL."""
    products = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:   # utf-8-sig transparently handles a leading BOM if present
        reader = csv.DictReader(fh)
        for row in reader:
            url = row.get("url", "").strip().rstrip("/")    # normalize the same way gigaipc_lib does, so URLs from
            if url:                                          # both the CSV and the live scrape line up as the same key
                products[url] = row
    return products


def parse_literal(value, fallback):
    """Best-effort ast.literal_eval of a stored dict/set repr string."""
    if not value:                              # an empty cell (e.g. a MISSING_FROM_CSV row) has nothing to parse
        return fallback
    try:
        return ast.literal_eval(value)          # safely evaluate a Python literal string, e.g. "{'CPU': [...]}"
    except (ValueError, SyntaxError):           # if the cell is malformed/unparseable, don't crash the whole run
        return fallback


def compare_simple(csv_val, actual_val):
    csv_val = (csv_val or "").strip()           # treat None and whitespace-only the same as an empty string
    actual_val = (actual_val or "").strip()
    if csv_val == actual_val:
        return "YES", None                      # no note needed when the values already match
    return "NO", f"value differs: '{csv_val}' vs '{actual_val}'"   # plain-text fields (model_name, category, image_url)


def compare_set_field(field_name, csv_val, actual_val):
    csv_set = parse_literal(csv_val, set())            # parse both sides back into real Python sets for comparison
    actual_set = parse_literal(actual_val, set())
    if csv_set == actual_set:
        return "YES", None
    only_csv = sorted(csv_set - actual_set)             # items present in the CSV but no longer on the live site
    only_actual = sorted(actual_set - csv_set)          # items present live but missing from the CSV
    parts = []
    if only_csv:
        parts.append(f"only in CSV: {only_csv}")
    if only_actual:
        parts.append(f"only on live site: {only_actual}")
    return "NO", f"{field_name} differ ({'; '.join(parts)})"   # used for features_summary


def compare_dict_field(field_name, csv_val, actual_val):
    csv_dict = parse_literal(csv_val, {})               # parse both sides back into real Python dicts for comparison
    actual_dict = parse_literal(actual_val, {})
    if csv_dict == actual_dict:
        return "YES", None
    only_csv_keys = sorted(set(csv_dict) - set(actual_dict))     # spec keys that dropped off the live page
    only_actual_keys = sorted(set(actual_dict) - set(csv_dict))  # spec keys that are new on the live page
    changed_keys = sorted(                                       # keys present on both sides but with a different value
        k for k in set(csv_dict) & set(actual_dict) if csv_dict[k] != actual_dict[k]
    )
    parts = []
    if only_csv_keys:
        parts.append(f"keys only in CSV: {only_csv_keys}")
    if only_actual_keys:
        parts.append(f"keys only on live site: {only_actual_keys}")
    if changed_keys:
        parts.append(f"changed keys: {changed_keys}")
    return "NO", f"{field_name} differ ({'; '.join(parts)})"   # used for specifications


def compare_row(csv_row, actual_row):
    """Compare one product's CSV row vs freshly scraped actual row (actual
    row values are already-parsed python objects for specifications /
    features_summary). Returns (report_dict_fields, all_match_bool)."""
    fields = {}
    notes = []
    all_match = True
    for field in COMPARE_FIELDS:
        csv_val = csv_row.get(field, "") if csv_row else ""       # empty when the product doesn't exist in the CSV at all
        actual_val = actual_row.get(field, "") if actual_row else ""   # empty when it doesn't exist live at all

        if csv_row is None or actual_row is None:      # can't meaningfully compare a field when one whole side is missing
            match = "N/A"
        elif field == "specifications":
            actual_str = str(actual_val)                # actual_row's specifications is still a real dict at this point;
            match, note = compare_dict_field(field, csv_val, actual_str)   # stringify it the same way the CSV stores it
            actual_val = actual_str                      # so the report column shows the same literal-string format as the CSV
            if note:
                notes.append(note)
        elif field == "features_summary":
            actual_str = str(actual_val)                # same idea, but for the set-valued field
            match, note = compare_set_field(field, csv_val, actual_str)
            actual_val = actual_str
            if note:
                notes.append(note)
        else:
            match, note = compare_simple(csv_val, actual_val)   # model_name / category / image_url are plain strings
            if note:
                notes.append(note)

        if match == "NO":
            all_match = False                            # one mismatched field is enough to mark the whole row UNMATCHED

        fields[f"{field}_csv"] = csv_val
        fields[f"{field}_actual"] = actual_val
        fields[f"{field}_match"] = match

    return fields, all_match, notes


def fetch_actual(session, url, delay):
    detail_url = detail_url_from_slug(url)      # normalize the stored URL/slug into a full product-detail URL
    html = fetch(session, detail_url)            # download the live page
    time.sleep(delay)                            # pause here (inside the worker) so concurrent workers stay polite together
    row = parse_product_detail(html, detail_url)  # extract the current live values for this product
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="existing products CSV to audit")
    parser.add_argument("--match-report", default="gigaipc_match_report.csv")       # full per-field comparison output
    parser.add_argument("--missing-report", default="gigaipc_missing_products_report.csv")   # live-only products output
    parser.add_argument("--workers", type=int, default=5)         # how many live product pages to fetch in parallel
    parser.add_argument("--delay", type=float, default=0.4)       # seconds to pause after each fetch, per worker
    parser.add_argument("--limit", type=int, default=None, help="only check the first N live URLs (for testing)")
    args = parser.parse_args()

    print(f"Loading existing catalog from {args.input}...")
    csv_products = load_csv(args.input)                # {url: row} for everything currently in the CSV
    print(f"  {len(csv_products)} products in CSV")

    session = make_session()
    print("Discovering live product URLs from the catalog listing...")
    live_urls = list_product_urls(session, delay=args.delay, log=print)   # every product URL the live site currently has
    if args.limit:
        live_urls = live_urls[: args.limit]             # optionally cap the run for a quick test
    print(f"  {len(live_urls)} live product URLs")

    all_urls = sorted(set(csv_products) | set(live_urls))   # union: every URL from either side gets a report row

    actuals = {}   # {url: freshly-scraped row dict}, filled in by the thread pool below
    errors = []    # (url, error message) for any live page that failed to fetch
    print(f"Fetching {len(all_urls)} live product pages with {args.workers} workers...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_url = {pool.submit(fetch_actual, session, u, args.delay): u for u in all_urls}   # fetch every URL, whether it's in the CSV, live, or both
        done = 0
        for future in as_completed(future_to_url):       # process results as each worker finishes
            url = future_to_url[future]
            done += 1
            try:
                actuals[url] = future.result()
            except Exception as exc:
                errors.append((url, str(exc)))
                print(f"  [{done}/{len(all_urls)}] FAILED  {url}: {exc}", file=sys.stderr)
            else:
                print(f"  [{done}/{len(all_urls)}] OK      {url}")

    match_rows = []      # rows for gigaipc_match_report.csv
    missing_rows = []    # rows for gigaipc_missing_products_report.csv

    for url in all_urls:
        csv_row = csv_products.get(url)     # None if this URL isn't in the CSV
        actual_row = actuals.get(url)       # None if we couldn't fetch/find it live

        in_csv = csv_row is not None
        in_live = url in actuals and actual_row is not None
        fetch_failed = url in dict(errors)   # True if this URL is in the CSV but its live fetch raised an exception

        if in_csv and not in_live and fetch_failed:
            status = "LIVE_FETCH_FAILED"                # couldn't check it live due to a network/HTTP error, not because it's gone
        elif in_csv and not in_live:
            status = "MISSING_FROM_LIVE_SITE"            # in the CSV, but no longer found in the live catalog listing
        elif not in_csv and in_live:
            status = "MISSING_FROM_CSV"                  # exists live, but this URL never made it into the CSV
        else:
            status = None  # both sides have it -- determined below from the actual field-by-field comparison

        fields, all_match, notes = compare_row(csv_row, actual_row)

        if status is None:
            status = "MATCHED" if all_match else "UNMATCHED"   # every field agreed, or at least one field differs

        row_match = "YES" if status == "MATCHED" else "NO"     # a simple pass/fail column alongside the detailed status

        if status == "MISSING_FROM_CSV":                        # override the per-field notes with one clear explanation
            notes = ["Product exists on live site but this row is absent from the CSV entirely"]
        elif status == "MISSING_FROM_LIVE_SITE":
            notes = ["Product is in the CSV but was not found on the live site (removed, renamed, or fetch error)"]

        report_row = {"url": url, "status": status, "row_match": row_match}
        report_row.update(fields)                                # adds the <field>_csv / _actual / _match columns
        report_row["notes"] = "; ".join(notes)
        match_rows.append(report_row)

        if status == "MISSING_FROM_CSV" and actual_row:          # also log a full catalog-format row for this product,
            missing_rows.append(                                  # so it can be reviewed/added without re-scraping it
                {
                    "model_name": actual_row["model_name"],
                    "category": actual_row["category"],
                    "url": actual_row["url"],
                    "specifications": str(actual_row["specifications"]),   # stringify to match the catalog CSV's format
                    "features_summary": str(actual_row["features_summary"]),
                    "image_url": actual_row["image_url"],
                }
            )

    with open(args.match_report, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=MATCH_REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(match_rows)

    with open(args.missing_report, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)   # same column layout as the main catalog CSV
        writer.writeheader()
        writer.writerows(missing_rows)

    from collections import Counter          # imported here since it's only needed for this one summary line

    counts = Counter(r["status"] for r in match_rows)
    print("\nSummary:")
    for status, count in counts.most_common():   # most-common-first, so the biggest buckets are easy to spot
        print(f"  {status}: {count}")
    print(f"\nWrote {len(match_rows)} rows to {args.match_report}")
    print(f"Wrote {len(missing_rows)} rows to {args.missing_report}")


if __name__ == "__main__":   # only run main() when executed directly, not when imported elsewhere
    main()
