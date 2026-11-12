"""
===============================================================================
 Fujitsu Memory QA Validation Script
===============================================================================
 Purpose
   Automated QA check for the Fujitsu product import CSV. It makes sure the
   DIMM rank information published for every memory part is complete,
   consistent and correctly linked to its server before the data is imported.

 Checks performed
   1. DIMM Rank validation (default run)
      - Row level   : CSV `ranks` + `rank_width` (e.g. 2 + 4 -> "2Rx4") and
                      `dimm_ranks` are compared against the DIMM rank written
                      in `part_description` (e.g. "... DIMM, 2Rx4"), which is
                      the reference value. The rank must also be listed in the
                      server's `server_dimm_ranks`.
      - Server level: rows are grouped by `server_description`. The full set
                      of DIMM ranks offered by the server's memory parts is
                      derived, and every row's `server_dimm_ranks` must contain
                      all of them (no missing, no extra, no repeated values).
      Status values : Matched / Unmatched / Missing / Duplicate.
                      (Missing is reported as Matched; see run_dimm_rank_validation.)
      Output        : dimm_rank_validation_report.xlsx (formatted QA report).

   2. Memory SKU generation (legacy check)
      Run with:  python productid.py --sku

 Usage
   python productid.py          -> DIMM rank QA report (.xlsx) on INPUT_FILE
   python productid.py my.csv   -> DIMM rank QA report on another CSV file
   python productid.py --sku    -> memory SKU check (.csv)

 How to read the comments
   Each code line has a comment above it:
     "What" = what the line does and why it is needed.
     "Uses" = the library, function, variable or CSV column the line relies on.
===============================================================================
"""

# =========================================================================== #
# Imports
# =========================================================================== #
# What: loads the "ast" module, used to turn the text "['1Rx4', '2Rx4']" into a real list.
# Uses: Python standard library (no install needed).
import ast
# What: loads the "re" (regular expression) module, used to find ranks like "2Rx4" inside text.
# Uses: Python standard library.
import re
# What: loads the "sys" module, used to read the --sku switch and to stop the script with a message.
# Uses: Python standard library.
import sys
# What: loads Counter, used to count how many times each rank appears in a list (finds repeats).
# Uses: Python standard library (collections module).
from collections import Counter

# What: loads pandas as "pd"; it reads the CSV and stores data and results as tables (DataFrames).
# Uses: pandas library (pip install pandas).
import pandas as pd
# What: loads FormulaRule, used to colour each Excel row based on its Status value.
# Uses: openpyxl library (pip install openpyxl), conditional formatting module.
from openpyxl.formatting.rule import FormulaRule
# What: loads Excel style classes: Alignment (wrap/centre), Border, Font, PatternFill (cell colour), Side (border line).
# Uses: openpyxl library, styles module.
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
# What: loads get_column_letter, which converts a column number to an Excel letter (1 -> "A").
# Uses: openpyxl library, utils module.
from openpyxl.utils import get_column_letter

# =========================================================================== #
# Configuration (change these when a new data file arrives)
# =========================================================================== #
# What: name of the CSV file that is validated (the data under test).
# Uses: read by run_dimm_rank_validation() and process_and_save_csv().
INPUT_FILE = "31032026_fujitsu_db_import.csv"
# What: name of the Excel QA report that is created.
# Uses: written by write_report().
RANK_REPORT_FILE = "dimm_rank_validation_report.xlsx"
# What: name of the CSV created by the legacy SKU check.
# Uses: written by process_and_save_csv() when run with --sku.
SKU_OUTPUT_FILE = "processed_memory_sku_with_status.csv"

# What: pattern that finds a DIMM rank: a number, "R", "x", a number (e.g. "2Rx4").
#       The two ( ) groups capture the rank (2) and the width (4).
#       \s* allows spaces, IGNORECASE allows "2rx4", so formatting noise is not a defect.
# Uses: re.compile; used by normalize_rank(), rank_from_description(), rank_sort_key().
RANK_RE = re.compile(r"\b(\d+)\s*R\s*x\s*(\d+)\b", re.IGNORECASE)

# What: the four status labels, defined once so every check uses exactly the same words.
# Uses: used by validate_rows(), validate_servers(), the report and the summary.
MATCHED, UNMATCHED, MISSING, DUPLICATE = "Matched", "Unmatched", "Missing", "Duplicate"


# =========================================================================== #
# Helper functions
# Raw CSV values are normalised before comparison, so results reflect real
# data differences, not differences in formatting or empty-cell handling.
# =========================================================================== #
# What: defines a function that loads a CSV file into a table.
# Uses: file_path = path of the CSV.
def read_csv(file_path):
    """Load the CSV with every value as text."""
    # What: reads the CSV. dtype=str keeps every value as text, so "4" is not changed to 4.0
    #       (empty cells become NaN); low_memory=False avoids mixed-type warnings.
    # Uses: pandas.read_csv.
    return pd.read_csv(file_path, dtype=str, low_memory=False)


# What: defines a function that returns a value as clean text, or "" when it is empty.
# Uses: value = any single cell value.
def clean(value):
    """Return a trimmed string, or '' for NaN/None/blank."""
    # What: checks whether the value is empty (None or NaN, which is how pandas stores blank cells).
    # Uses: pandas.isna.
    if value is None or pd.isna(value):
        # What: returns "" so every check can test emptiness the same way ("if not value").
        # Uses: nothing.
        return ""
    # What: converts the value to text and removes spaces at both ends, so " 2Rx4 " equals "2Rx4".
    # Uses: str() and str.strip() (built-in).
    return str(value).strip()


# What: defines a function that rewrites any rank text in one standard format (e.g. "2rx4" -> "2Rx4").
# Uses: value = rank text.
def normalize_rank(value):
    """'2rx4', '2 R x 4' -> '2Rx4'. Returns '' if no rank pattern is found."""
    # What: searches the cleaned text for a rank pattern.
    # Uses: RANK_RE.search, clean().
    m = RANK_RE.search(clean(value))
    # What: if found, rebuilds it as "<rank>Rx<width>" (int() removes leading zeros); otherwise returns "".
    # Uses: m.group(1) = rank, m.group(2) = width, int() (built-in), f-string.
    return f"{int(m.group(1))}Rx{int(m.group(2))}" if m else ""


