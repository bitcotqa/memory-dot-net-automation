"""Live, real-browser verification of extraction against the actual
kingston.com pages — as opposed to test_extraction.py, which checks the
parsing logic against captured/synthetic HTML fixtures offline.

This module drives the project's own Playwright agent (agent.browser_agent
+ agent.navigation, the exact code path runner.py uses) against real URLs,
then independently re-reads the same live DOM through plain Playwright
locators (not through our own parser) and cross-checks the two. That's the
point: test_extraction.py proves the parser is internally consistent given
some HTML; this proves the parser's output actually matches what the
Kingston page really shows, end to end, on a genuinely live page.

Covered, per test:
  - page loads and the extracted title/product name matches the DOM
  - specifications_json's category set matches the live page's own card
    headings (nothing missing, nothing fabricated)
  - a specific category's attribute/value pairs (Memory: Standard/Maximum)
    match values read independently straight from the DOM
  - parts count and a specific part's fields match the live DOM
  - no duplicate part numbers in real data
  - the full CSV round-trip (write -> read back -> json.loads) preserves
    the live-extracted JSON exactly
  - failure handling (Navigation/Timeout) fires correctly against a real,
    genuinely-broken URL — not a mock

Known limitation (see README "Known limitation: Cloudflare"): kingston.com
sits behind Cloudflare bot management, and a sandboxed/datacenter IP can
get blocked after only a handful of requests. These tests are opt-in and
network-dependent by design:

    KINGSTON_LIVE_TESTS=1 python -m pytest tests/test_live_playwright_extraction.py -v

Running the whole suite (`pytest tests/`) skips this module by default so
CI/offline runs stay fast and deterministic. When opted in, any individual
test that hits a Cloudflare interstitial or other transient navigation
failure skips itself (with the real reason reported) rather than failing —
a block is an environment condition, not a defect in the code under test.
"""

import json
import os

import pytest

from agent.browser_agent import BrowserAgent
from agent.navigation import open_server_page
from agent.extraction import extract_server_specs, build_sections
from agent.parts_extractor import extract_parts
from agent.error_handler import ScrapeError, FailureType
from models.models import SERVER_CSV_COLUMNS, PART_CSV_COLUMNS
from utils.io_utils import CsvWriter

LIVE = os.environ.get("KINGSTON_LIVE_TESTS") == "1"
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not LIVE,
        reason="Live/network test — opt in with KINGSTON_LIVE_TESTS=1 (see module docstring)",
    ),
]

MEMORY_SYSTEM_URL = "https://www.kingston.com/en/memory/search/model/36475/abit-an9-32x-motherboard"
MEMORY_SYSTEM_NAME = "ABIT- AN9 32X Motherboard"

# A path that is well-formed but doesn't exist -> exercises real
# navigation-failure handling against a genuine 404, not a mock.
BROKEN_URL = "https://www.kingston.com/en/memory/search/model/0/this-model-does-not-exist"


@pytest.fixture(scope="module")
def browser():
    agent = BrowserAgent().start()
    yield agent
    agent.stop()


def _open_or_skip(agent, url, label):
    try:
        return open_server_page(agent, url, debug_label=label)
    except ScrapeError as exc:
        pytest.skip(f"Live site not reachable right now ({exc.failure_type}): {exc.reason}")


# --- server-level cross-checks ---------------------------------------------


def test_live_title_and_product_name_match_dom(browser):
    soup = _open_or_skip(browser, MEMORY_SYSTEM_URL, "live_title")

    # Independently read the h1 straight off the live page via Playwright,
    # not via our own parser, then compare to what extraction produced
    # from the (separately captured) soup.
    dom_h1 = browser.page.locator("h1").first.inner_text().strip()

    record = extract_server_specs(soup, MEMORY_SYSTEM_NAME, MEMORY_SYSTEM_URL)

    assert record.product_name == dom_h1
    assert dom_h1  # sanity: page actually rendered a title, not a blank shell
    assert "AN9 32X" in dom_h1


def test_live_specifications_json_categories_match_dom_cards(browser):
    soup = _open_or_skip(browser, MEMORY_SYSTEM_URL, "live_categories")

    # Kingston renders these headings all-caps via CSS (text-transform),
    # so Playwright's inner_text() (the rendered text) comes back
    # "MEMORY"/"STORAGE"/... while the raw HTML — what BeautifulSoup reads
    # — is title-case "Memory"/"Storage". Compare case-insensitively so
    # this checks the actual category *set*, not incidental CSS styling.
    dom_headings = {
        h.strip().lower()
        for h in browser.page.locator(".c-configuratorResultsCard h3").all_inner_texts()
        if h.strip()
    }

    sections = build_sections(soup)
    record = extract_server_specs(soup, MEMORY_SYSTEM_NAME, MEMORY_SYSTEM_URL)
    json_sections = json.loads(record.specifications_json)
    json_headings = {key.lower() for key in json_sections}

    assert set(json_sections.keys()) == set(sections.keys())
    # Every category the live DOM actually shows made it into the JSON —
    # nothing silently dropped.
    assert dom_headings <= json_headings
    # And nothing was fabricated that isn't really on the page.
    assert json_headings <= dom_headings


