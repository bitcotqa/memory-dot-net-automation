"""Verifies the automatic post-run retry phase added to runner.py:

- a server that fails in the initial phase but succeeds on an automatic
  retry round gets its data written to the output CSVs and is removed
  from the current-failures CSV;
- a server that keeps failing through every retry round stays in the
  current-failures CSV;
- every failure attempt (resolved or not) is preserved in the historical
  error log;
- no duplicate server/part rows are produced because of the retry.

Mocks _process_one_server directly (no real Playwright/network) so these
run fast and deterministically.
"""

import csv

from unittest.mock import patch

import runner
from agent.error_handler import ScrapeError, FailureType
from models.models import ServerRecord, PartRecord


class _FakeAgent:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def new_page(self):
        pass


def _make_input(tmp_path, names_urls):
    lines = ["name,url"] + [f"{n},{u}" for n, u in names_urls]
    path = tmp_path / "in.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_retry_success_moves_server_out_of_failed_csv_with_no_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SERVERS_CSV_PATH", tmp_path / "servers.csv")
    monkeypatch.setattr(runner, "MEMORY_PARTS_CSV_PATH", tmp_path / "memory_parts.csv")
    monkeypatch.setattr(runner, "SSD_PARTS_CSV_PATH", tmp_path / "ssd_parts.csv")
    monkeypatch.setattr(runner, "FAILED_CSV_PATH", tmp_path / "failed.csv")
    monkeypatch.setattr(runner, "ERRORS_CSV_PATH", tmp_path / "errors.csv")
    monkeypatch.setattr(runner, "COOLDOWN_TRIGGER_STREAK", 999)  # never trip cooldown in this test
    monkeypatch.setattr(runner, "REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(runner, "MAX_RETRIES", 1)  # one nav-attempt per phase-attempt
    monkeypatch.setattr(runner, "BrowserAgent", lambda: _FakeAgent())

    servers = [
        ("Server A", "https://example.com/a"),  # fails initial, succeeds retry round 1
        ("Server B", "https://example.com/b"),  # fails every round
        ("Server C", "https://example.com/c"),  # succeeds initial
    ]
    input_path = _make_input(tmp_path, servers)

    call_counts = {}

    def fake_process_one_server(agent, server, debug_label, ssd_cache):
        url = server["url"]
        call_counts[url] = call_counts.get(url, 0) + 1
        n = call_counts[url]

        if url.endswith("/b"):
            raise ScrapeError(FailureType.BLOCKED, "still blocked", "still blocked")
        if url.endswith("/a") and n == 1:
            raise ScrapeError(FailureType.TIMEOUT, "timed out", "timed out")

        record = ServerRecord(brand="Kingston", server_name=server["name"], server_url=url)
        parts = [PartRecord(brand="Kingston", server_name=server["name"], server_url=url, part_number=f"P-{url[-1]}")]
        return record, parts

    monkeypatch.setattr(runner, "_process_one_server", fake_process_one_server)

    with patch("runner.time.sleep"):
        summary = runner.run(input_path, retry_attempts=2)

    servers_rows = _read_csv(tmp_path / "servers.csv")
    # The fake PartRecords built above have no component_type set, so
    # is_ssd_category("") is False and they land in the memory parts file.
    parts_rows = _read_csv(tmp_path / "memory_parts.csv")
    failed_rows = _read_csv(tmp_path / "failed.csv")
    error_rows = _read_csv(tmp_path / "errors.csv")

    # A and C made it to the output CSVs; B never did.
    server_urls_written = {r["server_url"] for r in servers_rows}
    assert server_urls_written == {"https://example.com/a", "https://example.com/c"}
    assert len(servers_rows) == 2  # no duplicate row for A despite failing once first
    # memory_parts.csv uses the dynamic writer's human-readable column
    # names (e.g. "Server URL"), not the fixed snake_case PART_CSV_COLUMNS.
    part_urls_written = {r["Server URL"] for r in parts_rows}
    assert part_urls_written == {"https://example.com/a", "https://example.com/c"}
    assert len(parts_rows) == 2

    # Only B remains in the current-failures CSV.
    assert [r["server_url"] for r in failed_rows] == ["https://example.com/b"]

    # The historical error log kept every attempt: A's one initial failure,
    # plus B's failure on the initial phase and both retry rounds.
    a_errors = [r for r in error_rows if r["server_url"] == "https://example.com/a"]
    b_errors = [r for r in error_rows if r["server_url"] == "https://example.com/b"]
    assert len(a_errors) == 1
    assert len(b_errors) == 3
    assert [r["attempt_number"] for r in b_errors] == ["1", "2", "3"]

    # Summary reflects initial vs retry outcomes correctly.
    assert summary["initial_successful"] == 1  # C
    assert summary["initial_failed"] == 2  # A, B
    assert summary["auto_retry_rounds_run"] == 2
    assert summary["retry_candidates"] == 2
    assert summary["retry_successful"] == 1  # A
    assert summary["retry_failed"] == 1  # B
    assert summary["final_failed"] == 1
    assert summary["remaining_failed_urls"] == 1