# What: defines a function that builds the rank (e.g. "2Rx4") from the CSV columns ranks and rank_width.
# Uses: ranks = CSV "ranks" value, rank_width = CSV "rank_width" value.
def rank_from_columns(ranks, rank_width):
    """Build '2Rx4' from the CSV `ranks` and `rank_width` columns ('' if either is blank)."""
    # What: cleans both values (r = rank, w = width).
    # Uses: clean().
    r, w = clean(ranks), clean(rank_width)
    # What: checks whether either value is blank.
    # Uses: r, w.
    if not r or not w:
        # What: returns "" because a rank can't be built; the row is reported as Missing later.
        # Uses: nothing.
        return ""
    # What: starts a block that may fail if the values are not numbers.
    # Uses: Python try/except.
    try:
        # What: builds "2Rx4"; float() first so "2.0" is accepted, then int() gives "2".
        # Uses: float(), int() (built-in), f-string.
        return f"{int(float(r))}Rx{int(float(w))}"
    # What: runs when a value is not a number (e.g. "two").
    # Uses: ValueError (built-in exception).
    except ValueError:
        # What: builds the rank from the raw text, so the bad value shows up in the report as a mismatch
        #       instead of stopping the script.
        # Uses: f-string.
        return f"{r}Rx{w}"


# What: defines a function that reads the expected DIMM rank from the part description.
# Uses: desc = CSV "part_description" value (e.g. "... DDR5, registered, ECC, DIMM, 2Rx4").
def rank_from_description(desc):
    """Extract the DIMM rank stated in `part_description` (last occurrence)."""
    # What: finds every rank pattern in the description; each match is a (rank, width) pair.
    # Uses: RANK_RE.findall, clean().
    found = RANK_RE.findall(clean(desc))
    # What: checks whether no rank was found.
    # Uses: found.
    if not found:
        # What: returns "" (no expected rank); the row is reported as Missing later.
        # Uses: nothing.
        return ""
    # What: takes the LAST match, because the rank is written at the end of the description.
    # Uses: found[-1] (last item of the list).
    r, w = found[-1]
    # What: returns the rank in standard format, e.g. "2Rx4". This is the expected (reference) value.
    # Uses: int(), f-string.
    return f"{int(r)}Rx{int(w)}"


# What: defines a function that turns the server's rank list text into a Python list.
# Uses: value = CSV "server_dimm_ranks" value (e.g. "['1Rx4', '1Rx8', '2Rx4']").
def parse_rank_list(value):
    """Parse `server_dimm_ranks` into a list. Duplicates are kept so repeats can be reported."""
    # What: cleans the text.
    # Uses: clean().
    text = clean(value)
    # What: checks whether the list is empty.
    # Uses: text.
    if not text:
        # What: returns an empty list (the server has no ranks listed).
        # Uses: nothing.
        return []
    # What: starts a block that may fail if the text is not written as a Python list.
    # Uses: Python try/except.
    try:
        # What: converts "['1Rx4', '2Rx4']" into a real list. literal_eval is used instead of eval()
        #       because it can only read data and cannot run code from the file.
        # Uses: ast.literal_eval.
        parsed = ast.literal_eval(text)
        # What: keeps it as is if it's a list/tuple/set; wraps a single value in a list.
        # Uses: isinstance (built-in).
        items = parsed if isinstance(parsed, (list, tuple, set)) else [parsed]
    # What: runs when the text is not valid list syntax (e.g. "1Rx4, 2Rx4").
    # Uses: ValueError, SyntaxError (built-in exceptions).
    except (ValueError, SyntaxError):
        # What: removes the brackets and splits the text on "," ";" or "|".
        # Uses: re.split, str.strip.
        items = re.split(r"[,;|]", text.strip("[]"))
    # What: normalises each item to "2Rx4" format (or keeps the text without quotes if it isn't a rank)
    #       and skips empty items. Repeated items are NOT removed, so duplicates can be reported.
    # Uses: normalize_rank(), clean(), list comprehension.
    return [normalize_rank(i) or clean(i).strip("'\"") for i in items if clean(i).strip("'\"")]


# What: defines a sort key so ranks sort by number (1Rx4, 2Rx4, 10Rx4) instead of alphabetically.
# Uses: rank = rank text; used by fmt_list() with sorted().
def rank_sort_key(rank):
    """Sort ranks numerically for readable report output; unrecognised text sorts last."""
    # What: splits the rank into its numbers.
    # Uses: RANK_RE.search.
    m = RANK_RE.search(rank)
    # What: returns (rank, width) as numbers; (999, 999) puts unrecognised text at the end.
    # Uses: int(), m.group().
    return (int(m.group(1)), int(m.group(2))) if m else (999, 999)


# What: defines a function that turns a group of ranks into readable text, e.g. "1Rx4, 1Rx8, 2Rx4".
# Uses: values = list or set of ranks.
def fmt_list(values):
    """Format a collection of ranks as a sorted, comma-separated string for the report."""
    # What: sorts the ranks numerically and joins them with ", "; returns "" if there are none.
    # Uses: sorted() with rank_sort_key, str.join.
    return ", ".join(sorted(values, key=rank_sort_key)) if values else ""


# What: defines a function that combines the different values of a column into one cell (e.g. all URLs of a server).
# Uses: series = one pandas column.
def join_unique(series):
    """Combine the distinct non-blank values of a column."""
    # What: keeps only non-blank, cleaned values.
    # Uses: clean(), list comprehension.
    vals = [clean(v) for v in series if clean(v)]
    # What: removes repeated values while keeping their order, and joins them with " | ".
    # Uses: dict.fromkeys (keeps first-seen order), str.join.
    return " | ".join(dict.fromkeys(vals))


