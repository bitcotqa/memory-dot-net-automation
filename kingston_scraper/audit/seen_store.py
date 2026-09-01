"""Rotating-sample bookkeeping for the output auditor.

The audit never re-checks the whole output in one run (that's exactly the
request-pattern Cloudflare escalates against — see README "Known
limitation: Cloudflare"). Instead each run samples a handful of server URLs
it hasn't audited recently, so a full sweep of the output completes over
many small, spaced-out runs instead of one big one.

Kept as its own tiny JSON file (state/audit_seen.json) rather than reusing
state/status_store.py's processing_status.json — that file's job is
resume/retry for the *scrape*; this one's job is coverage rotation for the
*audit*, a different lifecycle that shouldn't be able to collide with or
be reset by scrape runs.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from config.config import STATE_DIR
from utils.logger import get_logger

logger = get_logger(__name__)

AUDIT_SEEN_PATH = STATE_DIR / "audit_seen.json"


class AuditSeenStore:
    def __init__(self, path=None):
        self.path = Path(path or AUDIT_SEEN_PATH)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read audit-seen file %s (%s); starting fresh", self.path, exc)
            return {}

    def save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, sort_keys=True)

    def last_audited(self, key: str) -> str:
        """ISO timestamp key was last audited, or '' if never."""
        return self._data.get(key, "")

    def mark_audited(self, key: str):
        self._data[key] = datetime.now(timezone.utc).isoformat()

    def sort_by_staleness(self, keys: list) -> list:
        """keys never audited first (in input order), then audited keys
        oldest-first — so a rotating series of small runs eventually
        covers everything and then cycles back to the oldest checks."""
        never = [k for k in keys if not self.last_audited(k)]
        seen = [k for k in keys if self.last_audited(k)]
        seen.sort(key=lambda k: self.last_audited(k))
        return never + seen
