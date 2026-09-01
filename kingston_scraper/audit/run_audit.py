"""Data-freshness auditor: re-fetches a sample of URLs already represented
in output/kingston_servers.csv / kingston_memory_parts.csv /
kingston_ssd_parts.csv straight from the live site, re-extracts them with
the project's own extraction code (agent.extraction / agent.parts_extractor
— the exact functions runner.py uses), and diffs the fresh result against
what's already on disk.

This is deliberately separate from tests/test_live_playwright_extraction.py:
that module proves the parser matches the live DOM on one fixture URL, as a
correctness test you run when extraction logic changes. This module proves
the *already-shipped output* still matches the live site, over a rotating
sample of real output, as a recurring data-QA job.

    python -m audit.run_audit                  # audit a small rotating sample (default 15 servers)
    python -m audit.run_audit --sample-size 30
    python -m audit.run_audit --skip-parts      # server-level fields only, faster/fewer requests

Respects the same Cloudflare-safety posture as the scraper itself
(REQUEST_DELAY_SECONDS, COOLDOWN_TRIGGER_STREAK/COOLDOWN_SECONDS, MAX_RETRIES
— see README "Known limitation: Cloudflare"). Meant to be run occasionally
(cron/scheduled task), from a normal network, not per-commit in CI.
"""

import argparse
import csv
import time
from collections import defaultdict
from datetime import datetime, timezone

from agent.browser_agent import BrowserAgent
from agent.error_handler import ScrapeError, FailureType
from agent.extraction import extract_server_specs
from agent.navigation import open_server_page
from agent.parts_extractor import extract_parts
from agent.ssd_extractor import SsdProductCache
from audit.diff import diff_server, diff_parts
from audit.report import write_json_report, append_summary_row
from audit.seen_store import AuditSeenStore
from config.config import (
    SERVERS_CSV_PATH,
    MEMORY_PARTS_CSV_PATH,
    SSD_PARTS_CSV_PATH,
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    REQUEST_DELAY_SECONDS,
    COOLDOWN_TRIGGER_STREAK,
    COOLDOWN_SECONDS,
)
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SAMPLE_SIZE = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv_rows(path) -> list:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except FileNotFoundError:
        logger.warning("Output file not found, treating as empty: %s", path)
        return []


def _group_by_server_url(rows: list) -> dict:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("server_url", "")].append(row)
    return grouped


def _fetch_with_retries(agent, url: str, label: str):
    """Same retry shape as runner.py's per-server loop, kept local and
    small since the audit doesn't need the full failed-URL/status-store
    machinery a real scrape run does."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return open_server_page(agent, url, debug_label=label)
        except ScrapeError as exc:
            last_error = exc
            logger.warning("Audit fetch attempt %d/%d failed [%s]: %s", attempt, MAX_RETRIES, exc.failure_type, exc.reason)
            if attempt < MAX_RETRIES:
                agent.new_page()
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise last_error


def run_audit(sample_size: int = DEFAULT_SAMPLE_SIZE, skip_parts: bool = False) -> dict:
    server_rows = _read_csv_rows(SERVERS_CSV_PATH)
    memory_rows_by_url = _group_by_server_url(_read_csv_rows(MEMORY_PARTS_CSV_PATH))
    ssd_rows_by_url = _group_by_server_url(_read_csv_rows(SSD_PARTS_CSV_PATH))

    if not server_rows:
        logger.warning("No rows in %s — nothing to audit yet.", SERVERS_CSV_PATH)
        return {"servers_audited": 0}

    seen_store = AuditSeenStore()
    server_by_url = {r["server_url"]: r for r in server_rows if r.get("server_url")}
    ordered_urls = seen_store.sort_by_staleness(list(server_by_url.keys()))
    sample_urls = ordered_urls[:sample_size]

    logger.info("Auditing %d/%d server URLs this run (rotating sample)", len(sample_urls), len(server_by_url))

    results = []
    unreachable = 0
    consecutive_blocked = 0
    ssd_cache = SsdProductCache()

    agent = BrowserAgent().start()
    try:
        for index, url in enumerate(sample_urls, start=1):
            old_row = server_by_url[url]
            label = f"audit_{index}"

            if consecutive_blocked >= COOLDOWN_TRIGGER_STREAK:
                logger.warning(
                    "%d audit fetches in a row blocked — cooling down for %.0fs (see README "
                    "'Known limitation: Cloudflare').", consecutive_blocked, COOLDOWN_SECONDS,
                )
                time.sleep(COOLDOWN_SECONDS)
                consecutive_blocked = 0

            logger.info("[%d/%d] %s", index, len(sample_urls), url)
            try:
                soup = _fetch_with_retries(agent, url, label)
            except ScrapeError as exc:
                unreachable += 1
                consecutive_blocked += 1
                results.append({
                    "server_url": url,
                    "server_name": old_row.get("server_name", ""),
                    "unreachable": True,
                    "failure_type": exc.failure_type,
                    "reason": exc.reason,
                })
                agent.new_page()
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            consecutive_blocked = 0
            live_record = extract_server_specs(soup, old_row.get("server_name", ""), url)
            server_mismatches, server_drift = diff_server(old_row, live_record)

            entry = {
                "server_url": url,
                "server_name": old_row.get("server_name", ""),
                "unreachable": False,
                "server_mismatches": server_mismatches,
                "server_drift": server_drift,
            }

            if not skip_parts:
                live_parts = extract_parts(
                    soup, old_row.get("server_name", ""), url, agent=agent, ssd_cache=ssd_cache
                )
                old_parts_rows = memory_rows_by_url.get(url, []) + ssd_rows_by_url.get(url, [])
                entry["parts_diff"] = diff_parts(old_parts_rows, live_parts)

            results.append(entry)
            seen_store.mark_audited(url)
            time.sleep(REQUEST_DELAY_SECONDS)
    finally:
        agent.stop()
        seen_store.save()

    report = {
        "run_timestamp": _now_iso(),
        "sample_size_requested": sample_size,
        "servers_audited": len(sample_urls) - unreachable,
        "servers_unreachable": unreachable,
        "results": results,
    }
    write_json_report(report)

    summary = {
        "timestamp": report["run_timestamp"],
        "servers_audited": report["servers_audited"],
        "servers_unreachable": unreachable,
        "server_mismatches": sum(len(r.get("server_mismatches", [])) for r in results),
        "server_drift": sum(len(r.get("server_drift", [])) for r in results),
        "part_mismatches": sum(len(r.get("parts_diff", {}).get("mismatches", [])) for r in results),
        "part_drift": sum(len(r.get("parts_diff", {}).get("drift", [])) for r in results),
        "parts_missing_on_live": sum(len(r.get("parts_diff", {}).get("missing_on_live", [])) for r in results),
        "parts_new_on_live": sum(len(r.get("parts_diff", {}).get("new_on_live", [])) for r in results),
    }
    append_summary_row(summary)
    return summary


def parse_args():
    parser = argparse.ArgumentParser(description="Audit kingston_scraper output against the live site")
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE, help="How many server URLs to re-check this run")
    parser.add_argument("--skip-parts", action="store_true", help="Only audit server-level fields, skip memory/SSD parts re-extraction")
    return parser.parse_args()


def main():
    args = parse_args()
    summary = run_audit(sample_size=args.sample_size, skip_parts=args.skip_parts)
    print("\n--- Audit summary ---")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