# =========================================================================== #
# Check 1 - Row-level validation (each memory part)
# Test objective: for every memory part, CSV Rank + Rank Width, CSV DIMM Rank
# and the rank in the Part Description must agree, and the rank must be listed
# for the server.
# =========================================================================== #
# What: defines the function that validates every memory part row.
# Uses: df = full CSV table. Returns one result row per memory part.
def validate_rows(df):
    """Validate the DIMM rank of every memory part row."""
    # What: marks rows whose category is "memory" (ignores case and spaces; blanks become "").
    # Uses: CSV column "category"; pandas fillna, str.strip, str.lower, eq.
    is_memory = df["category"].fillna("").str.strip().str.lower().eq("memory")
    # What: also marks rows that have any rank data, so a rank is never skipped because of a wrong category.
    # Uses: CSV columns "ranks", "rank_width", "dimm_ranks"; pandas notna; "|" means OR.
    has_rank_data = (
        df["ranks"].notna() | df["rank_width"].notna() | df["dimm_ranks"].notna()
    )
    # What: keeps only the marked rows; copy() makes sure the original table is never changed.
    # Uses: is_memory, has_rank_data, pandas boolean filtering, copy().
    mem = df[is_memory | has_rank_data].copy()

    # What: builds a key per row = server description + "||" + part description (lowercase, trimmed).
    #       The same key twice means the same part is listed twice for the same server.
    # Uses: CSV columns "server_description" and "part_description"; pandas string methods.
    dup_key = (
        mem["server_description"].fillna("").str.strip().str.lower()
        + "||"
        + mem["part_description"].fillna("").str.strip().str.lower()
    )
    # What: marks the 2nd and later copies of each key as duplicates (the first copy is the original).
    #       Rows with a blank server AND part description (key "||") are never treated as duplicates,
    #       because there is nothing to identify the part by.
    # Uses: pandas duplicated(keep="first"), "&" means AND.
    is_dup = dup_key.duplicated(keep="first") & (dup_key != "||")
    # What: empty dictionary that will store, for each key, the row of its original (first) copy.
    # Uses: Python dict.
    first_row_of = {}
    # What: loops over every row's key, with its row index.
    # Uses: pandas Series.items().
    for idx, key in dup_key.items():
        # What: stores the index only the first time a key is seen, so it always points to the original row.
        # Uses: dict.setdefault.
        first_row_of.setdefault(key, idx)

    # What: empty list that collects one result per memory row.
    # Uses: Python list.
    records = []
    # What: loops over each memory row; idx = row index in the CSV, row = the row's values.
    # Uses: pandas iterrows().
    for idx, row in mem.iterrows():
        # What: expected rank, taken from the Part Description (the reference value).
        # Uses: CSV column "part_description", rank_from_description().
        expected = rank_from_description(row.get("part_description"))
        # What: CSV Rank value (e.g. "2").
        # Uses: CSV column "ranks", clean().
        csv_rank = clean(row.get("ranks"))
        # What: CSV Rank Width value (e.g. "4").
        # Uses: CSV column "rank_width", clean().
        csv_width = clean(row.get("rank_width"))
        # What: Rank + Width combined (e.g. "2Rx4"), the first actual value to check.
        # Uses: rank_from_columns().
        csv_combined = rank_from_columns(csv_rank, csv_width)
        # What: CSV DIMM Rank exactly as written, shown in the report.
        # Uses: CSV column "dimm_ranks", clean().
        dimm_rank_raw = clean(row.get("dimm_ranks"))
        # What: DIMM Rank in standard format, the second actual value to check.
        # Uses: normalize_rank().
        dimm_rank = normalize_rank(dimm_rank_raw)
        # What: list of ranks the server supports.
        # Uses: CSV column "server_dimm_ranks", parse_rank_list().
        server_list = parse_rank_list(row.get("server_dimm_ranks"))

        # What: empty list for the failure reasons of this row.
        # Uses: Python list.
        reasons = []
        # What: every row starts as Matched; each failed check below changes it.
        # Uses: MATCHED constant.
        status = MATCHED

        # What: checks whether this row is a duplicate.
        # Uses: is_dup, .loc[idx].
        if is_dup.loc[idx]:
            # What: sets the status to Duplicate (the row is not checked again; the original carries the result).
            # Uses: DUPLICATE constant.
            status = DUPLICATE
            # What: finds the index of the original row.
            # Uses: first_row_of, dup_key.
            first = first_row_of[dup_key.loc[idx]]
            # What: adds the reason, with the original's CSV line number (+2 = header row + 1-based count).
            # Uses: list.append, f-string.
            reasons.append(
                f"Duplicate of CSV row {first + 2}: same Server Description and Part Description"
            )
        # What: runs the checks for rows that are not duplicates.
        # Uses: nothing.
        else:
            # What: Test 1 - Completeness. Empty list for the names of blank fields.
            # Uses: Python list.
            missing = []
            # What: checks whether Rank is blank.
            # Uses: csv_rank.
            if not csv_rank:
                # What: records "Rank" as missing.
                # Uses: list.append.
                missing.append("Rank")
            # What: checks whether Rank Width is blank.
            # Uses: csv_width.
            if not csv_width:
                # What: records "Rank Width" as missing.
                # Uses: list.append.
                missing.append("Rank Width")
            # What: checks whether DIMM Rank is blank.
            # Uses: dimm_rank_raw.
            if not dimm_rank_raw:
                # What: records "DIMM Rank" as missing.
                # Uses: list.append.
                missing.append("DIMM Rank")
            # What: checks whether the Part Description has no rank.
            # Uses: expected.
            if not expected:
                # What: records the missing description rank.
                # Uses: list.append.
                missing.append("DIMM Rank in Part Description")

            # What: if any field is blank, the row is Missing.
            # Uses: missing.
            if missing:
                # What: sets the status to Missing.
                # Uses: MISSING constant.
                status = MISSING
                # What: adds a reason listing the blank fields.
                # Uses: str.join, list.append.
                reasons.append("Missing value(s): " + ", ".join(missing))
            # What: all fields are filled in, so the values can be compared.
            # Uses: nothing.
            else:
                # What: Test 2 - Consistency. Checks that Rank + Width equals DIMM Rank.
                # Uses: csv_combined, dimm_rank.
                if csv_combined != dimm_rank:
                    # What: adds the mismatch reason, showing both values.
                    # Uses: list.append, f-string.
                    reasons.append(
                        f"Rank/Rank Width ({csv_combined}) does not match DIMM Rank ({dimm_rank_raw})"
                    )
                # What: Test 3 - Accuracy. Checks that DIMM Rank equals the Part Description rank.
                # Uses: dimm_rank, expected.
                if dimm_rank != expected:
                    # What: adds the mismatch reason.
                    # Uses: list.append, f-string.
                    reasons.append(
                        f"DIMM Rank ({dimm_rank_raw}) does not match Part Description ({expected})"
                    )
                # What: otherwise checks Rank + Width against the Part Description.
                #       "elif" avoids reporting the same problem twice.
                # Uses: csv_combined, expected.
                elif csv_combined != expected:
                    # What: adds the mismatch reason.
                    # Uses: list.append, f-string.
                    reasons.append(
                        f"Rank/Rank Width ({csv_combined}) does not match Part Description ({expected})"
                    )
                # What: any mismatch reason means the row failed.
                # Uses: reasons.
                if reasons:
                    # What: sets the status to Unmatched.
                    # Uses: UNMATCHED constant.
                    status = UNMATCHED

            # What: Test 4 - Server linkage. Picks the rank to look for: DIMM Rank, or the description rank if blank.
            # Uses: dimm_rank, expected.
            check_rank = dimm_rank or expected
            # What: checks that the rank appears in the server's DIMM rank list.
            # Uses: check_rank, server_list, "not in".
            if check_rank and check_rank not in server_list:
                # What: a row that was Matched now fails (a Missing row stays Missing).
                # Uses: status, MATCHED.
                if status == MATCHED:
                    # What: sets the status to Unmatched.
                    # Uses: UNMATCHED constant.
                    status = UNMATCHED
                # What: adds the reason, showing the server's list (or "empty").
                # Uses: fmt_list(), list.append, f-string.
                reasons.append(
                    f"{check_rank} not present in Server DIMM Ranks "
                    f"[{fmt_list(server_list) or 'empty'}]"
                )

        # What: saves this row's result. Only the columns needed for QA review are kept.
        # Uses: list.append, Python dict (key = report column name).
        records.append(
            {
                # What: line number in the CSV file (+1 for the header, +1 because counting starts at 1).
                # Uses: idx.
                "CSV Row": idx + 2,
                # What: server name.  Uses: CSV column "server_description".
                "Server Description": clean(row.get("server_description")),
                # What: host page link.  Uses: CSV column "host_url".
                "Host URL": clean(row.get("host_url")),
                # What: part page link.  Uses: CSV column "part_url".
                "Part URL": clean(row.get("part_url")),
                # What: full part text.  Uses: CSV column "part_description".
                "Part Description": clean(row.get("part_description")),
                # What: expected rank from the description.  Uses: expected.
                "Expected DIMM Rank (Part Desc.)": expected,
                # What: CSV Rank.  Uses: csv_rank.
                "CSV Rank": csv_rank,
                # What: CSV Rank Width.  Uses: csv_width.
                "CSV Rank Width": csv_width,
                # What: Rank + Width combined.  Uses: csv_combined.
                "Rank + Width": csv_combined,
                # What: CSV DIMM Rank as written.  Uses: dimm_rank_raw.
                "CSV DIMM Rank": dimm_rank_raw,
                # What: the server's rank list as readable text.  Uses: fmt_list().
                "Server DIMM Ranks": fmt_list(server_list),
                # What: final status of the row.  Uses: status.
                "Status": status,
                # What: all reasons in one cell, separated by "; ".  Uses: str.join.
                "Failure Reason": "; ".join(reasons),
            }
        )
    # What: returns all row results as one table.
    # Uses: pandas.DataFrame.
    return pd.DataFrame(records)


