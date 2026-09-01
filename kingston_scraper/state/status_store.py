"""Resume/status tracking: a small JSON file keyed by server URL recording
what happened the last time each URL was processed, so a re-run can skip
already-successful servers (--resume) or target only the ones that failed
(--retry-failed) instead of reprocessing everything from scratch.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from config.config import STATUS_PATH
from agent.error_handler import Status
from utils.logger import get_logger

logger = get_logger(__name__)

# Statuses that mean "don't bother re-processing this URL on --resume".
_DONE_STATUSES = {Status.SERVER_SUCCESS, Status.PARTIAL_SUCCESS}


class StatusStore:
    def __init__(self, path=None):
        self.path = Path(path or STATUS_PATH)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read status file %s (%s); starting fresh", self.path, exc)
            return {}                                                                                                                                                                                                                                                                                                                                                   

    def save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, sort_keys=True)

    def record(self, url: str, status: str, extra: dict = None):
        entry = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            entry.update(extra)
        self._data[url] = entry
        self.save()

    def get(self, url: str) -> dict:
        return self._data.get(url, {})

    def is_done(self, url: str) -> bool:
        return self.get(url).get("status") in _DONE_STATUSES

    def is_failed(self, url: str) -> bool:
        return self.get(url).get("status") == Status.SERVER_FAILED

    def counts(self) -> dict:
        totals = {}
        for entry in self._data.values():
            status = entry.get("status", "unknown")
            totals[status] = totals.get(status, 0) + 1
        return totals                                                                                                                                                                                                                                                                                              
