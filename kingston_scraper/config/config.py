"""Central configuration for the Kingston scraper. Values can be overridden
via environment variables (see .env.example) so behavior can be tuned
without editing code.

This project is intentionally standalone: nothing here is imported by, or
imports from, the Intel ARK scraper.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars still work if already exported

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Input / Output -------------------------------------------------------
INPUT_DIR = BASE_DIR / "input"
DEFAULT_INPUT_PATH = INPUT_DIR / "server_urls.csv"

OUTPUT_DIR = BASE_DIR / "output"
DEBUG_DIR = OUTPUT_DIR / "debug"
SERVERS_CSV_PATH = OUTPUT_DIR / "kingston_servers.csv"
# Memory and SSD components go to separate CSVs — their extraction
# workflows, and the shape of the data they carry, are different enough
# (see agent/memory_extractor.py vs agent/ssd_extractor.py) that mixing
# both part types into one file just makes each harder to consume.
MEMORY_PARTS_CSV_PATH = OUTPUT_DIR / "kingston_memory_parts.csv"
SSD_PARTS_CSV_PATH = OUTPUT_DIR / "kingston_ssd_parts.csv"
# Current, unresolved failures only — rewritten (not appended) as retries
# resolve entries. See state/failed_store.py.
FAILED_CSV_PATH = OUTPUT_DIR / "kingston_failed_urls.csv"
# Permanent historical record of every failure attempt, resolved or not —
# append-only, never rewritten. See state/failed_store.py.
ERRORS_CSV_PATH = OUTPUT_DIR / "kingston_scrape_errors.csv"

LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "kingston_scraper.log"

STATE_DIR = BASE_DIR / "state"
STATUS_PATH = STATE_DIR / "processing_status.json"

for _d in (OUTPUT_DIR, DEBUG_DIR, LOG_DIR, STATE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Run mode --------------------------------------------------------------
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
# A persistent profile is especially useful in headed mode: Cloudflare's
# clearance cookie survives page/browser restarts instead of every retry
# looking like a brand-new visitor. Set this to an empty string to use an
# ephemeral Playwright context.
_browser_profile_value = os.getenv("BROWSER_PROFILE_DIR", "state/browser_profile").strip()
BROWSER_PROFILE_DIR = (
    (BASE_DIR / _browser_profile_value).resolve()
    if _browser_profile_value and not Path(_browser_profile_value).is_absolute()
    else (Path(_browser_profile_value).resolve() if _browser_profile_value else None)
)

# In a visible browser, leave a real CAPTCHA on screen long enough for the
# operator to solve it. Headless runs still fail fast because no person can
# interact with the challenge there.
CAPTCHA_WAIT_SECONDS = float(os.getenv("CAPTCHA_WAIT_SECONDS", "180"))

# --- Timeouts (milliseconds, Playwright convention) -------------------------
NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "45000"))
ACTION_TIMEOUT_MS = int(os.getenv("ACTION_TIMEOUT_MS", "15000"))
POST_LOAD_SETTLE_MS = int(os.getenv("POST_LOAD_SETTLE_MS", "2500"))

# --- Politeness / retries ---------------------------------------------------
# Kingston fronts memory.kingston.com with Cloudflare bot management. Keep
# the delay between server page loads generous and retries modest — hammering
# the site faster just gets the IP challenged sooner, it doesn't get more
# data. See README "Known limitation: Cloudflare" for details.
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "4.0"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("RETRY_BACKOFF_SECONDS", "5.0"))

# --- Automatic post-run retry phase ------------------------------------------
# After the initial extraction phase finishes, the runner automatically
# retries every server that ended up in the current failed-URL set, up to
# this many additional full passes, before giving up on it for the run.
# This is on top of (not instead of) the per-server navigation retries
# already governed by MAX_RETRIES above. Overridable per-run via
# `--retry-attempts`; set to 0 to disable the automatic retry phase
# entirely (the manual `--retry-failed` flag still works either way).
AUTO_RETRY_ATTEMPTS = int(os.getenv("AUTO_RETRY_ATTEMPTS", "2"))

# --- Cooldown / circuit breaker ---------------------------------------------
# Real-world evidence (a full 259-server run) showed the naive fixed-cadence
# retry never adapts: once Cloudflare starts hard-blocking, it blocks EVERY
# subsequent server at the same short retry cadence indefinitely — server 1
# succeeds, then every server after it fails all 3 attempts, for 30+ servers
# straight, regardless of whether the run is on a datacenter sandbox or a
# normal home/office network. That points at Cloudflare escalating against
# the session's *request pattern* (many distinct pages in quick succession),
# not just IP reputation — so the fix is to stop hammering once a block
# streak is detected and give it real time to cool off, rather than
# retrying at the same pace forever.
#
# After COOLDOWN_TRIGGER_STREAK servers in a row come back Blocked, the
# runner pauses for COOLDOWN_SECONDS before continuing (logged clearly, and
# still resumable/interruptible). This trades run-time for a real shot at
# the block actually lifting, instead of burning through the whole input
# file at a guaranteed-failed cadence.
COOLDOWN_TRIGGER_STREAK = int(os.getenv("COOLDOWN_TRIGGER_STREAK", "2"))
COOLDOWN_SECONDS = float(os.getenv("COOLDOWN_SECONDS", "90"))

# --- Interaction footprint ---------------------------------------------------
# Every spec/part is already present in the initial server-rendered HTML
# (see README) — the "expand collapsed accordions/tabs" pass in
# navigation.py exists only as a defensive fallback for a page structure we
# haven't seen, never as something extraction depends on. It's also pure
# extra automated-looking DOM interaction with no data upside, so it's off
# by default; only turn it on if you hit a real page where content is
# genuinely missing without it.
ENABLE_EXPLORATORY_CLICKS = os.getenv("ENABLE_EXPLORATORY_CLICKS", "false").lower() == "true"

# --- Discovery / debugging --------------------------------------------------
# When true, save the rendered HTML of any page the agent fails to parse
# (blocked, unexpected structure, etc.) to output/debug/ for inspection.
SAVE_DEBUG_HTML_ON_FAILURE = os.getenv("SAVE_DEBUG_HTML_ON_FAILURE", "true").lower() == "true"

BRAND_DEFAULT = "Kingston"