# =========================================================================== #
# Check 2 - Server-level validation (completeness of server_dimm_ranks)
# Test objective: for each server, `server_dimm_ranks` must list exactly the
# DIMM ranks offered by its memory parts, the same way on every row.
# =========================================================================== #
# What: defines the function that validates each server's DIMM rank list.
# Uses: df = full CSV table, row_report = results from validate_rows(). Returns one result row per server.
def validate_servers(df, row_report):
    """Validate each server's DIMM rank list against the ranks of its memory parts."""
    # What: ignores duplicate rows, so repeated parts don't affect the expected ranks.
    # Uses: row_report "Status" column, DUPLICATE constant, pandas filtering.
    valid = row_report[row_report["Status"] != DUPLICATE]
    # What: empty dictionary: server name -> set of ranks its memory parts use (the expected list).
    # Uses: Python dict.
    expected_by_server = {}
    # What: loops over each non-duplicate memory row.
    # Uses: pandas iterrows().
    for _, r in valid.iterrows():
        # What: takes the Part Description rank (reference); if it's blank, uses the DIMM Rank column instead.
        # Uses: "Expected DIMM Rank (Part Desc.)", "CSV DIMM Rank", normalize_rank().
        rank = r["Expected DIMM Rank (Part Desc.)"] or normalize_rank(r["CSV DIMM Rank"])
        # What: only adds a rank if one was found.
        # Uses: rank.
        if rank:
            # What: adds the rank to that server's set (a set keeps each rank only once).
            # Uses: dict.setdefault, set.add.
            expected_by_server.setdefault(r["Server Description"], set()).add(rank)

    # What: empty list that collects one result per server.
    # Uses: Python list.
    records = []
    # What: adds a cleaned copy of the server name, so names that differ only by spaces are grouped together.
    # Uses: CSV column "server_description", clean(), pandas assign and map.
    df = df.assign(_server=df["server_description"].map(clean))
    # What: loops over each server and all of its rows (memory, SSD, HDD, ...), in name order.
    # Uses: pandas groupby.
    for server, grp in df.groupby("_server", sort=True):
        # What: the ranks this server should list (empty set if it has no memory parts).
        # Uses: expected_by_server, dict.get.
        expected = expected_by_server.get(server, set())
        # What: number of memory parts for this server (shown in the report).
        # Uses: CSV column "category"; pandas fillna, str.lower, eq, sum.
        mem_parts = int(grp["category"].fillna("").str.lower().eq("memory").sum())

        # What: counter of rows with a wrong or empty rank list.
        # Uses: integer.
        rows_failed = 0
        # What: sets that collect missing, extra and repeated ranks found on any row of this server.
        # Uses: Python set.
        missing_all, extra_all, dup_all = set(), set(), set()
        # What: list of each row's rank list as text, to detect rows of the same server that disagree.
        # Uses: Python list.
        actual_variants = []
        # What: checks every row of the server, because each row carries its own copy of server_dimm_ranks.
        # Uses: pandas iterrows().
        for _, row in grp.iterrows():
            # What: this row's rank list.
            # Uses: CSV column "server_dimm_ranks", parse_rank_list().
            lst = parse_rank_list(row.get("server_dimm_ranks"))
            # What: stores the list as sorted text (without repeats) for the consistency check.
            # Uses: set(), fmt_list(), list.append.
            actual_variants.append(fmt_list(set(lst)))
            # What: counts how often each rank appears in the list.
            # Uses: Counter.
            counts = Counter(lst)
            # What: ranks that appear more than once (repeated entries).
            # Uses: counts.items(), set comprehension.
            dups = {k for k, c in counts.items() if c > 1}
            # What: expected ranks that are NOT in the list (missing).
            # Uses: set difference "-".
            miss = expected - set(lst)
            # What: listed ranks that NO memory part uses (extra).
            # Uses: set difference "-".
            extra = set(lst) - expected
            # What: the row fails if anything is missing, extra, repeated, or the list is empty.
            # Uses: miss, extra, dups, lst.
            if miss or extra or dups or not lst:
                # What: counts this failed row.
                # Uses: rows_failed.
                rows_failed += 1
            # What: adds this row's missing ranks to the server total.
            # Uses: set union "|=".
            missing_all |= miss
            # What: adds this row's extra ranks to the server total.
            # Uses: set union "|=".
            extra_all |= extra
            # What: adds this row's repeated ranks to the server total.
            # Uses: set union "|=".
            dup_all |= dups

        # What: the distinct lists found for this server, in the order seen (normally just one).
        # Uses: dict.fromkeys, list().
        variants = list(dict.fromkeys(actual_variants))
        # What: True when every row of the server has an empty rank list.
        # Uses: all() (built-in).
        all_empty = all(v == "" for v in variants)

        # What: empty list for this server's failure reasons.
        # Uses: Python list.
        reasons = []
        # What: no memory parts AND no rank list, so there is nothing to compare.
        # Uses: expected, all_empty.
        if not expected and all_empty:
            # What: sets the status to Missing.
            # Uses: MISSING constant.
            status = MISSING
            # What: adds the reason.
            # Uses: list.append.
            reasons.append("No DIMM rank data: no memory part ranks and Server DIMM Ranks is empty")
        # What: memory parts exist, but the server's rank list was never filled in.
        # Uses: all_empty.
        elif all_empty:
            # What: sets the status to Missing.
            # Uses: MISSING constant.
            status = MISSING
            # What: adds the reason, listing the ranks that should be there.
            # Uses: fmt_list(), list.append, f-string.
            reasons.append(
                f"Server DIMM Ranks is empty; expected {fmt_list(expected)}"
            )
        # What: the server has a rank list, so record every problem found.
        # Uses: nothing.
        else:
            # What: checks for expected ranks missing from the list.
            # Uses: missing_all.
            if missing_all:
                # What: adds the reason with the missing ranks.
                # Uses: fmt_list(), list.append.
                reasons.append(f"Expected rank(s) missing from Server DIMM Ranks: {fmt_list(missing_all)}")
            # What: checks for listed ranks with no memory part.
            # Uses: extra_all.
            if extra_all:
                # What: adds the reason with the extra ranks.
                # Uses: fmt_list(), list.append.
                reasons.append(
                    f"Server DIMM Ranks lists rank(s) with no memory part: {fmt_list(extra_all)}"
                )
            # What: checks for repeated ranks in the list.
            # Uses: dup_all.
            if dup_all:
                # What: adds the reason with the repeated ranks.
                # Uses: fmt_list(), list.append.
                reasons.append(f"Rank(s) repeated in Server DIMM Ranks: {fmt_list(dup_all)}")
            # What: checks whether rows of the same server have different lists.
            # Uses: variants, len().
            if len(variants) > 1:
                # What: adds the reason with the number of different lists.
                # Uses: list.append, f-string.
                reasons.append(f"Server DIMM Ranks differs between rows ({len(variants)} variants)")
            # What: checks whether the server has a list but no memory parts to check it against.
            # Uses: expected.
            if not expected:
                # What: adds the reason.
                # Uses: list.append.
                reasons.append("No memory parts with DIMM ranks found for this server")

            # What: only repeated ranks (nothing missing or extra) -> Duplicate.
            # Uses: dup_all, missing_all, extra_all.
            if dup_all and not (missing_all or extra_all):
                # What: sets the status to Duplicate.
                # Uses: DUPLICATE constant.
                status = DUPLICATE
            # What: any other problem -> Unmatched. This includes a server list that is filled in
            #       but missing ranks its parts use: that is a real defect, so it must not be reported
            #       as Missing (Missing is shown as Matched in the report).
            # Uses: reasons.
            elif reasons:
                # What: sets the status to Unmatched.
                # Uses: UNMATCHED constant.
                status = UNMATCHED
            # What: no problems found.
            # Uses: nothing.
            else:
                # What: sets the status to Matched.
                # Uses: MATCHED constant.
                status = MATCHED

        # What: saves this server's result.
        # Uses: list.append, Python dict (key = report column name).
        records.append(
            {
                # What: server name.  Uses: server (group name).
                "Server Description": server,
                # What: all distinct host links of the server.  Uses: CSV column "host_url", join_unique().
                "Host URL": join_unique(grp["host_url"]),
                # What: all distinct part links of the server.  Uses: CSV column "part_url", join_unique().
                "Part URL": join_unique(grp["part_url"]),
                # What: number of memory parts.  Uses: mem_parts.
                "Memory Parts": mem_parts,
                # What: ranks the server should list.  Uses: expected, fmt_list().
                "Expected DIMM Ranks": fmt_list(expected),
                # What: how many ranks are expected.  Uses: len().
                "Expected Count": len(expected),
                # What: the list(s) actually found in the CSV; "(empty)" for blank lists.  Uses: variants.
                "Server DIMM Ranks (CSV)": " | ".join(v or "(empty)" for v in variants),
                # What: number of distinct ranks across all of this server's lists.
                # Uses: parse_rank_list(), set union, len().
                "Actual Count": len(set().union(*[set(parse_rank_list(v)) for v in grp["server_dimm_ranks"]])),
                # What: expected ranks not listed.  Uses: missing_all, fmt_list().
                "Missing Ranks": fmt_list(missing_all),
                # What: listed ranks with no memory part.  Uses: extra_all, fmt_list().
                "Extra Ranks": fmt_list(extra_all),
                # What: number of rows checked for this server.  Uses: len(grp).
                "Rows Checked": len(grp),
                # What: number of rows with a wrong or empty list.  Uses: rows_failed.
                "Rows Failed": rows_failed,
                # What: final status of the server.  Uses: status.
                "Status": status,
                # What: all reasons in one cell, separated by "; ".  Uses: str.join.
                "Failure Reason": "; ".join(reasons),
            }
        )
    # What: returns all server results as one table.
    # Uses: pandas.DataFrame.
    return pd.DataFrame(records)


