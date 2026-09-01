"""Input file reading and CSV output writing.

Kept dependency-free (stdlib csv) and separate from the models so both the
runner and tests can use it without pulling in Playwright.
"""

import csv
import json
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

# Accepted header spellings for the two required input columns, matched
# case-insensitively. First match wins.
_NAME_HEADERS = ("name", "server_name", "server name")
_URL_HEADERS = ("url", "server_url", "server url")


def read_input_servers(path) -> list:
    """Read the uploaded input file (CSV: name,url — or close variants of
    those header names) and return a list of {"name": ..., "url": ...}
    dicts, in file order, skipping rows with no URL. Never assumes the
    columns beyond what's actually present.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"Input file has no header row: {path}")

        field_lookup = {f.strip().lower(): f for f in reader.fieldnames}
        name_col = next((field_lookup[h] for h in _NAME_HEADERS if h in field_lookup), None)
        url_col = next((field_lookup[h] for h in _URL_HEADERS if h in field_lookup), None)

        if url_col is None:
            raise ValueError(
                f"Could not find a URL column in {path}. "
                f"Found columns: {reader.fieldnames}"
            )
        if name_col is None:
            logger.warning("No name column found in %s; using URL as the name too", path)

        servers = []
        for row in reader:
            url = (row.get(url_col) or "").strip()
            if not url:
                continue
            name = (row.get(name_col) or "").strip() if name_col else ""
            servers.append({"name": name or url, "url": url})

    logger.info("Read %d server records from %s", len(servers), path)
    return servers


class CsvWriter:
    """Append-friendly CSV writer with duplicate-key tracking. Loads any
    keys already present in the file at construction time so a resumed run
    never double-writes a record that made it to disk on a prior run.

    Guards against a real, serious failure mode: if an existing file's
    header no longer matches `columns` — because the schema changed since
    that file was last written (a column added/removed/reordered) — naive
    append-in-'a'-mode would keep writing rows in the *current* column
    order underneath a header line that still describes the *old* one.
    The header and the data beneath it silently drift out of sync: every
    reader (pandas, Excel, csv.DictReader) either errors on the ragged rows
    or, worse, silently labels each value under the wrong column name from
    that point on. On construction, a header mismatch is detected and the
    whole file is healed once — every existing row is re-read by its own
    (old) column names and rewritten under the current schema — before any
    new row is appended, so the header and every row's data always agree.
    """

    def __init__(self, path, columns: list, key_fn):
        self.path = Path(path)
        self.columns = columns
        self.key_fn = key_fn
        self.seen_keys = set()
        self._heal_schema_drift()
        self._load_existing_keys()
        self._ensure_header()

    def _heal_schema_drift(self):
        """If the file exists and its on-disk header differs from the
        current `columns` (added/removed/reordered since it was written),
        rewrite it now under the current schema, re-mapping every existing
        row by column *name* (never by position) — this is what actually
        prevents the header/data misalignment described above. A column
        no longer in the current schema is dropped; a new column absent
        from an old row is left blank; nothing is guessed or fabricated.
        """
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        try:
            with open(self.path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                on_disk_header = list(reader.fieldnames or [])
                if on_disk_header == self.columns:
                    return  # already consistent — nothing to heal
                old_rows = [dict(row) for row in reader]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read existing CSV %s to check for schema drift: %s", self.path, exc)
            return

        logger.warning(
            "%s header changed since it was last written (was %s, now %s) — "
            "rewriting %d existing row(s) under the current column set so "
            "the header and the data beneath it never drift apart.",
            self.path,
            on_disk_header,
            self.columns,
            len(old_rows),
        )
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.columns)
            writer.writeheader()
            for row in old_rows:
                writer.writerow({col: row.get(col, "") for col in self.columns})
        tmp_path.replace(self.path)

    def _load_existing_keys(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    key = self.key_fn(row)
                    if key:
                        self.seen_keys.add(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read existing CSV %s for dedup: %s", self.path, exc)

    def _ensure_header(self):
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        if is_new:
            with open(self.path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=self.columns)
                writer.writeheader()

    def write_row(self, row: dict) -> bool:
        """Append one row. Returns False (and skips writing) if a row with
        the same dedup key has already been written this run or in a
        previous run of this output file."""
        key = self.key_fn(row)
        if key and key in self.seen_keys:
            return False
        with open(self.path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.columns)
            writer.writerow({col: row.get(col, "") for col in self.columns})
        if key:
            self.seen_keys.add(key)
        return True

    def write_rows(self, rows: list) -> int:
        return sum(1 for row in rows if self.write_row(row))


class DynamicCsvWriter:
    """A CsvWriter whose column set isn't known up front — it's discovered
    as rows come in, since Kingston memory parts don't all carry the same
    specification keys (one part has CAS Latency, another has Rank/DRAM
    Density, ...) and every key needs its own column rather than being
    bottled into one flat string.

    Because the header can grow after rows have already been added, this
    can't append-and-flush per row the way CsvWriter does — it holds every
    row (existing-on-disk + new-this-run) in memory and rewrites the whole
    file on flush(). Call flush() at a bounded cadence (e.g. once per
    server, like every other per-server persistence point in this
    project) rather than after every single row, so a large run doesn't
    pay for an O(n) rewrite on every row.
    """

    def __init__(self, path, key_fn, row_migrator=None):
        self.path = Path(path)
        self.key_fn = key_fn
        # Optional row_migrator(old_row: dict) -> dict: called on every row
        # loaded from disk before it's adopted. Exists so a file written
        # under an earlier, differently-shaped schema (e.g. this project's
        # own pre-dynamic-columns fixed schema, all lowercase/snake_case)
        # gets upgraded to the current shape on load instead of its columns
        # sitting alongside a second, differently-cased set the new writer
        # would otherwise create (old "status" + new "Status" both present,
        # each blank for the other schema's rows — exactly the "Status
        # column/data missing" symptom this exists to prevent).
        self.row_migrator = row_migrator
        self.columns = []  # ordered; grows as new keys are first seen
        self.rows = []  # every row dict, existing-on-disk + new
        self.seen_keys = set()
        self._load_existing()

    def _load_existing(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    row = dict(row)
                    if self.row_migrator:
                        row = self.row_migrator(row)
                    for col in row.keys():
                        if col not in self.columns:
                            self.columns.append(col)
                    self.rows.append(row)
                    key = self.key_fn(row)
                    if key:
                        self.seen_keys.add(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read existing CSV %s for dedup: %s", self.path, exc)

    def write_row(self, row: dict) -> bool:
        """Add one row (in memory only — call flush() to persist). Returns
        False (and skips) if a row with the same dedup key has already
        been added this run or was already on disk from a previous run."""
        key = self.key_fn(row)
        if key and key in self.seen_keys:
            return False
        for col in row.keys():
            if col not in self.columns:
                self.columns.append(col)
        self.rows.append(dict(row))
        if key:
            self.seen_keys.add(key)
        return True

    def flush(self):
        """Rewrite the whole file (atomically, via a temp file + rename)
        with the current column set and every row. Safe to call as often
        as needed — a crash between flushes just means the file reflects
        an earlier (still fully valid) point, never a half-written one."""
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.columns)
            writer.writeheader()
            for row in self.rows:
                writer.writerow({col: row.get(col, "") for col in self.columns})
        tmp_path.replace(self.path)

    def write_rows(self, rows: list) -> int:
        return sum(1 for row in rows if self.write_row(row))


class AppendCsvLogger:
    """Pure append-only CSV log — every call to append_row() is written,
    even if it repeats a prior row exactly (unlike CsvWriter, which is
    dedup-aware). Used for kingston_scrape_errors.csv, which is meant to
    retain the full history of every failure attempt (initial and every
    automatic retry round), not just the current unresolved set — that
    current set lives in state/failed_store.py instead.
    """

    def __init__(self, path, columns: list):
        self.path = Path(path)
        self.columns = columns
        self._ensure_header()

    def _ensure_header(self):
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        if is_new:
            with open(self.path, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=self.columns).writeheader()

    def append_row(self, row: dict):
        with open(self.path, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=self.columns).writerow(
                {col: row.get(col, "") for col in self.columns}
            )


def validate_csv_file(path) -> list:
    """Read a written CSV back and confirm it's structurally sound —
    the concrete, checkable definition of "no multi-value specification
    spilled into a neighboring column": every data row must have exactly
    as many fields as the header, and any cell that looks like a JSON
    array (starts with '[', ends with ']' — e.g. a multi-value spec
    stored via json.dumps()) must actually parse as one.

    Uses plain csv.reader (positional), not DictReader — DictReader
    silently pads a short row with None or stashes extra fields under a
    `None` key, which would hide exactly the defect this exists to catch.

    Returns a list of human-readable problem strings; an empty list means
    the file is clean. Never raises — a file that can't even be opened is
    itself reported as one problem, not an exception the caller must
    handle specially. Call this after writing/flushing a CSV, and log
    (never silently swallow) whatever it finds.
    """
    problems = []
    path = Path(path)
    if not path.exists():
        return [f"{path}: file does not exist"]

    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                return [f"{path}: file is empty (no header row)"]

            expected = len(header)
            for line_no, row in enumerate(reader, start=2):
                if len(row) != expected:
                    problems.append(
                        f"{path}:{line_no}: row has {len(row)} field(s), header has {expected} "
                        f"— columns are misaligned from this row onward"
                    )
                    continue  # positional column names are unreliable for this row — skip the JSON check below
                for col_name, value in zip(header, row):
                    if value.startswith("[") and value.endswith("]"):
                        try:
                            json.loads(value)
                        except (ValueError, TypeError) as exc:
                            problems.append(
                                f"{path}:{line_no}: column '{col_name}' looks like a JSON array "
                                f"but does not parse: {exc}"
                            )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{path}: could not be read for validation: {exc}")

    return problems


def validate_and_log_csv_file(path) -> bool:
    """validate_csv_file() + logging. Returns True if the file is clean.
    A problem is always logged (never swallowed) — this is the "never
    silently produce corrupted CSV data" check, run right after a CSV is
    written/flushed.
    """
    problems = validate_csv_file(path)
    if not problems:
        logger.info("CSV validation passed: %s", path)
        return True
    logger.warning("CSV validation found %d issue(s) in %s:", len(problems), path)
    for problem in problems:
        logger.warning("  %s", problem)
    return False
