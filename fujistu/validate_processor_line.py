"""
Fujitsu Processor Family / Processor Line Validation
====================================================

Purpose
-------
For every row of the Fujitsu CSV this script:
  1. Reads the list of processors in the row.
  2. Looks up each processor in the Intel and AMD reference CSVs.
  3. Collects the Processor Family and Processor Line those processors belong to
     (this is the EXPECTED value).
  4. Compares it with the Processor Family and Processor Line written in the
     Fujitsu row (this is the ACTUAL value).
  5. Marks each field PASS / FAIL / DUPLICATED and explains every problem.

Outputs (written to the same folder as this script)
---------------------------------------------------
  processor_validation_report.csv / .xlsx          -> one line for every Fujitsu row
  processor_validation_mismatch_details.csv / .xlsx -> only rows that are not PASS

Run
---
  python validate_processor_line.py

See README.md for full documentation.
"""

# =============================================================================
# 1. IMPORTS - libraries the script needs
# =============================================================================
import ast                      # safely turns text like "['a', 'b']" into a real Python list
import re                       # regular expressions, used to clean up text before comparing
import sys                      # used to return an exit code when the script finishes
from pathlib import Path        # builds file paths that work on any operating system

import pandas as pd             # reads and writes CSV files as tables (DataFrames)
from openpyxl import Workbook   # creates Excel (.xlsx) files
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # Excel cell styling
from openpyxl.utils import get_column_letter                            # column number -> letter (1 -> "A")


# =============================================================================
# 2. CONFIGURATION - input and output file locations
#    Change the file names here when a new dated export arrives.
# =============================================================================
HERE = Path(__file__).parent                                   # folder that contains this script

FUJITSU = HERE / "31032026_fujitsu_db_import.csv"              # data to be validated
REFS = [                                                       # reference ("source of truth") data
    ("Intel", HERE / "16062026_intel_db_import.csv"),
    ("AMD", HERE / "02062026_amd_db_import.csv"),
]

OUT_ROWS = HERE / "processor_validation_report.csv"                    # full report, plain CSV
OUT_DETAIL = HERE / "processor_validation_mismatch_details.csv"        # problems only, plain CSV
OUT_XLSX = HERE / "processor_validation_report.xlsx"                   # full report, formatted Excel
OUT_DETAIL_XLSX = HERE / "processor_validation_mismatch_details.xlsx"  # problems only, formatted Excel

# Trademark symbols (and the "?" replacement character) are removed before comparing,
# so "Intel® Xeon®" and "Intel Xeon" are treated as the same text.
# Status order used for the overall result (first one found wins).
STATUS_PRIORITY = ["FAIL", "MISSING", "DUPLICATED", "PASS"]

SYMBOLS = re.compile(r"[™®©�]|\(r\)|\(tm\)", re.I)


# =============================================================================
# 3. HELPER FUNCTIONS - text cleaning, parsing, lookup and comparison
# =============================================================================
def norm(s: str) -> str:
    """Normalise text for comparison: drop ™/®/©, collapse spaces, make lower-case."""
    s = SYMBOLS.sub("", str(s))                  # remove trademark symbols
    s = re.sub(r"\s+", " ", s).strip().lower()   # many spaces -> one space, trim ends, lower-case
    return s


def proc_key(s: str) -> str:
    """Matching key for a processor name.

    Same as norm(), and additionally ignores the word "processor", because the Intel
    file says "Intel® Xeon® Gold 6126 Processor" while Fujitsu says "Intel® Xeon® Gold 6126".
    """
    without_word = re.sub(r"\bprocessor\b", " ", norm(s))   # delete the whole word "processor"
    return re.sub(r"\s+", " ", without_word).strip()        # tidy up the leftover spaces


def parse_list(v: str) -> list[str]:
    """Turn a Fujitsu cell such as "['EPYC', 'Ryzen']" into ['EPYC', 'Ryzen']."""
    v = (v or "").strip()                        # treat missing cells as empty text
    if not v:
        return []                                # empty cell -> empty list
    # Excel/Word sometimes replace straight quotes with curly ones, which breaks parsing.
    v = v.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    if v.startswith("["):                        # looks like a list
        try:
            # literal_eval reads the list safely; keep only non-blank items
            return [str(x).strip() for x in ast.literal_eval(v) if str(x).strip()]
        except (ValueError, SyntaxError):
            pass                                 # not a valid list -> fall through
    return [v]                                   # plain text -> a list with one item


