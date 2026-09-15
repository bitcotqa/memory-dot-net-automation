"""
Flags rows whose part_specification dict is missing an attribute key that the
rest of that row's category (ssd/hdd) normally has, or that uses a differently
spelled key name in its place (e.g. 'Flash' instead of 'Flash Technology').

This is a quick data-quality pass on a single CSV, independent of
validate_vmware_parts.py (which cross-checks against a second, ground-truth CSV).

Usage:
    python check_spec_attribute_naming.py [input.csv] [output.csv]
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

RENAME_MAP = {
    "Flash": "Flash Technology",
    "Partner": "Partner Name",
    "Minimum Version": "Minimum Firmware Version",
}


def extract_keys(spec):
    return re.findall(r"'([A-Za-z0-9 ]+?)'\s*:", spec or "")


def main():
    here = Path(__file__).parent
    in_path = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "input" / "VMWARE_1.csv"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "output" / "Comments_Missing_Report.csv"

    with open(in_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    cat_keys = defaultdict(Counter)
    row_keys = []
    for row in rows:
        keys = extract_keys(row.get("part_specification", ""))
        row_keys.append((row, keys))
        cat_keys[row.get("category", "").strip()][tuple(keys)]  # touch for defaultdict
        for k in keys:
            cat_keys[row.get("category", "").strip()][k] += 1

    core_keys = {}
    for cat, counter in cat_keys.items():
        total = sum(1 for row, _ in row_keys if row.get("category", "").strip() == cat)
        core_keys[cat] = {k for k, c in counter.items() if isinstance(k, str) and c >= total - 1 and c > total / 2}

    out_rows = []
    for row, keys in row_keys:
        cat = row.get("category", "").strip()
        present = set(keys)
        missing = core_keys.get(cat, set()) - present
        notes = []
        for odd, intended in RENAME_MAP.items():
            if odd in present and intended in missing:
                missing.discard(intended)
                notes.append(f"'{intended}' present but mis-labeled as '{odd}' in part_specification")
        if missing:
            notes.append("Missing key(s) in part_specification: " + ", ".join(f"'{m}'" for m in sorted(missing)))
        out_rows.append({
            "category": cat,
            "oem": row.get("oem", "").strip(),
            "part_number": row.get("part_number", "").strip(),
            "mfr_part_number": row.get("mfr_part_number", "").strip(),
            "missing_or_mislabeled_fields": "; ".join(notes) if notes else "None",
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["category", "oem", "part_number", "mfr_part_number", "missing_or_mislabeled_fields"])
        w.writeheader()
        w.writerows(out_rows)

    print(f"Checked {len(out_rows)} rows -> {out_path}")


if __name__ == "__main__":
    main()
