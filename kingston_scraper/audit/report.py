"""Writes the audit run's findings to output/audit_report.json (full detail,
for a human/CI to inspect) and appends one summary row per run to
output/audit_summary.csv (for tracking mismatch/drift counts over time —
one auditor run per row, so a trend of rising mismatches is visible without
opening the JSON)."""

import csv
import json
from pathlib import Path

from config.config import OUTPUT_DIR
from utils.logger import get_logger

logger = get_logger(__name__)

AUDIT_REPORT_JSON_PATH = OUTPUT_DIR / "audit_report.json"
AUDIT_SUMMARY_CSV_PATH = OUTPUT_DIR / "audit_summary.csv"

_SUMMARY_COLUMNS = [
    "timestamp",
    "servers_audited",
    "servers_unreachable",
    "server_mismatches",
    "server_drift",
    "part_mismatches",
    "part_drift",
    "parts_missing_on_live",
    "parts_new_on_live",
]


def write_json_report(report: dict, path=None):
    path = Path(path or AUDIT_REPORT_JSON_PATH)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)
    logger.info("Wrote full audit report: %s", path)


def append_summary_row(summary: dict, path=None):
    path = Path(path or AUDIT_SUMMARY_CSV_PATH)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_SUMMARY_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow({col: summary.get(col, "") for col in _SUMMARY_COLUMNS})
    logger.info("Appended run summary: %s", path)