def load_reference():
    """Build a lookup table from the Intel and AMD files.

    Result: {processor matching key: (source, processor, family, line)}
    """
    ref = {}
    for src, path in REFS:                                               # Intel first, then AMD
        df = pd.read_csv(path, dtype=str, keep_default_na=False,         # read everything as text,
                         encoding_errors="replace")                      # blanks stay "" (not NaN)
        for p, fam, line in zip(df["processor"], df["processor_family"], df["processor_line"]):
            k = proc_key(p)                                              # matching key for this processor
            if k and k not in ref:                                       # keep the first record only
                ref[k] = (src, p.strip(), fam.strip(), line.strip())
    return ref


def find_dups(values: list[str]) -> list[str]:
    """Return values that appear more than once (ignoring symbols/case/spacing)."""
    seen, dups = {}, []
    for v in values:
        k = norm(v)                              # compare cleaned-up text
        if k in seen and seen[k] == 1:           # second time we see it -> report once
            dups.append(v)
        seen[k] = seen.get(k, 0) + 1             # count occurrences
    return dups


def compare(expected: dict, actual: list[str]):
    """Compare EXPECTED values (from Intel/AMD) with ACTUAL values (from Fujitsu).

    expected : {normalised value: display value}
    actual   : list of values from the Fujitsu cell
    Returns  : (status, missing values, extra values, reason text)
    """
    act = {norm(a): a for a in actual if norm(a)}                   # Fujitsu values, cleaned
    missing = [expected[k] for k in expected if k not in act]       # expected but not in Fujitsu
    extra = [act[k] for k in act if k not in expected]              # in Fujitsu but not expected
    dups = find_dups(actual)                                        # same value written twice
    dup_reason = f"Duplicated value(s) in Fujitsu: {dups}" if dups else ""

    if not missing and not extra:                                   # values agree...
        if dups:
            return "DUPLICATED", missing, extra, dup_reason         # ...but something is repeated
        return "PASS", missing, extra, ""                           # ...and nothing is repeated

    reasons = [dup_reason] if dups else []
    if missing:
        if not act:                                                 # Fujitsu cell is completely empty
            reasons.append(f"Fujitsu value is empty; expected from reference: {missing}")
        else:                                                       # some expected values not listed
            reasons.append(f"Missing in Fujitsu (expected from reference): {missing}")
    if extra:
        reasons.append(f"Present in Fujitsu but not supported by any matched processor: {extra}")
    # Only missing values (nothing wrong present) -> MISSING; any wrong value present -> FAIL
    status = "FAIL" if extra else "MISSING"
    return status, missing, extra, " | ".join(reasons)


