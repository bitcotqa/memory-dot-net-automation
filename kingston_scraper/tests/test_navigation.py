"""Verifies agent/navigation.py's handling of a bad HTTP status alongside
a large rendered page — the exact scenario the live test suite caught:
Kingston's own "page not found" response for a genuinely bad/removed URL
still renders a large page (full site header/nav/footer), so page *size*
alone can't tell that apart from the real Cloudflare-stale-403-then-
resolved case this logic is meant to forgive. Only checking for the
page's own real content markers (not just length) tells the two apart.
"""

import pytest

from agent.error_handler import ScrapeError, FailureType
from agent import navigation
from agent.navigation import open_product_page, open_server_page


class _FakeAgent:
    def __init__(self, status, html, title="Kingston Technology"):
        self._status = status
        self._html = html
        self._title = title

    def goto(self, url):
        return True, self._status, None

    def title(self):
        return self._title

    def content(self):
        return self._html

    def save_debug_html(self, label):
        pass


class _ChallengeAgent(_FakeAgent):
    class _Page:
        def __init__(self, owner):
            self.owner = owner

        def wait_for_timeout(self, _milliseconds):
            self.owner._title = "Kingston Technology"
            self.owner._html = _large_page('<div class="c-configuratorResultsCard">Memory</div>')

    def __init__(self):
        super().__init__(403, "<html>performing security verification</html>", "Just a moment...")
        self.page = self._Page(self)


class _MixedPageAgent(_FakeAgent):
    """Serve normal and challenged pages from the same browser session."""

    class _Page:
        def __init__(self, owner):
            self.owner = owner

        def wait_for_timeout(self, _milliseconds):
            self.owner.challenge_waits += 1
            self.owner._title = "Kingston Technology"
            self.owner._html = _large_page('<div class="s-productDetails">SSD</div>')

    def __init__(self):
        super().__init__(200, "")
        self.page = self._Page(self)
        self.challenge_waits = 0

    def goto(self, url):
        if "challenged" in url:
            self._status = 403
            self._title = "Just a moment..."
            self._html = "<html>performing security verification</html>"
        else:
            self._status = 200
            self._title = "Kingston Technology"
            self._html = _large_page('<div class="c-configuratorResultsCard">Memory</div>')
        return True, self._status, None


def _large_page(body: str, min_len: int = 25000) -> str:
    padding = "<div>site chrome filler</div>" * ((min_len // 30) + 1)
    return f"<html><head></head><body>{padding}{body}</body></html>"


def test_404_with_no_real_content_markers_raises_even_when_page_is_large():
    """The exact bug the live test caught: a genuinely bad/removed URL
    that renders a large generic "not found" page (Kingston's own site
    chrome, no configurator/product content) must still be classified as
    a real navigation failure."""
    html = _large_page("<h1>Page Not Found</h1><p>Sorry, we could not find that page.</p>")
    agent = _FakeAgent(status=404, html=html)

    with pytest.raises(ScrapeError) as excinfo:
        open_server_page(agent, "https://www.kingston.com/en/memory/search/model/0/does-not-exist")

    assert excinfo.value.failure_type == FailureType.NAVIGATION


def test_403_with_real_content_markers_is_forgiven_as_stale_cloudflare_response():
    """The case this exception exists for: Cloudflare answers with a
    stale 403 but the real page (confirmed by its own content markers)
    already rendered underneath — must NOT be treated as fatal."""
    html = _large_page('<div class="c-configuratorResultsCard"><h3>Memory</h3></div>')
    agent = _FakeAgent(status=403, html=html)

    soup = open_server_page(agent, "https://www.kingston.com/en/memory/search/model/1/real-page")
    assert soup.select_one(".c-configuratorResultsCard") is not None


def test_404_with_real_content_markers_is_also_forgiven():
    """Same forgiveness applies to a product page's own markers (SSD
    product pages route through this same function via open_product_page)."""
    html = _large_page('<div class="s-productDetails">real product content</div>')
    agent = _FakeAgent(status=404, html=html)

    soup = open_server_page(agent, "https://www.kingston.com/en/ssd/some-real-product")
    assert soup.select_one(".s-productDetails") is not None


def test_success_status_is_unaffected_by_the_content_check():
    html = _large_page('<div class="c-configuratorResultsCard"><h3>Memory</h3></div>')
    agent = _FakeAgent(status=200, html=html)
    soup = open_server_page(agent, "https://www.kingston.com/en/memory/search/model/2/fine")
    assert soup.select_one(".c-configuratorResultsCard") is not None


def test_headed_mode_waits_for_visible_challenge_to_clear(monkeypatch):
    monkeypatch.setattr(navigation, "HEADLESS", False)
    monkeypatch.setattr(navigation, "CAPTCHA_WAIT_SECONDS", 2)
    agent = _ChallengeAgent()

    soup = open_server_page(agent, "https://www.kingston.com/en/memory/search/model/2/fine")

    assert soup.select_one(".c-configuratorResultsCard") is not None


def test_each_url_independently_checks_for_challenge(monkeypatch):
    """Normal -> challenged -> normal only waits for the challenged load."""
    monkeypatch.setattr(navigation, "HEADLESS", False)
    monkeypatch.setattr(navigation, "CAPTCHA_WAIT_SECONDS", 2)
    agent = _MixedPageAgent()

    first = open_server_page(agent, "https://www.kingston.com/normal-server")
    challenged = open_product_page(agent, "https://www.kingston.com/challenged-ssd")
    last = open_server_page(agent, "https://www.kingston.com/another-normal-server")

    assert first.select_one(".c-configuratorResultsCard") is not None
    assert challenged.select_one(".s-productDetails") is not None
    assert last.select_one(".c-configuratorResultsCard") is not None
    assert agent.challenge_waits == 1
