"""Verifies the SSD product-page extraction workflow (agent/ssd_extractor.py
+ its wiring into agent/parts_extractor.py) against real captured Kingston
HTML (tests/fixtures/kc600_product_page.html, a400_product_page.html) —
not hand-written synthetic markup, so the parser is checked against the
actual site structure the task asked us to analyze.

Covers, per the task's own test-case list:
- KC600 2.5" @ 256/512/1024/2048GB and mSATA @ 256/512/1024GB all
  extracted as separate variant rows with correct part numbers.
- mSATA + 2048GB (unsupported combination) is never generated.
- Capacity-scoped specs (TBW, Sequential Read/Write) hold the right value
  per capacity, never copied blindly from another capacity.
- Form-factor-scoped specs (Dimensions/Weight) differ correctly between
  2.5" and mSATA.
- component_url is the real HTML product page; datasheet_url is the PDF —
  never confused with each other.
- Product-level caching: the same product URL is fetched at most once
  across multiple servers/cards that reference it.
- A product-page fetch failure produces a fallback record (component/
  server relationship preserved) instead of crashing or dropping data.
"""

import json

from bs4 import BeautifulSoup

from agent.error_handler import PartStatus, ScrapeError, FailureType
from agent.parts_extractor import extract_parts
from agent.ssd_extractor import SsdProductCache


KC600_HTML = open("tests/fixtures/kc600_product_page.html", encoding="utf-8").read()
A400_HTML = open("tests/fixtures/a400_product_page.html", encoding="utf-8").read()


def _server_page_with_ssd_card(learn_more_href="/en/ssd/kc600-sata-solid-state-drive"):
    return f"""
    <html><body>
    <div class="l-tabView">
      <ul class="l-tabView__tabs">
        <li data-pgtab="Storage" data-tab="tabContent0_1"><span>Solid-state drives</span></li>
      </ul>
      <div class="l-tabView__panels">
        <div class="l-tabView__panels__panel" id="tabContent0_1">
          <li class="product-gallery-card" data-partnumber="SKC600/256G"
              data-name="256GB KC600 2.5-Inch SSD" data-category="Solid-State Drives"
              data-group="KC600" data-capacity="256" data-date="01/01/2020">
            <div class="c-productCard4">
              <div class="c-productCard4__header"><a class="c-productCard4__header__link">
                <span class="c-productCard4__header__link__name">256GB KC600 2.5-Inch SSD</span></a></div>
              <div class="c-productCard4__details">
                <div class="c-productCard4__details__content__longDesc">
                  <ul>
                    <li class="c-productCard4__details__content__longDesc__partNumber">Part Number: SKC600/256G</li>
                    <li>SATA Rev 3.0</li>
                    <li><a class="downLoadPdf" href="https://www.kingston.com/datasheets/KC600_en.pdf">Spec Sheet PDF</a></li>
                    <li><a class="learn-more" href="{learn_more_href}">Learn more</a></li>
                  </ul>
                </div>
              </div>
              <div class="c-productCard4__footer"></div>
            </div>
          </li>
        </div>
      </div>
    </div>
    </body></html>
    """


