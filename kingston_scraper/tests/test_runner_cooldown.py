"""Verifies the cooldown/circuit-breaker behavior added to runner.py after
a real 259-server run showed the naive retry loop never adapting: once
Cloudflare starts hard-blocking, every subsequent server failed at the same
fast retry cadence indefinitely. These tests mock the browser agent and
navigation entirely (no real Playwright/network) so they run fast and
deterministically, and assert on the *pacing* decision runner.py makes,
not on live site behavior (that's what test_live_playwright_extraction.py
is for).
"""

from unittest.mock import patch

import runner
from agent.error_handler import BlockedError


class _FakeAgent:
    """Stands in for BrowserAgent — supports the same context-manager and
    new_page() surface runner.py calls, without launching real Chromium."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def new_page(self):
        pass


def _make_input(tmp_path, n):
    lines = ["name,url"] + [f"Server {i},https://example.com/{i}" for i in range(n)]
    path = tmp_path / "in.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_cooldown_triggers_after_consecutive_block_streak(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SERVERS_CSV_PATH", tmp_path / "servers.csv")
    monkeypatch.setattr(runner, "MEMORY_PARTS_CSV_PATH", tmp_path / "memory_parts.csv")
    monkeypatch.setattr(runner, "SSD_PARTS_CSV_PATH", tmp_path / "ssd_parts.csv")
    monkeypatch.setattr(runner, "FAILED_CSV_PATH", tmp_path / "failed.csv")
    monkeypatch.setattr(runner, "ERRORS_CSV_PATH", tmp_path / "errors.csv")
    monkeypatch.setattr(runner, "COOLDOWN_TRIGGER_STREAK", 2)
    monkeypatch.setattr(runner, "COOLDOWN_SECONDS", 42)
    monkeypatch.setattr(runner, "REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(runner, "MAX_RETRIES", 1)  # fail fast per server for this test
    monkeypatch.setattr(runner, "BrowserAgent", lambda: _FakeAgent())

    input_path = _make_input(tmp_path, 4)

    # Every server is Blocked, every attempt — simulates the exact real-run
    # scenario: a hard block that never clears within the run. That means
    # the automatic retry phase can't succeed either, so pass
    # retry_attempts=0 here to isolate this test to Phase 1 cooldown
    # behavior only (the retry phase's own cooldown pacing is identical
    # logic, exercised implicitly by every other run).
    def always_blocked(agent, url, debug_label=""):
        raise BlockedError("Bot-check / CAPTCHA interstitial returned instead of page content")

    monkeypatch.setattr(runner, "open_server_page", always_blocked)

    sleep_calls = []
    with patch("runner.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        summary = runner.run(input_path, retry_attempts=0)

    assert summary["final_failed"] == 4
    assert summary["final_successful"] == 0
    # With COOLDOWN_TRIGGER_STREAK=2, servers 1-2 fail without a cooldown
    # pause, then the streak trips before server 3 (and again before a
    # would-be server 5, but the run ends at 4) — i.e. the 42s cooldown
    # must appear in the sleep calls at least once.
    assert 42 in sleep_calls


def test_no_cooldown_when_servers_succeed_between_failures(tmp_path, monkeypatch):
    """A single isolated block shouldn't trip the breaker — only a real
    streak should. Confirms the counter resets on success."""
    monkeypatch.setattr(runner, "SERVERS_CSV_PATH", tmp_path / "servers.csv")
    monkeypatch.setattr(runner, "MEMORY_PARTS_CSV_PATH", tmp_path / "memory_parts.csv")
    monkeypatch.setattr(runner, "SSD_PARTS_CSV_PATH", tmp_path / "ssd_parts.csv")
    monkeypatch.setattr(runner, "FAILED_CSV_PATH", tmp_path / "failed.csv")
    monkeypatch.setattr(runner, "ERRORS_CSV_PATH", tmp_path / "errors.csv")
    monkeypatch.setattr(runner, "COOLDOWN_TRIGGER_STREAK", 2)
    monkeypatch.setattr(runner, "COOLDOWN_SECONDS", 99)
    monkeypatch.setattr(runner, "REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(runner, "MAX_RETRIES", 1)
    monkeypatch.setattr(runner, "BrowserAgent", lambda: _FakeAgent())

    input_path = _make_input(tmp_path, 4)

    call_count = {"n": 0}

    def alternating(agent, url, debug_label=""):
        call_count["n"] += 1
        if call_count["n"] % 2 == 1:
            raise BlockedError("blocked")
        from bs4 import BeautifulSoup

        return BeautifulSoup("<html><h1>Server</h1></html>", "html.parser")

    monkeypatch.setattr(runner, "open_server_page", alternating)

    sleep_calls = []
    with patch("runner.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        runner.run(input_path)

    assert 99 not in sleep_calls
