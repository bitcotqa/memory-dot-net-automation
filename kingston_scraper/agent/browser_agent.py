"""Thin Playwright wrapper: owns the one Chromium browser/context/page used
for the whole run, and provides the small set of interaction primitives the
rest of the agent needs (navigate, wait, click, dump HTML). Kept generic on
purpose — no Kingston-specific selectors live here, that's navigation.py /
extraction.py / parts_extractor.py.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config.config import (
    HEADLESS,
    NAV_TIMEOUT_MS,
    ACTION_TIMEOUT_MS,
    POST_LOAD_SETTLE_MS,
    DEBUG_DIR,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BrowserAgent:
    """Owns one Chromium browser + page for the lifetime of a scrape run."""

    def __init__(self):
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None

    # -- lifecycle -----------------------------------------------------

    def start(self):
        logger.info("Launching Chromium (headless=%s)", HEADLESS)
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = self.browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.context.set_default_timeout(ACTION_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        self.page = self.context.new_page()
        return self

    def stop(self):
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
        finally:
            if self._playwright:
                self._playwright.stop()
        logger.info("Browser closed")

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()

    def new_page(self):
        """Recycle the context into a fresh page (clears any per-page state
        without paying for a full browser relaunch)."""
        try:
            self.page.close()
        except Exception:  # noqa: BLE001
            pass
        self.page = self.context.new_page()
        return self.page

    # -- navigation --------------------------------------------------------

    def goto(self, url: str, wait_until: str = "domcontentloaded"):
        """Navigate and settle. Returns (ok, response_status_or_None,
        exception_or_None) — never raises, callers decide what a failure
        means."""
        try:
            response = self.page.goto(url, wait_until=wait_until)
            self.page.wait_for_timeout(POST_LOAD_SETTLE_MS)
            status = response.status if response else None
            return True, status, None
        except PlaywrightTimeoutError as exc:
            return False, None, exc
        except Exception as exc:  # noqa: BLE001
            return False, None, exc

    def current_url(self) -> str:
        return self.page.url

    def title(self) -> str:
        try:
            return self.page.title()
        except Exception:  # noqa: BLE001
            return ""

    def content(self) -> str:
        return self.page.content()

    # -- interaction -----------------------------------------------------

    # Best-effort exploratory clicks (accordions/tabs/"load more" the page
    # *might* need nudged open) use a short, fixed per-click timeout of
    # their own rather than ACTION_TIMEOUT_MS. These selectors can also
    # match unrelated, non-interactable page chrome (mega-menu items,
    # footer accordions) — with the full 15s action timeout, a couple
    # dozen such misses compounds into minutes of dead time per page. A
    # short timeout plus a hard element cap keeps a bad selector match
    # cheap instead of catastrophic.
    _EXPLORATORY_CLICK_TIMEOUT_MS = 1200
    _EXPLORATORY_MAX_ELEMENTS = 15

    def click_all(self, selector: str, max_clicks: int = 10) -> int:
        """Click every element matching selector (e.g. 'load more' buttons,
        collapsed tabs) up to max_clicks times total. Returns how many
        clicks actually happened. Never raises, never spends more than
        ~max_clicks * _EXPLORATORY_CLICK_TIMEOUT_MS regardless of what the
        selector matches."""
        clicked = 0
        for _ in range(max_clicks):
            try:
                locator = self.page.locator(selector).first
                if locator.count() == 0 or not locator.is_visible(timeout=self._EXPLORATORY_CLICK_TIMEOUT_MS):
                    break
                locator.click(timeout=self._EXPLORATORY_CLICK_TIMEOUT_MS)
                clicked += 1
                self.page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001
                break
        return clicked

    def click_each(self, selector: str, max_elements: int = None) -> int:
        """Click every element currently matching selector, once each (e.g.
        every tab button), tolerating individual click failures. Bounded to
        at most max_elements attempts (default _EXPLORATORY_MAX_ELEMENTS)
        so an overly broad selector can't turn into a multi-minute stall."""
        cap = self._EXPLORATORY_MAX_ELEMENTS if max_elements is None else max_elements
        clicked = 0
        try:
            handles = self.page.query_selector_all(selector)
        except Exception:  # noqa: BLE001
            return 0
        for handle in handles[:cap]:
            try:
                handle.click(timeout=self._EXPLORATORY_CLICK_TIMEOUT_MS)
                clicked += 1
                self.page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001
                continue
        return clicked

    # -- debugging -----------------------------------------------------

    def save_debug_html(self, label: str):
        try:
            path = Path(DEBUG_DIR) / f"{label}.html"
            path.write_text(self.page.content(), encoding="utf-8")
            logger.info("Saved debug HTML: %s", path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not save debug HTML for %s: %s", label, exc)