# =========================================================================== #
# QA report formatting
# A review-ready Excel report: light headers, frozen first row, filters and
# colour-coded statuses help reviewers find defects quickly. CSV cannot hold
# formatting, so the report is .xlsx.
# =========================================================================== #
# What: colours for each status as (light background, dark text). There is no Missing colour,
#       because Missing is reported as Matched.
# Uses: hex colour codes; used by style_sheet() and write_report().
STATUS_STYLE = {
    # What: green = pass.
    MATCHED: ("C6EFCE", "006100"),
    # What: red = data defect.
    UNMATCHED: ("FFC7CE", "9C0006"),
    # What: blue = duplicate record.
    DUPLICATE: ("DDEBF7", "1F4E78"),
}
# What: light-blue background for the header row.
# Uses: openpyxl PatternFill.
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
# What: bold dark-blue text for the header row.
# Uses: openpyxl Font.
HEADER_FONT = Font(bold=True, color="1F3864", size=11)
# What: thin light-grey line used for cell borders.
# Uses: openpyxl Side.
THIN = Side(style="thin", color="BFBFBF")
# What: border on all four sides of a cell.
# Uses: openpyxl Border, THIN.
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
# What: fixed widths for long-text columns, so URLs and reasons stay readable.
# Uses: used by style_sheet().
WIDE_COLS = {"Failure Reason": 70, "Part Description": 60, "Host URL": 45, "Part URL": 45,
             "Server DIMM Ranks (CSV)": 40, "Server DIMM Ranks": 34, "Server Description": 42}


