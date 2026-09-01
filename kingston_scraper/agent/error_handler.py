"""Failure classification + retry helper shared by the whole agent.

Kept separate from browser_agent/navigation so failure *policy* (what
counts as a failure, how many times to retry, how long to wait) lives in
one place instead of being scattered across call sites.
"""

import time

from config.config import MAX_RETRIES, RETRY_BACKOFF_SECONDS
from utils.logger import get_logger

logger = get_logger(__name__)


# --- Failure taxonomy -------------------------------------------------------
# Mirrors the failure types the spec calls out explicitly (timeouts, nav
# failures, blocked/CAPTCHA, unexpected structure, extraction failure).
class FailureType:
    NAVIGATION = "Navigation Error"
    TIMEOUT = "Timeout"
    BLOCKED = "Blocked"
    STRUCTURE = "Unexpected Structure"
    EXTRACTION = "Extraction Error"
    UNKNOWN = "Unknown Error"


class ScrapeError(Exception):
    """A recoverable-or-not scraping failure with a classified type."""

    def __init__(self, failure_type: str, reason: str, error_message: str = ""):
        super().__init__(reason)
        self.failure_type = failure_type
        self.reason = reason
        self.error_message = error_message or reason


class BlockedError(ScrapeError):
    """Raised when the page is showing a bot-check / CAPTCHA / block page
    instead of real content. Deliberately not retried aggressively —
    retrying instantly against an active block just burns the IP's
    reputation further."""

    def __init__(self, reason: str, error_message: str = ""):
        super().__init__(FailureType.BLOCKED, reason, error_message)


# --- Statuses ----------------------------------------------------------
class Status:
    SERVER_SUCCESS = "SERVER_SUCCESS"
    SERVER_FAILED = "SERVER_FAILED"  # a.k.a SERVER_URL_FAILED — the server
    # compatibility page itself couldn't be loaded/parsed at all.
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    PART_FAILED = "PART_FAILED"


class PartStatus:
    """Component-level outcomes, one granularity level below Status above.
    A server page can load fine (Status.SERVER_SUCCESS) while individual
    *components* on it still fail independently — most commonly an SSD
    part whose linked Kingston product page can't be reached or doesn't
    parse the way we expect. These never abort the run: a failing
    component still gets a PartRecord row (server/part relationship
    preserved, per the CSV's own `status` column) and the runner moves on
    to the next component.
    """

    SUCCESS = "SUCCESS"
    # The server page's card didn't carry a real Kingston product-page
    # link at all (expected for most memory/ValueRAM parts — see
    # agent/memory_extractor.py) or the link present didn't resolve.
    COMPONENT_URL_FAILED = "COMPONENT_URL_FAILED"
    # A real product URL was found but navigating to it failed (blocked,
    # timed out, 4xx/5xx, or the returned page was empty/unparseable).
    PRODUCT_PAGE_FAILED = "PRODUCT_PAGE_FAILED"
    # The product page loaded, but its capacity/form-factor/part-number
    # catalog (KCMS.AddToCart.initialize(...) payload) was missing or
    # didn't yield a usable variant matrix.
    VARIANT_EXTRACTION_FAILED = "VARIANT_EXTRACTION_FAILED"
    # Variants were identified, but the specification table(s) for this
    # product/form-factor couldn't be parsed (page structure changed).
    SPECIFICATION_EXTRACTION_FAILED = "SPECIFICATION_EXTRACTION_FAILED"


def with_retries(func, *, max_retries: int = None, backoff_seconds: float = None, on_attempt_failed=None):
    """Call func() (no args — wrap your call in a lambda/closure), retrying
    on ScrapeError up to max_retries times with linear backoff. Re-raises
    the last ScrapeError if every attempt fails. BlockedError is retried
    too, but callers should keep retry counts low for it (default config
    already does).
    """
    retries = MAX_RETRIES if max_retries is None else max_retries
    backoff = RETRY_BACKOFF_SECONDS if backoff_seconds is None else backoff_seconds

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except ScrapeError as exc:
            last_error = exc
            logger.warning(
                "Attempt %d/%d failed (%s): %s", attempt, retries, exc.failure_type, exc.reason
            )
            if on_attempt_failed:
                on_attempt_failed(attempt, exc)
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last_error