# =============================================================================
# 4. MAIN VALIDATION - runs once for every row of the Fujitsu file
# =============================================================================
def main():
    ref = load_reference()                                          # Intel + AMD lookup table
    fj = pd.read_csv(FUJITSU, dtype=str, keep_default_na=False,     # Fujitsu data, all as text
                     encoding_errors="replace")
    rows, details = [], []                                          # collected report lines
    first_seen = {}                                                 # row content -> first row number

    for idx, r in fj.iterrows():                                    # loop over EVERY row, none skipped
        row_no = idx + 2                                            # row number as seen in Excel (header = 1)
        procs = parse_list(r["processor"])                          # processors in this row
        fams = parse_list(r["processor_family"])                    # Fujitsu families (actual)
        lines = parse_list(r["processor_line"])                     # Fujitsu lines (actual)

        # --- Step A: look up each processor and build the EXPECTED family/line values
        exp_fam, exp_line, unmatched, matched = {}, {}, [], 0
        for p in procs:
            hit = ref.get(proc_key(p))                              # find it in Intel/AMD
            if not hit:
                unmatched.append(p)                                 # not in either reference file
                continue
            matched += 1
            src, _, fam, line = hit
            if fam:
                exp_fam.setdefault(norm(fam), fam)                  # add family (no repeats)
            if line:
                exp_line.setdefault(norm(line), line)               # add line (no repeats)

        # --- Step B: validate each field separately (one failing does not stop the other)
        fam_status, fam_miss, fam_extra, fam_reason = compare(exp_fam, fams)
        line_status, line_miss, line_extra, line_reason = compare(exp_line, lines)
        if not procs:                                               # nothing to look up in this row
            note = "Processor list is empty; nothing to look up"
            fam_reason = fam_reason or note
            line_reason = line_reason or note

        # --- Step C: duplicate checks for the whole row and for the processor list
        dup_reasons = []
        key = tuple(r.values)                                       # entire row content
        if key in first_seen:
            dup_reasons.append(f"Entire row is identical to row {first_seen[key]}")
        else:
            first_seen[key] = row_no                                # remember the first copy
        proc_dups = find_dups(procs)
        if proc_dups:
            dup_reasons.append(f"Processor listed more than once: {proc_dups}")
        dup_status = "DUPLICATED" if dup_reasons else "UNIQUE"

        # --- Step D: overall status - worst result wins: FAIL > MISSING > DUPLICATED > PASS
        statuses = {fam_status, line_status, "DUPLICATED" if dup_reasons else "PASS"}
        overall = next(s for s in STATUS_PRIORITY if s in statuses)

        # --- Step E: one line for the full report (every row)
        rows.append({
            "row_number": row_no,
            "file_name": r.get("file_name", ""),
            "processor": "; ".join(procs),
            "processor_count": len(procs),
            "matched_count": matched,
            "unmatched_count": len(unmatched),
            "unmatched_processors": "; ".join(unmatched),
            "family_status": fam_status,
            "family_expected": "; ".join(exp_fam.values()),
            "family_actual": "; ".join(fams),
            "family_reason": fam_reason,
            "line_status": line_status,
            "line_expected": "; ".join(exp_line.values()),
            "line_actual": "; ".join(lines),
            "line_reason": line_reason,
            "duplicate_status": dup_status,
            "duplicate_reason": " | ".join(dup_reasons),
            "overall_status": overall,
        })

        # --- Step F: one line for the details report (only rows with a problem)
        if overall != "PASS":
            details.append({
                "row_number": row_no,
                "file_name": r.get("file_name", ""),
                "processor": "; ".join(procs),
                "family_status": fam_status,
                "family_expected": "; ".join(exp_fam.values()),
                "family_actual": "; ".join(fams),
                "family_reason": fam_reason,
                "line_status": line_status,
                "line_expected": "; ".join(exp_line.values()),
                "line_actual": "; ".join(lines),
                "line_missing_in_fujitsu": "; ".join(line_miss),
                "line_extra_in_fujitsu": "; ".join(line_extra),
                "line_reason": line_reason,
                "duplicate_status": dup_status,
                "duplicate_reason": " | ".join(dup_reasons),
                "overall_status": overall,
            })

    # --- Step G: save the results
    out = pd.DataFrame(rows)
    out.to_csv(OUT_ROWS, index=False, encoding="utf-8-sig")         # utf-8-sig keeps ™/® readable in Excel
    det = pd.DataFrame(details, columns=[                           # fixed columns, even when empty
        "row_number", "file_name", "processor",
        "family_status", "family_expected", "family_actual", "family_reason",
        "line_status", "line_expected", "line_actual", "line_missing_in_fujitsu", "line_extra_in_fujitsu", "line_reason",
        "duplicate_status", "duplicate_reason", "overall_status"])
    det.to_csv(OUT_DETAIL, index=False, encoding="utf-8-sig")
    write_xlsx(out, det, len(fj))                                   # formatted Excel versions

    # --- Step H: print a short summary in the terminal
    n = len(out)
    print(f"Rows validated: {n} (of {len(fj)} in Fujitsu file)")
    for col in ("family_status", "line_status", "duplicate_status", "overall_status"):
        vc = out[col].value_counts()
        print(f"{col:16s} " + "  ".join(f"{k}={v}" for k, v in vc.items()))
    tot_p = out.processor_count.sum()
    print(f"Processor entries: {tot_p}, matched: {out.matched_count.sum()}, unmatched: {out.unmatched_count.sum()}")
    print(f"Wrote {OUT_ROWS.name}, {OUT_DETAIL.name}, {OUT_XLSX.name} and {OUT_DETAIL_XLSX.name}")