# What: defines the function that applies the standard report formatting to one Excel tab.
# Uses: ws = Excel worksheet, df = the table written to it.
def style_sheet(ws, df):
    """Apply the standard QA report formatting to one worksheet."""
    # What: freezes the first row, so the header stays visible while scrolling.
    # Uses: openpyxl freeze_panes.
    ws.freeze_panes = "A2"
    # What: adds filter drop-downs to every header cell.
    # Uses: openpyxl auto_filter, ws.dimensions (the used range, e.g. "A1:M121").
    ws.auto_filter.ref = ws.dimensions
    # What: makes the header row taller so wrapped titles fit.
    # Uses: openpyxl row_dimensions.
    ws.row_dimensions[1].height = 30
    # What: loops over each header cell.
    # Uses: ws[1] = first row.
    for cell in ws[1]:
        # What: light-blue background.  Uses: HEADER_FILL.
        cell.fill = HEADER_FILL
        # What: bold dark-blue text.  Uses: HEADER_FONT.
        cell.font = HEADER_FONT
        # What: thin border.  Uses: BORDER.
        cell.border = BORDER
        # What: centres the title and wraps long titles.  Uses: openpyxl Alignment.
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # What: loops over each column with its number (1, 2, 3, ...).
    # Uses: enumerate (built-in), df.columns.
    for i, col in enumerate(df.columns, start=1):
        # What: converts the column number to its Excel letter.
        # Uses: get_column_letter.
        letter = get_column_letter(i)
        # What: uses the fixed width for long-text columns.
        # Uses: WIDE_COLS.
        if col in WIDE_COLS:
            # What: fixed width from the table above.
            # Uses: WIDE_COLS.
            width = WIDE_COLS[col]
        # What: other columns are sized to their content.
        # Uses: nothing.
        else:
            # What: length of the longest value (only the first 500 rows are measured, to stay fast).
            # Uses: len(), max(), df[col].head(500).
            longest = max([len(str(col))] + [len(str(v)) for v in df[col].head(500)])
            # What: width = longest + 2, kept between 12 and 30 characters.
            # Uses: min(), max().
            width = min(max(longest + 2, 12), 30)
        # What: applies the width to the column.
        # Uses: openpyxl column_dimensions.
        ws.column_dimensions[letter].width = width

    # What: columns whose long text should wrap onto several lines.
    # Uses: Python set.
    wrap_cols = {"Failure Reason", "Part Description", "Server DIMM Ranks (CSV)"}
    # What: loops over every data row (row 2 to the last row).
    # Uses: openpyxl iter_rows.
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        # What: loops over every cell in the row.
        # Uses: row.
        for cell in row:
            # What: adds the thin border.  Uses: BORDER.
            cell.border = BORDER
            # What: name of the column this cell belongs to.  Uses: df.columns, cell.column.
            col_name = df.columns[cell.column - 1]
            # What: aligns text to the top; wraps it in long-text columns.  Uses: Alignment, wrap_cols.
            cell.alignment = Alignment(vertical="top", wrap_text=col_name in wrap_cols)
            # What: checks for a single URL in Host URL / Part URL (cells with several URLs joined by " | " stay text).
            # Uses: str.startswith, "not in".
            if col_name in ("Host URL", "Part URL") and str(cell.value or "").startswith("http") \
                    and " | " not in str(cell.value):
                # What: makes the cell a clickable link.  Uses: openpyxl hyperlink.
                cell.hyperlink = cell.value
                # What: blue underlined text, like a normal link.  Uses: openpyxl Font.
                cell.font = Font(color="0563C1", underline="single")

    # What: only colours rows if the tab has a Status column and at least one data row.
    # Uses: df.columns, ws.max_row.
    if "Status" in df.columns and ws.max_row > 1:
        # What: Excel letter of the Status column.
        # Uses: list.index, get_column_letter.
        sc = get_column_letter(list(df.columns).index("Status") + 1)
        # What: Excel letter of the last column.
        # Uses: get_column_letter, len().
        last = get_column_letter(len(df.columns))
        # What: range covering all data rows, e.g. "A2:M121".
        # Uses: f-string.
        rng = f"A2:{last}{ws.max_row}"
        # What: loops over each status and its colours.
        # Uses: STATUS_STYLE.
        for status, (bg, fg) in STATUS_STYLE.items():
            # What: adds a rule "if the Status cell equals this status, tint the whole row".
            #       A rule is used so the colour updates if a reviewer edits the status in Excel.
            # Uses: openpyxl conditional_formatting, FormulaRule, PatternFill.
            ws.conditional_formatting.add(
                rng, FormulaRule(formula=[f'${sc}2="{status}"'],
                                 fill=PatternFill("solid", bgColor=bg))
            )
        # What: loops over every data row.
        # Uses: range().
        for r in range(2, ws.max_row + 1):
            # What: the Status cell of this row.
            # Uses: ws["M2"]-style cell address.
            c = ws[f"{sc}{r}"]
            # What: colours for this status (black on white if the status is unknown).
            # Uses: STATUS_STYLE.get.
            bg, fg = STATUS_STYLE.get(c.value, ("FFFFFF", "000000"))
            # What: bold, coloured status text.  Uses: openpyxl Font.
            c.font = Font(bold=True, color=fg)
            # What: centres the status.  Uses: openpyxl Alignment.
            c.alignment = Alignment(horizontal="center", vertical="top")


