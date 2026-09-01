"""Page-level navigation: open a server URL, detect Cloudflare/bot-check
blocking, nudge the page into revealing any lazily-shown content (tabs,
accordions, "load more" buttons), and hand back a parsed BeautifulSoup tree
for extraction.py / parts_extractor.py to read.

Kingston's system/memory-configurator pages turn out to be server-rendered:
every spec card and every compatible-part card is already present in the
initial HTML (Kingston just toggles CSS classes client-side for tabs and
"show more"). So navigation's job is mostly: get past any bot-check, wait
for hydration, and defensively click anything that looks collapsed —
without depending on that clicking to *find* data that's already there.
"""

from bs4 import BeautifulSoup

from agent.error_handler import BlockedError, ScrapeError, FailureType
from config.config import SAVE_DEBUG_HTML_ON_FAILURE, ENABLE_EXPLORATORY_CLICKS
from utils.logger import get_logger

logger = get_logger(__name__)

# Cloudflare (and similar) interstitials consistently use one of these
# markers. Checking several makes detection robust to which challenge
# variant is served.
_BLOCK_TITLE_MARKERS = ("just a moment", "attention required", "access denied", "are you a human")

# These strings only appear on the interstitial page itself (Cloudflare's
# "Performing security verification" holding page) — unlike generic
# markers such as "captcha" or "challenge-platform", which also show up in
# real Kingston pages that merely *load* Cloudflare's/reCAPTCHA's JS SDK
# for unrelated widgets and would false-positive on a real, fully-loaded
# page. A real page is also far larger than the interstitial (~2-3KB), so
# the short-page check guards against a marker match deep in real content.
_BLOCK_STRONG_HTML_MARKERS = (
    "performing security verification",
    "cf-chl-widget",
    "cf-turnstile-response",
    "enable javascript and cookies to continue",
)
_BLOCK_MAX_HTML_LEN = 20000

# Markers that only appear on an actual server-compatibility or SSD/
# branded product page (never on a generic error page, however large —
# Kingston's own "not found" page still renders the full site chrome:
# header, nav, footer — so page *size* alone can't distinguish "real
# content" from "large error page"). Covers both page types this module
# serves (open_server_page and its open_product_page alias).
_REAL_CONTENT_MARKERS = (
    "c-configuratorresultscard",  # server page's own System Information cards
    "product-gallery-card",  # server page's compatible-part cards
    "s-productdetails",  # SSD/branded product page
)


def _has_real_content(html: str) -> bool:
    html_l = (html or "").lower()
    return any(marker in html_l for marker in _REAL_CONTENT_MARKERS)


def _looks_blocked(title: str, html: str) -> bool:
    title_l = (title or "").lower()
    if any(marker in title_l for marker in _BLOCK_TITLE_MARKERS):
        return True
    if len(html or "") > _BLOCK_MAX_HTML_LEN:
        return False  # a real, fully-rendered page is never this small
    html_l = (html or "").lower()
    return any(marker in html_l for marker in _BLOCK_STRONG_HTML_MARKERS)


def open_server_page(agent, url: str, debug_label: str = "") -> BeautifulSoup:
    """Navigate to url and return a parsed BeautifulSoup tree of the fully
    settled page. Raises ScrapeError (BlockedError / NAVIGATION / TIMEOUT)
    on failure — never returns a soup for a page that isn't real content.
    """
    ok, status, exc = agent.goto(url)
    if not ok:
        raise ScrapeError(
            FailureType.TIMEOUT if "Timeout" in type(exc).__name__ else FailureType.NAVIGATION,
            f"Failed to open {url}",
            str(exc),
        )

    # Check what actually rendered *before* trusting the HTTP status code.
    # Cloudflare's invisible/JS challenge often answers the initial request
    # with 403 and then silently swaps in the real page a moment later
    # once the challenge auto-resolves — by the time browser_agent.goto()
    # returns (it already waited POST_LOAD_SETTLE_MS), the DOM may hold
    # perfectly good content despite that stale 403. So: trust the
    # rendered page over the response code, and only treat a bad status
    # as fatal if the content itself also looks wrong.
    title = agent.title()
    html = agent.content()

    if _looks_blocked(title, html):
        if SAVE_DEBUG_HTML_ON_FAILURE and debug_label:
            agent.save_debug_html(f"{debug_label}_blocked")
        raise BlockedError(
            "Bot-check / CAPTCHA interstitial returned instead of page content",
            f"Page title was: {title!r}",
        )

    if status is not None and status >= 400:
        # Page *size* alone can't tell a real page apart from a large
        # generic error page (Kingston's own "not found" page still
        # renders the full site chrome) — a genuinely bad/removed URL
        # must be caught here even if it happens to render a big page, or
        # it silently "succeeds" into an empty/PARTIAL_SUCCESS record
        # instead of being flagged in kingston_failed_urls.csv. Checking
        # for the page's own real content markers (not just length)
        # keeps the Cloudflare-stale-403-then-resolved exception narrow
        # and correct.
        if not _has_real_content(html):
            raise ScrapeError(
                FailureType.NAVIGATION,
                f"HTTP {status} loading {url} with no recognizable page content",
                f"HTTP status {status}",
            )
        logger.info(
            "Navigation response was HTTP %d for %s but the page rendered real content "
            "anyway (likely a Cloudflare challenge that auto-resolved) — proceeding.",
            status,
            url,
        )

    # Best-effort: reveal any collapsed accordion tabs and "load more"
    # panels. Content is present either way (see module docstring) — this
    # exists only as a fallback for a page structure we haven't seen, so
    # it's off by default (ENABLE_EXPLORATORY_CLICKS) both because it's
    # unnecessary and because it's pure extra automated-looking DOM
    # interaction with no data upside, i.e. added bot-detection surface for
    # nothing in return.
    if ENABLE_EXPLORATORY_CLICKS:
        try:
            # Scoped to the configurator/results + compatible-parts sections
            # only — the page header/footer (mega-menu, language switcher,
            # cookie banner) also match "[aria-expanded='false']" but aren't
            # part of the data we need, and clicking into them wastes time
            # at best and can misnavigate at worst.
            agent.click_each(
                "#configuratorResults0 [aria-expanded='false'], "
                ".s-productGallery3 [aria-expanded='false']"
            )
            agent.click_all(
                "button.gallery-loadmore, .c-btn--loadMore, [data-loadmore]", max_clicks=5
            )
            html = agent.content()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Non-fatal: interaction pass before extraction failed: %s", exc)

    if not html or len(html) < 500:
        raise ScrapeError(FailureType.STRUCTURE, "Page content was empty or too short", "")

    return BeautifulSoup(html, "html.parser")


# Kingston SSD product pages (e.g. kingston.com/en/ssd/kc600-sata-solid-state-drive)
# are the same kind of server-rendered page as the server-compatibility pages
# above — same bot-check behavior, same "everything is already in the initial
# HTML" behavior (confirmed by inspecting a real KC600/A400 page: capacity,
# form factor, and the full specification table for every form factor are
# all present without clicking anything — see agent/ssd_extractor.py). So
# fetching one is exactly the same operation; this alias exists purely so
# call sites read clearly (agent/ssd_extractor.py navigates to *product*
# pages, not server pages) without duplicating the logic above.
open_product_page = open_server_page