# =============================================================================
# 5. EXCEL FORMATTING - colours and layout for the .xlsx reports
# =============================================================================
HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")    # soft steel blue header
HEADER_FONT = Font(name="Calibri", bold=True, color="1F3864")
PASS_FILL, PASS_FONT = PatternFill("solid", fgColor="E2EFDA"), Font(name="Calibri", bold=True, color="375623")
FAIL_FILL, FAIL_FONT = PatternFill("solid", fgColor="FBE2E0"), Font(name="Calibri", bold=True, color="9C0006")
DUP_FILL, DUP_FONT = PatternFill("solid", fgColor="FFF2CC"), Font(name="Calibri", bold=True, color="7F6000")
MISS_STATUS_FILL = PatternFill("solid", fgColor="FCE4D6")  # light orange - MISSING status
MISS_STATUS_FONT = Font(name="Calibri", bold=True, color="C55A11")
MISSING_FILL = PatternFill("solid", fgColor="FDEBDD")   # light peach    - expected value missing
EXTRA_FILL = PatternFill("solid", fgColor="EEE8F7")     # light lavender - unexpected value present
REASON_FILL = PatternFill("solid", fgColor="FFF9E6")    # light cream    - explanation text
EXPECTED_FILL = PatternFill("solid", fgColor="EAF3FB")  # light blue     - reference (expected) value
ACTUAL_FILL = PatternFill("solid", fgColor="F3F3F3")    # light grey     - Fujitsu (actual) value
BAND_FILL = PatternFill("solid", fgColor="F7F9FC")      # subtle zebra striping on other cells
THIN = Side(style="thin", color="C9D3E0")               # thin grey-blue border line
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BODY_FONT = Font(name="Calibri", color="262626")

STATUS_COLS = {"family_status", "line_status", "overall_status", "duplicate_status"}
STATUS_STYLE = {"PASS": (PASS_FILL, PASS_FONT), "UNIQUE": (PASS_FILL, PASS_FONT),
                "FAIL": (FAIL_FILL, FAIL_FONT), "MISSING": (MISS_STATUS_FILL, MISS_STATUS_FONT),
                "DUPLICATED": (DUP_FILL, DUP_FONT)}

# Upper limit on column width (characters) so long lists do not create huge columns.
MAX_WIDTH = {"processor": 70, "unmatched_processors": 50, "family_expected": 55, "family_actual": 55,
             "line_expected": 55, "line_actual": 55, "family_reason": 60, "line_reason": 60,
             "line_missing_in_fujitsu": 45, "line_extra_in_fujitsu": 45, "duplicate_reason": 50,
             "file_name": 40}


def _sheet(wb, title, df):
    """Write one table to a new worksheet and apply the professional formatting."""
    ws = wb.create_sheet(title)
    cols = list(df.columns)
    ws.append([c.replace("_", " ").title() for c in cols])          # header: "family_status" -> "Family Status"
    for rec in df.itertuples(index=False):                          # data rows
        ws.append(list(rec))

    # Header styling and column widths
    for ci, name in enumerate(cols, 1):
        h = ws.cell(row=1, column=ci)
        h.fill, h.font, h.border = HEADER_FILL, HEADER_FONT, BORDER
        h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        longest = max([len(str(h.value))] + [len(str(v)) for v in df[name].head(500)])
        ws.column_dimensions[get_column_letter(ci)].width = min(max(longest + 2, 10), MAX_WIDTH.get(name, 22))

    # Body styling: status colours first, then light tints, then zebra stripes
    for ri, row in enumerate(ws.iter_rows(min_row=2), start=2):
        band = ri % 2 == 1                                          # every other row
        for ci, cell in enumerate(row):
            cell.border, cell.font = BORDER, BODY_FONT
            cell.alignment = Alignment(vertical="top")
            name = cols[ci]
            if name in STATUS_COLS and cell.value in STATUS_STYLE:  # PASS / FAIL / DUPLICATED / UNIQUE
                cell.fill, cell.font = STATUS_STYLE[cell.value]
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif cell.value not in (None, "") and "missing" in name:
                cell.fill = MISSING_FILL
            elif cell.value not in (None, "") and ("extra" in name or name == "unmatched_processors"):
                cell.fill = EXTRA_FILL
            elif cell.value not in (None, "") and "reason" in name:
                cell.fill = REASON_FILL
            elif name.endswith("_expected"):
                cell.fill = EXPECTED_FILL
            elif name.endswith("_actual"):
                cell.fill = ACTUAL_FILL
            elif band:
                cell.fill = BAND_FILL

    # Sheet-level settings
    ws.row_dimensions[1].height = 32                # taller header row
    ws.sheet_properties.tabColor = "8EA9DB"         # soft blue sheet tab
    ws.page_setup.orientation = "landscape"         # print in landscape
    ws.print_title_rows = "1:1"                     # repeat header on every printed page
    ws.freeze_panes = "A2"                          # freeze the first row
    ws.auto_filter.ref = ws.dimensions              # filter drop-downs on the header
    ws.sheet_view.zoomScale = 90
    return ws