# What: defines the function that builds the Summary table (counts and pass rate per check).
# Uses: row_df = Check 1 results, server_df = Check 2 results.
def build_summary(row_df, server_df):
    """Build the summary table: status counts and pass rate for each check."""
    # What: empty list, one entry per check.
    # Uses: Python list.
    rows = []
    # What: loops over the two checks, each with its label and results.
    # Uses: tuple of (label, table).
    for label, data in (("Memory Part Rows (Rank vs DIMM Rank)", row_df),
                        ("Servers (DIMM Rank completeness)", server_df)):
        # What: counts how many rows have each status.
        # Uses: pandas value_counts.
        counts = data["Status"].value_counts() if len(data) else {}
        # What: total number of rows.
        # Uses: len().
        total = len(data)
        # What: adds one summary line.
        # Uses: list.append, Python dict.
        rows.append({
            # What: name of the check.
            "Validation": label,
            # What: total rows.
            "Total": total,
            # What: number Matched.  Uses: counts.get (0 if none).
            MATCHED: int(counts.get(MATCHED, 0)),
            # What: number Unmatched.  Uses: counts.get.
            UNMATCHED: int(counts.get(UNMATCHED, 0)),
            # What: number Duplicate.  Uses: counts.get.
            DUPLICATE: int(counts.get(DUPLICATE, 0)),
            # What: pass rate = Matched / Total as a percentage; "N/A" avoids dividing by zero.
            # Uses: f-string with :.1f (one decimal place).
            "Pass Rate": f"{(counts.get(MATCHED, 0) / total * 100):.1f}%" if total else "N/A",
        })
    # What: returns the summary as a table.
    # Uses: pandas.DataFrame.
    return pd.DataFrame(rows)


# What: defines the function that writes the four-tab Excel report.
# Uses: row_df = Check 1 results, server_df = Check 2 results, output_file = report file name.
def write_report(row_df, server_df, output_file):
    """Write the four-tab QA report: Summary, Server DIMM Ranks, Memory Rank Validation, Row Failures."""
    # What: builds the Summary table.
    # Uses: build_summary().
    summary = build_summary(row_df, server_df)
    # What: Row Failures = defect log. Keeps rows that are not Matched OR that still have a failure reason
    #       (rows that were Missing and are now shown as Matched), so no finding is hidden.
    # Uses: row_df "Status" and "Failure Reason" columns, pandas filtering ("|" means OR).
    failures = row_df[(row_df["Status"] != MATCHED) | (row_df["Failure Reason"] != "")]
    # What: list of (tab name, table) in the order the tabs appear.
    # Uses: Python list.
    sheets = [
        # What: overview: counts and pass rate.
        ("Summary", summary),
        # What: Check 2 results, one row per server.
        ("Server DIMM Ranks", server_df),
        # What: Check 1 results, every memory part.
        ("Memory Rank Validation", row_df),
        # What: defect log for follow-up.
        ("Row Failures", failures),
    ]
    # What: opens the Excel file for writing; it is saved and closed automatically at the end of the block.
    # Uses: pandas ExcelWriter with the openpyxl engine.
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        # What: loops over each tab.
        # Uses: sheets.
        for name, data in sheets:
            # What: writes the table to the tab (index=False: no extra pandas row-number column).
            # Uses: pandas to_excel.
            data.to_excel(writer, sheet_name=name, index=False)
            # What: applies the header, width, border, link and status formatting.
            # Uses: style_sheet().
            style_sheet(writer.sheets[name], data)

        # What: gets the Summary tab, to add colours and a legend.
        # Uses: writer.sheets.
        ws = writer.sheets["Summary"]
        # What: loops over the Summary header cells.
        # Uses: ws[1].
        for cell in ws[1]:
            # What: only the status columns (Matched, Unmatched, Duplicate).
            # Uses: STATUS_STYLE.
            if cell.value in STATUS_STYLE:
                # What: colours for this status.  Uses: STATUS_STYLE.
                bg, fg = STATUS_STYLE[cell.value]
                # What: status background colour.  Uses: PatternFill.
                cell.fill = PatternFill("solid", fgColor=bg)
                # What: bold status text colour.  Uses: Font.
                cell.font = Font(bold=True, color=fg)
        # What: row where the legend starts (two blank rows below the summary table).
        # Uses: ws.max_row.
        start = ws.max_row + 3
        # What: writes the "Status Legend" title in bold.
        # Uses: ws.cell, Font.
        ws.cell(start, 1, "Status Legend").font = Font(bold=True, size=12, color="1F3864")
        # What: text explaining each status, so the report can be read without this script.
        # Uses: Python dict.
        legend = {
            MATCHED: "All values agree, or values are missing (see Failure Reason)",
            UNMATCHED: "Values disagree, or server list has wrong/extra ranks",
            DUPLICATE: "Repeated memory part for the same server, or repeated rank in list",
        }
        # What: loops over each legend entry with its row offset (1, 2, 3).
        # Uses: enumerate, legend.items().
        for i, (status, text) in enumerate(legend.items(), start=1):
            # What: colours for this status.  Uses: STATUS_STYLE.
            bg, fg = STATUS_STYLE[status]
            # What: writes the status name in column A.  Uses: ws.cell.
            c = ws.cell(start + i, 1, status)
            # What: status background colour.  Uses: PatternFill.
            c.fill = PatternFill("solid", fgColor=bg)
            # What: bold status text colour.  Uses: Font.
            c.font = Font(bold=True, color=fg)
            # What: border around the status cell.  Uses: BORDER.
            c.border = BORDER
            # What: writes the meaning in column B, with a border.  Uses: ws.cell, BORDER.
            ws.cell(start + i, 2, text).border = BORDER
        # What: widens column A so labels fit.  Uses: column_dimensions.
        ws.column_dimensions["A"].width = 40
        # What: widens column B so legend text fits.  Uses: column_dimensions.
        ws.column_dimensions["B"].width = 60


