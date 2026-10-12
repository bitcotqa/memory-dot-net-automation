"""
Scrape the full GIGAIPC product catalog (https://www.gigaipc.com/en/products)
and write it to a CSV with columns: model_name, category, url, specifications,
features_summary, image_url.

Usage:
    python scrape_products.py [--output gigaipc_products_complete.csv]
                               [--workers 5] [--delay 0.4] [--limit N]
"""

import argparse                                          # parses the --output/--workers/--delay/--limit CLI flags
import csv                                                # writes the final rows out as a CSV file
import sys                                                # used for stderr output and a non-zero exit code on failure
import time                                               # time.sleep() for the polite per-request delay
from concurrent.futures import ThreadPoolExecutor, as_completed   # runs multiple detail-page fetches concurrently

from gigaipc_lib import (                                 # the shared scraping/parsing helpers
    detail_url_from_slug,
    fetch,
    list_product_urls,
    make_session,
    parse_product_detail,
    CSV_FIELDS,
)


def scrape_one(session, slug_or_url, delay):
    detail_url = detail_url_from_slug(slug_or_url)         # normalize whatever we were given into a full detail-page URL
    html = fetch(session, detail_url)                       # download that product's page
    time.sleep(delay)                                       # pause here (inside the worker) so concurrent workers stay polite together
    row = parse_product_detail(html, detail_url)             # extract model_name/category/specifications/features_summary/image_url
    row["specifications"] = str(row["specifications"])       # convert the dict to its Python-literal string form for the CSV cell
    row["features_summary"] = str(row["features_summary"])   # same for the set, so it round-trips via ast.literal_eval later
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="gigaipc_products_complete.csv")   # where to write the resulting catalog CSV
    parser.add_argument("--workers", type=int, default=5)                     # how many product pages to fetch in parallel
    parser.add_argument("--delay", type=float, default=0.4, help="seconds to wait after each detail-page fetch")
    parser.add_argument("--limit", type=int, default=None, help="only scrape the first N products (for testing)")
    args = parser.parse_args()

    session = make_session()   # one shared HTTP session (connection pooling + retry policy) for every request below

    print("Discovering product URLs from the catalog listing...")
    urls = list_product_urls(session, delay=args.delay, log=print)   # crawl every listing page to find all product URLs
    if args.limit:
        urls = urls[: args.limit]                                    # optionally cap the run for a quick test
    print(f"Found {len(urls)} product URLs. Fetching details with {args.workers} workers...")

    rows = []      # successfully-scraped product rows, in whatever order the workers finish
    errors = []    # (url, error message) pairs for any product page that failed to scrape
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_url = {pool.submit(scrape_one, session, u, args.delay): u for u in urls}   # kick off all fetches at once
        done = 0
        for future in as_completed(future_to_url):   # process results as each worker finishes, not in submission order
            url = future_to_url[future]
            done += 1
            try:
                rows.append(future.result())          # re-raises any exception the worker hit, so we can catch it below
            except Exception as exc:
                errors.append((url, str(exc)))
                print(f"  [{done}/{len(urls)}] FAILED  {url}: {exc}", file=sys.stderr)
            else:
                print(f"  [{done}/{len(urls)}] OK      {url}")

    rows.sort(key=lambda r: r["model_name"].lower())   # deterministic output order, independent of fetch completion order

    with open(args.output, "w", newline="", encoding="utf-8-sig") as fh:   # utf-8-sig so Excel opens accented text correctly
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} products to {args.output}")
    if errors:                                          # surface failures clearly and fail the run so CI/automation notices
        print(f"{len(errors)} URLs failed to scrape:", file=sys.stderr)
        for url, err in errors:
            print(f"  {url}: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":   # only run main() when executed directly, not when imported elsewhere
    main()