def test_live_memory_attrs_match_dom_values(browser):
    soup = _open_or_skip(browser, MEMORY_SYSTEM_URL, "live_memory")

    # Independently locate the Memory card and read its Standard/Maximum
    # values straight from the DOM (scoped to the card whose h3 == "Memory"
    # so this doesn't accidentally read some other card).
    memory_card = browser.page.locator(
        ".c-configuratorResultsCard", has=browser.page.locator("h3", has_text="Memory")
    ).first
    labels = [t.strip() for t in memory_card.locator("h4").all_inner_texts()]
    values = [t.strip() for t in memory_card.locator("ul.u-list-unstyled > li p").all_inner_texts()]
    dom_memory = dict(zip(labels, values))

    record = extract_server_specs(soup, MEMORY_SYSTEM_NAME, MEMORY_SYSTEM_URL)
    sections = json.loads(record.specifications_json)

    assert "Standard" in dom_memory and "Maximum" in dom_memory
    assert sections["Memory"]["Standard"] == dom_memory["Standard"]
    assert sections["Memory"]["Maximum"] == dom_memory["Maximum"]
    # And the legacy flat column carries the same values (format aside).
    assert dom_memory["Standard"] in record.memory
    assert dom_memory["Maximum"] in record.memory
    # Cross-category isolation, checked against the *live* page too: the
    # Memory values must not have leaked into the storage column.
    assert dom_memory["Maximum"] not in record.storage


# --- parts cross-checks -----------------------------------------------------


def test_live_parts_count_and_first_part_match_dom(browser):
    soup = _open_or_skip(browser, MEMORY_SYSTEM_URL, "live_parts")

    dom_card_count = browser.page.locator("li.product-gallery-card").count()
    dom_first_part_number = browser.page.locator("li.product-gallery-card").first.get_attribute(
        "data-partnumber"
    )
    dom_first_name = browser.page.locator("li.product-gallery-card").first.get_attribute("data-name")

    parts = extract_parts(soup, MEMORY_SYSTEM_NAME, MEMORY_SYSTEM_URL)

    assert len(parts) == dom_card_count
    assert parts[0].part_number == dom_first_part_number
    assert parts[0].component_name == dom_first_name

    part_sections = json.loads(parts[0].part_specifications_json)
    assert part_sections["General"]["Part Number"] == dom_first_part_number


def test_live_no_duplicate_part_numbers(browser):
    soup = _open_or_skip(browser, MEMORY_SYSTEM_URL, "live_dedup")
    parts = extract_parts(soup, MEMORY_SYSTEM_NAME, MEMORY_SYSTEM_URL)

    part_numbers = [p.part_number for p in parts if p.part_number]
    assert len(part_numbers) == len(set(part_numbers))


# --- CSV round-trip with real, live-extracted data --------------------------


def test_live_json_columns_survive_csv_round_trip(browser, tmp_path):
    soup = _open_or_skip(browser, MEMORY_SYSTEM_URL, "live_csv")

    record = extract_server_specs(soup, MEMORY_SYSTEM_NAME, MEMORY_SYSTEM_URL)
    parts = extract_parts(soup, MEMORY_SYSTEM_NAME, MEMORY_SYSTEM_URL)
    assert parts, "expected at least one compatible part on this system"

    servers_writer = CsvWriter(
        tmp_path / "servers.csv", SERVER_CSV_COLUMNS, key_fn=lambda r: r.get("server_url", "")
    )
    parts_writer = CsvWriter(
        tmp_path / "parts.csv",
        PART_CSV_COLUMNS,
        key_fn=lambda r: f"{r.get('server_url', '')}::{r.get('part_number', '')}",
    )
    servers_writer.write_row(record.to_row())
    parts_writer.write_row(parts[0].to_row())

    import csv

    with open(tmp_path / "servers.csv", newline="", encoding="utf-8") as fh:
        server_row = next(csv.DictReader(fh))
    with open(tmp_path / "parts.csv", newline="", encoding="utf-8") as fh:
        part_row = next(csv.DictReader(fh))

    # Round-tripping through the CSV writer/reader must not corrupt the
    # JSON (quoting, escaping, embedded commas/quotes in descriptions, ...).
    assert json.loads(server_row["specifications_json"]) == json.loads(record.specifications_json)
    assert json.loads(part_row["part_specifications_json"]) == json.loads(
        parts[0].part_specifications_json
    )


# --- failure handling against a real, broken URL ----------------------------


def test_live_navigation_failure_is_classified_correctly(browser):
    """A well-formed but non-existent Kingston URL should surface as a real
    ScrapeError (Navigation/Blocked/Timeout) from actual Playwright
    navigation — not a mocked exception — proving the failed-URL path
    (runner.py's retry loop -> kingston_failed_urls.csv) is wired to real
    browser behavior, not just unit-tested in isolation."""
    with pytest.raises(ScrapeError) as excinfo:
        open_server_page(browser, BROKEN_URL, debug_label="live_broken")

    assert excinfo.value.failure_type in (
        FailureType.NAVIGATION,
        FailureType.BLOCKED,
        FailureType.STRUCTURE,
        FailureType.TIMEOUT,
    )