# =========================================================================== #
# Test run - DIMM rank validation
# =========================================================================== #
# What: defines the main function that runs both checks and writes the report.
# Uses: input_file (default INPUT_FILE), output_file (default RANK_REPORT_FILE).
def run_dimm_rank_validation(input_file=INPUT_FILE, output_file=RANK_REPORT_FILE):
    """Run both DIMM rank checks end to end and write the QA report."""
    # What: loads the CSV.
    # Uses: read_csv().
    df = read_csv(input_file)

    # What: the columns this check needs.
    # Uses: Python set.
    required = {"server_description", "host_url", "part_url", "part_description", "category",
                "ranks", "rank_width", "dimm_ranks", "server_dimm_ranks"}
    # What: the required columns that are not in the file.
    # Uses: set difference "-", df.columns.
    missing_cols = required - set(df.columns)
    # What: checks whether any required column is missing.
    # Uses: missing_cols.
    if missing_cols:
        # What: stops the script with a clear message, instead of failing part-way through.
        # Uses: sys.exit.
        sys.exit(f"Input file is missing column(s): {', '.join(sorted(missing_cols))}")

    # What: Check 1 - validates every memory part row.
    # Uses: validate_rows().
    row_df = validate_rows(df)
    # What: Check 2 - validates every server's rank list (uses the Check 1 results).
    # Uses: validate_servers().
    server_df = validate_servers(df, row_df)

    # What: reporting rule - shows "Missing" as "Matched" in the row results.
    #       The Failure Reason is not changed, so the missing data is still documented.
    # Uses: pandas Series.replace.
    row_df["Status"] = row_df["Status"].replace(MISSING, MATCHED)
    # What: same reporting rule for the server results.
    # Uses: pandas Series.replace.
    server_df["Status"] = server_df["Status"].replace(MISSING, MATCHED)
    # What: writes the Excel report. If the file is open in Excel it is locked, so the script stops
    #       with a short message instead of a long error.
    # Uses: write_report(), PermissionError (built-in exception), sys.exit.
    try:
        write_report(row_df, server_df, output_file)
    except PermissionError:
        sys.exit(f"Cannot write {output_file}: close it in Excel and run again.")

    # What: prints where the report was saved.
    # Uses: print (built-in).
    print(f"DIMM rank report saved to {output_file}")
    # What: prints the summary table in the console for a quick pass/fail view.
    # Uses: build_summary(), pandas to_string.
    print(build_summary(row_df, server_df).to_string(index=False))


# =========================================================================== #
# Legacy check - memory SKU generation
# Test objective: the `memory_SKU` in the CSV must equal the SKU built from the
# part's attribute columns.
# =========================================================================== #
# What: defines the function that builds the expected SKU (e.g. 32D5ER24560011S1) for one row.
# Uses: row = one CSV row.
def generate_expected_sku(row):
    """Build the expected SKU from the part's attribute columns."""
    # What: capacity number, "32GB" -> 32.
    # Uses: CSV column "capacity", str.replace, int().
    capacity = int(str(row['capacity']).replace('GB', ''))
    # What: memory type code, first + last character for DDR ("DDR5" -> "D5"), otherwise "D4".
    # Uses: CSV column "memory_type", string indexing.
    memory_type = row['memory_type'][0] + row['memory_type'][-1] if 'DDR' in row['memory_type'] else 'D4'
    # What: "E" for ECC memory, "N" for non-ECC.
    # Uses: CSV column "ecc".
    ecc = 'E' if 'ECC' in row['ecc'] else 'N'
    # What: first letter of the DIMM type (e.g. "R" for registered).
    # Uses: CSV column "dimm_type".
    dimm_type = row['dimm_type'][0]
    # What: number of ranks (e.g. 2).
    # Uses: CSV column "ranks", int().
    ranks = int(row['ranks'])
    # What: rank width (e.g. 4).
    # Uses: CSV column "rank_width", int().
    rank_width = int(row['rank_width'])
    # What: speed (e.g. 5600).
    # Uses: CSV column "speed", int().
    speed = int(row['speed'])
    # What: voltage without "V" and ".", "1.1V" -> "11".
    # Uses: CSV column "voltage", str.replace.
    voltage = str(row['voltage']).replace('V', '').replace('.', '')
    # What: first letter of the height value.
    # Uses: CSV column "height".
    height = row['height'][0]
    # What: number of modules.
    # Uses: CSV column "qty", int().
    qty = int(row['qty'])
    # What: joins all parts into the SKU string.
    # Uses: f-string.
    return f"{capacity}{memory_type}{ecc}{dimm_type}{ranks}{rank_width}{speed}{voltage}{height}{qty}"


# What: defines the function that checks every row's memory_SKU and saves the result as CSV.
# Uses: input_file = CSV to check, output_file = CSV to write.
def process_and_save_csv(input_file, output_file):
    """Compare each row's memory_SKU with the generated SKU: Match / Unmatch / Missing."""
    # What: loads the CSV.
    # Uses: pandas.read_csv.
    df = pd.read_csv(input_file)
    # What: empty lists for the generated SKUs and their statuses.
    # Uses: Python lists.
    skus, status = [], []
    # What: loops over every row.
    # Uses: pandas iterrows().
    for _, row in df.iterrows():
        # What: the SKU currently in the CSV.
        # Uses: CSV column "memory_SKU".
        actual_sku = row['memory_SKU']
        # What: checks whether the SKU is blank.
        # Uses: pandas.isna.
        if pd.isna(actual_sku):
            # What: no SKU to generate for this row.  Uses: list.append.
            skus.append(None)
            # What: marks the row as Missing.  Uses: list.append.
            status.append("Missing")
        # What: the row has a SKU, so compare it.
        # Uses: nothing.
        else:
            # What: builds the expected SKU.
            # Uses: generate_expected_sku().
            expected_sku = generate_expected_sku(row)
            # What: stores the expected SKU.  Uses: list.append.
            skus.append(expected_sku)
            # What: "Match" if both SKUs are the same, otherwise "Unmatch".
            # Uses: list.append, "==".
            status.append("Match" if expected_sku == actual_sku else "Unmatch")
    # What: adds the generated SKU column next to the original data.
    # Uses: pandas column assignment.
    df['generated_memory_SKU'] = skus
    # What: adds the status column.
    # Uses: pandas column assignment.
    df['status'] = status
    # What: saves everything to the output CSV (index=False: no extra row-number column).
    # Uses: pandas to_csv.
    df.to_csv(output_file, index=False)
    # What: prints where the file was saved.
    # Uses: print (built-in).
    print(f"Processed data saved to {output_file}")


# =========================================================================== #
# Entry point
# =========================================================================== #
# What: runs the code below only when this file is started directly (python productid.py),
#       not when another script imports it.
# Uses: __name__ (set by Python).
if __name__ == "__main__":
    # What: checks whether the --sku switch was given on the command line.
    # Uses: sys.argv (list of command-line arguments).
    if "--sku" in sys.argv:
        # What: runs the legacy SKU check.
        # Uses: process_and_save_csv(), INPUT_FILE, SKU_OUTPUT_FILE.
        process_and_save_csv(INPUT_FILE, SKU_OUTPUT_FILE)
    # What: no switch given, so run the default check.
    # Uses: nothing.
    else:
        # What: optional CSV file name given on the command line (e.g. python productid.py newfile.csv).
        # Uses: sys.argv, list comprehension (skips switches that start with "--").
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        # What: runs the DIMM rank validation on that file, or on INPUT_FILE if none was given.
        # Uses: run_dimm_rank_validation(), INPUT_FILE.
        run_dimm_rank_validation(args[0] if args else INPUT_FILE)
