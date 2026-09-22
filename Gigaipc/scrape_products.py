"""
Scrape the full GIGAIPC product catalog (https://www.gigaipc.com/en/products)
and write it to a CSV with columns: model_name, category, url, specifications,
features_summary, image_url.

Usage:
    python scrape_products.py [--output gigaipc_products_complete.csv]
                               [--workers 5] [--delay 0.4] [--limit N]
"""

import argparse
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


def scrape_one(session, slug_or_url, delay):
    detail_url = detail_url_from_slug(slug_or_url)
    html = fetch(session, detail_url)
    time.sleep(delay)
    row = parse_product_detail(html, detail_url)
    row["specifications"] = str(row["specifications"])
    row["features_summary"] = str(row["features_summary"])
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="gigaipc_products_complete.csv")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.4, help="seconds to wait after each detail-page fetch")
    parser.add_argument("--limit", type=int, default=None, help="only scrape the first N products (for testing)")
    args = parser.parse_args()

    session = make_session()

    print("Discovering product URLs from the catalog listing...")
    urls = list_product_urls(session, delay=args.delay, log=print)
    if args.limit:
        urls = urls[: args.limit]
    print(f"Found {len(urls)} product URLs. Fetching details with {args.workers} workers...")

    rows = []
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_url = {pool.submit(scrape_one, session, u, args.delay): u for u in urls}
        done = 0
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            done += 1
            try:
                rows.append(future.result())
            except Exception as exc:
                errors.append((url, str(exc)))
                print(f"  [{done}/{len(urls)}] FAILED  {url}: {exc}", file=sys.stderr)
            else:
                print(f"  [{done}/{len(urls)}] OK      {url}")

    rows.sort(key=lambda r: r["model_name"].lower())

    with open(args.output, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} products to {args.output}")
    if errors:
        print(f"{len(errors)} URLs failed to scrape:", file=sys.stderr)
        for url, err in errors:
            print(f"  {url}: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