def write_xlsx(out, det, total_rows):
    """Create both Excel files: the full report (3 sheets) and the details-only file."""
    wb = Workbook()
    wb.remove(wb.active)                            # remove the default empty sheet

    # Summary sheet: headline counts
    summary = pd.DataFrame([
        {"metric": "Fujitsu rows in file", "value": total_rows},
        {"metric": "Rows validated", "value": len(out)},
        {"metric": "Processor Family PASS", "value": int((out.family_status == "PASS").sum())},
        {"metric": "Processor Family FAIL", "value": int((out.family_status == "FAIL").sum())},
        {"metric": "Processor Line PASS", "value": int((out.line_status == "PASS").sum())},
        {"metric": "Processor Line FAIL", "value": int((out.line_status == "FAIL").sum())},
        {"metric": "Processor Family MISSING", "value": int((out.family_status == "MISSING").sum())},
        {"metric": "Processor Line MISSING", "value": int((out.line_status == "MISSING").sum())},
        {"metric": "Processor Family DUPLICATED", "value": int((out.family_status == "DUPLICATED").sum())},
        {"metric": "Processor Line DUPLICATED", "value": int((out.line_status == "DUPLICATED").sum())},
        {"metric": "Rows DUPLICATED (whole row / processor)", "value": int((out.duplicate_status == "DUPLICATED").sum())},
        {"metric": "Overall PASS", "value": int((out.overall_status == "PASS").sum())},
        {"metric": "Overall FAIL", "value": int((out.overall_status == "FAIL").sum())},
        {"metric": "Overall MISSING", "value": int((out.overall_status == "MISSING").sum())},
        {"metric": "Overall DUPLICATED", "value": int((out.overall_status == "DUPLICATED").sum())},
        {"metric": "Processor entries", "value": int(out.processor_count.sum())},
        {"metric": "Processors matched in Intel/AMD", "value": int(out.matched_count.sum())},
        {"metric": "Processors not found in Intel/AMD", "value": int(out.unmatched_count.sum())},
    ])
    ws = _sheet(wb, "Summary", summary)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 38, 14
    for r in ws.iter_rows(min_row=2):               # colour the count cells by meaning
        if r[0].value.endswith("FAIL") and r[1].value:
            r[1].fill, r[1].font = FAIL_FILL, FAIL_FONT
        elif r[0].value.endswith("MISSING") and r[1].value:
            r[1].fill, r[1].font = MISS_STATUS_FILL, MISS_STATUS_FONT
        elif "DUPLICATED" in r[0].value and r[1].value:
            r[1].fill, r[1].font = DUP_FILL, DUP_FONT
        elif r[0].value.endswith("PASS"):
            r[1].fill, r[1].font = PASS_FILL, PASS_FONT

    _sheet(wb, "Validation Report", out)            # every Fujitsu row
    _sheet(wb, "Mismatch Details", det)             # problem rows only
    wb.active = 1                                   # open on "Validation Report"
    wb.save(OUT_XLSX)

    wb2 = Workbook()                                # separate details-only workbook
    wb2.remove(wb2.active)
    _sheet(wb2, "Mismatch Details", det)
    wb2.save(OUT_DETAIL_XLSX)


# =============================================================================
# 6. ENTRY POINT - runs main() when the file is executed directly
# =============================================================================
if __name__ == "__main__":
    sys.exit(main())