class _FakeAgent:
    """Serves pre-captured real HTML for whatever URL is requested,
    counting how many times each URL is actually fetched (to verify
    product-level caching), with no real Playwright/network involved."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.fetch_counts = {}
        self._next_content = ""
        self._next_title = "Kingston Technology"

    def goto(self, url):
        self.fetch_counts[url] = self.fetch_counts.get(url, 0) + 1
        if url not in self.pages:
            return False, None, RuntimeError(f"no fixture for {url}")
        self._next_content = self.pages[url]
        return True, 200, None

    def title(self):
        return self._next_title

    def content(self):
        return self._next_content

    def new_page(self):
        pass

    def save_debug_html(self, label):
        pass


def test_kc600_produces_exactly_the_seven_supported_variants():
    soup = BeautifulSoup(_server_page_with_ssd_card(), "html.parser")
    agent = _FakeAgent({"https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive": KC600_HTML})
    cache = SsdProductCache()

    parts = extract_parts(soup, "Test Server", "https://example.com/server", agent=agent, ssd_cache=cache)

    variant_keys = {(p.form_factor, p.capacity, p.part_number) for p in parts}
    assert variant_keys == {
        ("2.5\"", "256GB", "SKC600/256G"),
        ("2.5\"", "512GB", "SKC600/512G"),
        ("2.5\"", "1024GB", "SKC600/1024G"),
        ("2.5\"", "2048GB", "SKC600/2048G"),
        ("mSATA", "256GB", "SKC600MS/256G"),
        ("mSATA", "512GB", "SKC600MS/512G"),
        ("mSATA", "1024GB", "SKC600MS/1024G"),
    }
    # The unsupported combination must never be generated:
    assert ("mSATA", "2048GB", "SKC600MS/2048G") not in variant_keys
    assert len(parts) == 7


def test_kc600_component_url_and_datasheet_url_are_kept_separate():
    soup = BeautifulSoup(_server_page_with_ssd_card(), "html.parser")
    agent = _FakeAgent({"https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive": KC600_HTML})
    cache = SsdProductCache()

    parts = extract_parts(soup, "Test Server", "https://example.com/server", agent=agent, ssd_cache=cache)

    for p in parts:
        assert p.component_url == "https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive"
        assert not p.component_url.endswith(".pdf")
        assert p.datasheet_url == "https://www.kingston.com/datasheets/KC600_en.pdf"
        assert p.datasheet_url.endswith(".pdf")


def test_kc600_capacity_and_form_factor_scoped_specs_are_correct():
    soup = BeautifulSoup(_server_page_with_ssd_card(), "html.parser")
    agent = _FakeAgent({"https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive": KC600_HTML})
    cache = SsdProductCache()

    parts = extract_parts(soup, "Test Server", "https://example.com/server", agent=agent, ssd_cache=cache)
    by_key = {(p.form_factor, p.capacity): json.loads(p.part_specifications_json) for p in parts}

    # TBW must never be copied blindly across capacities (task's own
    # reference example: 256GB -> 150TB, 2048GB -> 1200TB).
    assert by_key[("2.5\"", "256GB")]["Specifications"]["Total Bytes Written (TBW)"] == "150TB"
    assert by_key[("2.5\"", "2048GB")]["Specifications"]["Total Bytes Written (TBW)"] == "1200TB"

    # Sequential Read/Write: 256GB differs from the 512GB-2048GB range.
    assert "550/500MB/s" in by_key[("2.5\"", "256GB")]["Specifications"]["Sequential Read/Write"]
    assert "550/520MB/s" in by_key[("2.5\"", "2048GB")]["Specifications"]["Sequential Read/Write"]

    # Form-factor-scoped dimensions/weight must differ between 2.5" and mSATA.
    dims_25 = by_key[("2.5\"", "256GB")]["Specifications"]["Dimensions"]
    dims_msata = by_key[("mSATA", "256GB")]["Specifications"]["Dimensions"]
    assert dims_25 != dims_msata
    assert by_key[("2.5\"", "256GB")]["Specifications"]["Weight"] == "40g"
    assert by_key[("mSATA", "256GB")]["Specifications"]["Weight"] == "7g"

    # Every variant's JSON carries the full Product/Variant/Specifications
    # structure the task specified.
    sample = by_key[("2.5\"", "2048GB")]
    assert sample["Product"]["Name"] == "KC600"
    assert sample["Variant"] == {"Form Factor": "2.5\"", "Capacity": "2048GB", "Part Number": "SKC600/2048G"}
    assert sample["Compatibility"]["Server"] == "Test Server"


def test_ssd_product_page_is_fetched_at_most_once_across_multiple_cards():
    """Same product referenced by two different servers' cards -> the
    underlying page fetch happens exactly once (product-level cache)."""
    soup1 = BeautifulSoup(_server_page_with_ssd_card(), "html.parser")
    soup2 = BeautifulSoup(_server_page_with_ssd_card(), "html.parser")
    agent = _FakeAgent({"https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive": KC600_HTML})
    cache = SsdProductCache()

    parts1 = extract_parts(soup1, "Server A", "https://example.com/a", agent=agent, ssd_cache=cache)
    parts2 = extract_parts(soup2, "Server B", "https://example.com/b", agent=agent, ssd_cache=cache)

    assert len(parts1) == 7
    assert len(parts2) == 7
    assert agent.fetch_counts["https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive"] == 1
    # Server/part relationships stay distinct per server despite the
    # shared product data:
    assert {p.server_name for p in parts1} == {"Server A"}
    assert {p.server_name for p in parts2} == {"Server B"}


def test_a400_single_form_factor_no_tabs_still_produces_correct_variants():
    soup = BeautifulSoup(_server_page_with_ssd_card("/en/ssd/a400-solid-state-drive"), "html.parser")
    agent = _FakeAgent({"https://www.kingston.com/en/ssd/a400-solid-state-drive": A400_HTML})
    cache = SsdProductCache()

    parts = extract_parts(soup, "Test Server", "https://example.com/server", agent=agent, ssd_cache=cache)
    variant_keys = {(p.form_factor, p.capacity, p.part_number) for p in parts}
    assert variant_keys == {
        ("2.5\"", "240GB", "SA400S37/240G"),
        ("2.5\"", "480GB", "SA400S37/480G"),
        ("2.5\"", "960GB", "SA400S37/960G"),
    }
    for p in parts:
        assert p.datasheet_url == "https://www.kingston.com/datasheets/SA400_en.pdf"


def test_product_page_fetch_failure_falls_back_without_dropping_the_component():
    """If the linked product page can't be reached, the component must
    still show up (server/part relationship preserved) with a
    PRODUCT_PAGE_FAILED extraction result — never silently dropped, never
    aborting extraction of the rest of the server's parts."""

    class _AlwaysBlockedAgent(_FakeAgent):
        def goto(self, url):
            self.fetch_counts[url] = self.fetch_counts.get(url, 0) + 1
            raise ScrapeError(FailureType.BLOCKED, "blocked", "blocked")

    # open_product_page catches goto()'s return, not an exception raised
    # by goto() itself — simulate the real contract (goto never raises,
    # returns ok=False) instead.
    class _NavFailsAgent(_FakeAgent):
        def goto(self, url):
            self.fetch_counts[url] = self.fetch_counts.get(url, 0) + 1
            return False, None, TimeoutError("nav timeout")

    soup = BeautifulSoup(_server_page_with_ssd_card(), "html.parser")
    agent = _NavFailsAgent({})
    cache = SsdProductCache()

    parts = extract_parts(soup, "Test Server", "https://example.com/server", agent=agent, ssd_cache=cache)

    assert len(parts) == 1
    part = parts[0]
    assert part.server_name == "Test Server"
    assert part.part_number == "SKC600/256G"
    assert part.status == "Available"  # Kingston's own text is never overwritten
    sections = json.loads(part.part_specifications_json)
    assert sections["Extraction"]["Result"] == PartStatus.PRODUCT_PAGE_FAILED


