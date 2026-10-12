"""Comparison logic: previously-written CSV row(s) vs. freshly re-extracted
live data for the same URL.

Two different kinds of disagreement are deliberately kept separate:

- **mismatch** — a field that should be stable (a spec, a part number, a
  capacity) came back different. This is either a real site change worth
  knowing about or, just as likely, a sign the extraction logic has
  drifted from the live DOM (the same class of bug
  tests/test_live_playwright_extraction.py exists to catch on one fixture
  URL — this is that same check, run over real sampled output instead).
- **drift** — a field that's *expected* to change over time on a live
  e-commerce site (status/availability, price-adjacent text). Logged for
  visibility, never treated as a failure.

Both diff_server and diff_parts return (mismatches, drift) lists of
(field, old_value, new_value) tuples so callers/report writers don't need
to know field semantics.
"""

import json

# Server-record fields expected to stay stable page-to-page. Excludes
# specifications_json itself (compared separately, by key-set) and
# free-text fields (compatibility / important_configuration_notes) that
# Kingston edits wording on without the underlying spec changing — those
# would generate noisy false positives on a straight string compare.
_SERVER_STABLE_FIELDS = [
    "product_name",
    "server_model",
    "server_type",
    "processor",
    "memory",
    "storage",
    "expansion",
]
_SERVER_VOLATILE_FIELDS = ["status"]

_PART_STABLE_FIELDS = [
    "component_name",
    "component_model",
    "capacity",
    "form_factor",
    "component_url",
    "datasheet_url",
]
_PART_VOLATILE_FIELDS = ["status", "description"]


def _norm(value) -> str:
    return " ".join((value or "").split()).strip().lower()


def diff_server(old_row: dict, live_record) -> tuple:
    mismatches, drift = [], []

    for field in _SERVER_STABLE_FIELDS:
        old_v, new_v = old_row.get(field, ""), getattr(live_record, field, "")
        if _norm(old_v) != _norm(new_v):
            mismatches.append((field, old_v, new_v))

    for field in _SERVER_VOLATILE_FIELDS:
        old_v, new_v = old_row.get(field, ""), getattr(live_record, field, "")
        if _norm(old_v) != _norm(new_v):
            drift.append((field, old_v, new_v))

    try:
        old_keys = set(json.loads(old_row.get("specifications_json") or "{}").keys())
    except (json.JSONDecodeError, TypeError):
        old_keys = None
    try:
        new_keys = set(json.loads(getattr(live_record, "specifications_json", "") or "{}").keys())
    except (json.JSONDecodeError, TypeError):
        new_keys = None

    if old_keys is not None and new_keys is not None and old_keys != new_keys:
        mismatches.append(("specifications_json.keys", sorted(old_keys), sorted(new_keys)))

    return mismatches, drift


def diff_parts(old_rows: list, live_parts: list) -> dict:
    """old_rows: list of CSV DictReader rows (memory or SSD) for one
    server_url. live_parts: list of PartRecord from a fresh extract_parts()
    call on the same URL. Matched by part_number.
    """
    old_by_pn = {r["part_number"]: r for r in old_rows if r.get("part_number")}
    live_by_pn = {p.part_number: p for p in live_parts if p.part_number}

    mismatches, drift = [], []
    for pn, old_row in old_by_pn.items():
        live = live_by_pn.get(pn)
        if live is None:
            continue  # handled as missing_on_live below
        for field in _PART_STABLE_FIELDS:
            old_v, new_v = old_row.get(field, ""), getattr(live, field, "")
            if _norm(old_v) != _norm(new_v):
                mismatches.append((pn, field, old_v, new_v))
        for field in _PART_VOLATILE_FIELDS:
            old_v, new_v = old_row.get(field, ""), getattr(live, field, "")
            if _norm(old_v) != _norm(new_v):
                drift.append((pn, field, old_v, new_v))

    missing_on_live = sorted(set(old_by_pn) - set(live_by_pn))  # in CSV, gone from the live page
    new_on_live = sorted(set(live_by_pn) - set(old_by_pn))  # on the live page, never captured

    return {
        "mismatches": mismatches,
        "drift": drift,
        "missing_on_live": missing_on_live,
        "new_on_live": new_on_live,
    }
