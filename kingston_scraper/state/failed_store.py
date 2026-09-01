"""Tracks the *current* set of unresolved failed server URLs.

Unlike utils.io_utils.CsvWriter (append-only, dedup-on-write-only),
kingston_failed_urls.csv must behave as a live view of "servers that still
need a successful extraction" — a server is removed the moment its data
has been successfully persisted to the output CSVs, and re-added if it
fails again on a later run. It must never become a permanent historical
log; that job belongs to kingston_scrape_errors.csv (utils.io_utils.
AppendCsvLogger), which every failure attempt is also written to.

Every mutation rewrites the file immediately (atomically, via a temp file
+ rename) so a crash mid-run — including mid-retry-phase — never loses a
failure that hasn't actually been resolved yet, and never leaves the file
in a half-written state.
"""

import csv
from pathlib import Path

from models.models import FAILED_CSV_COLUMNS
from utils.logger import get_logger

logger = get_logger(__name__)


class FailedUrlStore:
    def __init__(self, path):
        self.path = Path(path)
        self._rows = {}  # url (lowercased) -> row dict
        self._order = []  # insertion order, for stable/deterministic output
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    url = (row.get("server_url") or "").strip()
                    if not url:
                        continue
                    key = url.lower()
                    if key not in self._rows:
                        self._order.append(key)
                    self._rows[key] = {col: row.get(col, "") for col in FAILED_CSV_COLUMNS}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read existing failed-URL CSV %s: %s", self.path, exc)

    def _write(self):
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FAILED_CSV_COLUMNS)
            writer.writeheader()
            for key in self._order:
                row = self._rows.get(key)
                if row:
                    writer.writerow(row)
        tmp_path.replace(self.path)

    def upsert(self, row: dict):
        """Record (or update) a current failure for this URL and persist
        immediately. Call this only *after* every retry attempt for the
        current phase is exhausted for that server."""
        url = (row.get("server_url") or "").strip()
        if not url:
            return
        key = url.lower()
        if key not in self._rows:
            self._order.append(key)
        self._rows[key] = {col: row.get(col, "") for col in FAILED_CSV_COLUMNS}
        self._write()

    def remove(self, url: str):
        """Drop a URL from the current-failure set and persist immediately.
        Call this only after its data has actually been written to the
        output CSVs — never before, so a crash right after removal can't
        lose data that was never really saved."""
        key = (url or "").strip().lower()
        if key in self._rows:
            del self._rows[key]
            self._order.remove(key)
            self._write()

    def flush(self):
        """Force the file to exist and reflect the current in-memory state,
        even if nothing has changed (e.g. a run with zero failures still
        needs kingston_failed_urls.csv to exist, header-only, rather than
        being absent or left over from some unrelated previous run)."""
        self._write()

    def is_failed(self, url: str) -> bool:
        return (url or "").strip().lower() in self._rows

    def all_rows(self) -> list:
        return [self._rows[key] for key in self._order if key in self._rows]

    def urls(self) -> list:
        return [self._rows[key]["server_url"] for key in self._order if key in self._rows]

    def __len__(self):
        return len(self._order)