def test_memory_card_has_no_component_url_but_ssd_card_does():
    """Cross-check on the same page: memory cards never get a
    component_url (no product page exists for them); an SSD card with a
    learn-more link does."""
    html = _server_page_with_ssd_card().replace(
        "</body>",
        """
        <li class="product-gallery-card" data-partnumber="KVR800D2E6/1G" data-name="1GB DDR2 800MT/s ECC Unbuffered DIMM">
          <div class="c-productCard4">
            <div class="c-productCard4__header"><a class="c-productCard4__header__link">
              <span class="c-productCard4__header__link__name">1GB DDR2 800MT/s ECC Unbuffered DIMM</span></a></div>
            <div class="c-productCard4__details">
              <div class="c-productCard4__details__content__longDesc">
                <ul><li>DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin</li></ul>
              </div>
            </div>
            <div class="c-productCard4__footer"></div>
          </div>
        </li>
        </body>""",
    )
    soup = BeautifulSoup(html, "html.parser")
    agent = _FakeAgent({"https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive": KC600_HTML})
    cache = SsdProductCache()

    parts = extract_parts(soup, "Test Server", "https://example.com/server", agent=agent, ssd_cache=cache)
    memory_parts = [p for p in parts if p.part_number == "KVR800D2E6/1G"]
    ssd_parts = [p for p in parts if p.part_number.startswith("SKC600")]

    assert len(memory_parts) == 1
    assert memory_parts[0].component_url == ""
    assert len(ssd_parts) == 7
    assert all(p.component_url for p in ssd_parts)
