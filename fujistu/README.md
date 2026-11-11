# Fujitsu Processor Family & Processor Line Validation

Automated QA check that validates the **Processor Family** and **Processor Line** columns of the Fujitsu product CSV against Intel and AMD reference data. It produces colour-coded Excel reports that show the expected value, the actual value and the reason for every problem.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Folder Contents](#2-folder-contents)
3. [Prerequisites](#3-prerequisites)
4. [How to Run](#4-how-to-run)
5. [Validation Logic](#5-validation-logic)
6. [Status Definitions](#6-status-definitions)
7. [Output Reports](#7-output-reports)
8. [Excel Colour Legend](#8-excel-colour-legend)
9. [Script Walkthrough](#9-script-walkthrough)
10. [Updating for New Data Files](#10-updating-for-new-data-files)
11. [Troubleshooting](#11-troubleshooting)
12. [Known Limitations](#12-known-limitations)

---

## 1. Overview

| Item | Detail |
|---|---|
| **Script** | `validate_processor_line.py` |
| **Input under test** | `31032026_fujitsu_db_import.csv` |
| **Reference data** | `16062026_intel_db_import.csv`, `02062026_amd_db_import.csv` |
| **Fields validated** | `processor_family`, `processor_line` |
| **Coverage** | Every Fujitsu row is validated. No rows are skipped. |
| **Result per field** | `PASS`, `FAIL`, `MISSING` or `DUPLICATED`, with a reason |

For each Fujitsu row, the script:

1. Reads the **Processor** value (a list of processors).
2. Finds each processor in the Intel or AMD reference data.
3. Takes the **Processor Family** and **Processor Line** from the matched reference records. This is the **expected** value.
4. Compares the expected value with the Fujitsu **Processor Family** and **Processor Line**. This is the **actual** value.
5. Validates each field separately. If one field fails, the other is still validated.
6. Reports the expected value, the actual value and the reason for every mismatch or duplicate.

---

## 2. Folder Contents

| File | Type | Description |
|---|---|---|
| `validate_processor_line.py` | Script | The validation script |
| `README.md` | Docs | This document |
| `31032026_fujitsu_db_import.csv` | Input | Fujitsu data under test |
| `16062026_intel_db_import.csv` | Reference | Intel processor reference data |
| `02062026_amd_db_import.csv` | Reference | AMD processor reference data |
| `processor_validation_report.xlsx` | Output | Formatted report with Summary, Validation Report and Mismatch Details sheets |
| `processor_validation_mismatch_details.xlsx` | Output | Formatted report of problem rows only |
| `processor_validation_report.csv` | Output | Plain-data version of the full report |
| `processor_validation_mismatch_details.csv` | Output | Plain-data version of the problem rows |

> The output files are created again, and overwritten, every time the script runs.

---

## 3. Prerequisites

| Requirement | Version tested |
|---|---|
| Python | 3.11 |
| pandas | 3.0.5 |
| openpyxl | 3.1.5 |

Install the libraries once:

```powershell
pip install pandas openpyxl
```

---

## 4. How to Run

Open **PowerShell** and run:

```powershell
python "C:\Automation\fujistu\processor line\validate_processor_line.py"
```

If `python` is not recognised, use the Windows launcher:

```powershell
py "C:\Automation\fujistu\processor line\validate_processor_line.py"
```

**Example console output:**

```
Rows validated: 1474 (of 1474 in Fujitsu file)
family_status    PASS=1468  FAIL=4  DUPLICATED=1  MISSING=1
line_status      PASS=1474
duplicate_status DUPLICATED=1423  UNIQUE=51
overall_status   DUPLICATED=1424  PASS=45  FAIL=4  MISSING=1
Processor entries: 103152, matched: 101435, unmatched: 1717
Wrote processor_validation_report.csv, processor_validation_mismatch_details.csv, processor_validation_report.xlsx and processor_validation_mismatch_details.xlsx
```

> **Close the output `.xlsx` files in Excel before running.** Windows locks open files, so the script cannot overwrite them.

---

## 5. Validation Logic

### 5.1 Data format

Each Fujitsu row lists **many processors**, and its family and line columns hold the **combined set** of families and lines for those processors:

```
processor        : ['AMD EPYC™ 9015', 'AMD EPYC™ 9115', 'AMD EPYC™ 9124', ...]
processor_family : ['EPYC']
processor_line   : []
```

So the check works per row. It compares the set of values expected from the reference data with the set of values written in Fujitsu.

### 5.2 Processor matching

Processor names are worded differently in each file, so names are normalised before matching:

| Rule | Example |
|---|---|
| Remove ™ ® © symbols | `Intel® Xeon®` → `Intel Xeon` |
| Ignore case and extra spaces | `INTEL  xeon` → `intel xeon` |
| Ignore the word "Processor" | `Intel Xeon Gold 6126 Processor` (Intel file) = `Intel Xeon Gold 6126` (Fujitsu) |
| Accept curly quotes in list cells | `’PRO’` is read as `'PRO'` |

These rules were tested against all Intel and AMD records. No two different processors end up with the same matching key, so no processor can be assigned the wrong family or line.

### 5.3 Field comparison

For each field (family and line):

| Situation | Result |
|---|---|
| Expected set = actual set, no repeats | `PASS` |
| Expected set = actual set, but a value is written twice | `DUPLICATED` |
| Fujitsu cell is empty, but the reference expects values | `MISSING`: *Fujitsu value is empty; expected from reference* |
| Some expected values are not in Fujitsu, and nothing wrong is listed | `MISSING`: *Missing in Fujitsu* |
| Fujitsu has a value that no matched processor supports (with or without missing values) | `FAIL`: *Present in Fujitsu but not supported* |

### 5.4 Duplicate checks

| Check | Example reason |
|---|---|
| Same value repeated in the family or line cell | `Duplicated value(s) in Fujitsu: ['2nd Gen Intel Xeon Scalable Processors']` |
| Whole row identical to an earlier row | `Entire row is identical to row 5` (the first copy stays `UNIQUE`) |
| Same processor listed twice in one row | `Processor listed more than once: [...]` |

### 5.5 Overall status

The worst result wins: `FAIL` > `MISSING` > `DUPLICATED` > `PASS`.

---

## 6. Status Definitions

| Status | Meaning |
|---|---|
| `PASS` | The Fujitsu value matches the reference data exactly |
| `FAIL` | The Fujitsu value contains a wrong value that the reference data does not support |
| `MISSING` | The Fujitsu value is empty or incomplete; expected values are not listed, but nothing wrong is present |
| `DUPLICATED` | Values match, but data is repeated (within a cell, or the whole row) |
| `UNIQUE` | (duplicate_status only) The row is not a repeat of an earlier row |

---

## 7. Output Reports

### 7.1 `processor_validation_report.xlsx`

| Sheet | Contents |
|---|---|
| **Summary** | Row counts and PASS / FAIL / MISSING / DUPLICATED totals for each field and overall, plus processor match counts |
| **Validation Report** | One line for **every** Fujitsu row |
| **Mismatch Details** | One line for every row that is **not** `PASS` |

### 7.2 Validation Report columns

| Column | Description |
|---|---|
| `row_number` | Row number in the Fujitsu CSV as seen in Excel (header = row 1) |
| `file_name` | Source datasheet PDF for the row |
| `processor` | All processors in the row, separated by `;` |
| `processor_count` | Number of processors in the row |
| `matched_count` | Processors found in the Intel/AMD reference data |
| `unmatched_count` | Processors not found |
| `unmatched_processors` | Names of the processors not found |
| `family_status` | `PASS` / `FAIL` / `MISSING` / `DUPLICATED` |
| `family_expected` | Families derived from the Intel/AMD reference data |
| `family_actual` | Families written in the Fujitsu CSV |
| `family_reason` | Explanation of the result when it is not PASS |
| `line_status` / `line_expected` / `line_actual` / `line_reason` | The same, for Processor Line |
| `duplicate_status` | `UNIQUE` / `DUPLICATED` |
| `duplicate_reason` | Why the row is a duplicate |
| `overall_status` | Combined result for the row |

### 7.3 Mismatch Details columns

The details report has one line per problem row. When a cell holds several values, they are combined in that cell with `;`.

`row_number, file_name, processor, family_status, family_expected, family_actual, family_reason, line_status, line_expected, line_actual, line_missing_in_fujitsu, line_extra_in_fujitsu, line_reason, duplicate_status, duplicate_reason, overall_status`

### 7.4 CSV vs Excel

The `.csv` files contain the same data as the Excel files. The CSV format cannot store colours or a frozen header row, so use the **`.xlsx` files** for review.

---

## 8. Excel Colour Legend

| Element | Colour |
|---|---|
| Header row | Soft blue background, bold navy text, **frozen**, with filter drop-downs |
| `PASS` / `UNIQUE` | Light green, dark green text |
| `FAIL` | Light red, dark red text |
| `MISSING` | Light orange, dark orange text |
| `DUPLICATED` | Light amber, dark amber text |
| Missing values | Light peach |
| Extra values / unmatched processors | Light lavender |
| Reason columns | Light cream |
| Expected values (reference) | Light blue |
| Actual values (Fujitsu) | Light grey |
| Other cells | Alternating white / very light grey rows, thin grey-blue borders |

Every sheet is also set to print in landscape, with the header row repeated at the top of each page.

---

## 9. Script Walkthrough

The script is commented throughout. The table below maps each section to its line numbers in `validate_processor_line.py`.

| Lines | Section | What it does |
|---|---|---|
| 1–26 | **Docstring** | Describes the purpose, the output files and how to run the script |
| 29–39 | **1. Imports** | `ast` (reads list text safely), `re` (text cleaning), `pathlib` (file paths), `pandas` (reads/writes CSV), `openpyxl` (creates formatted Excel) |
| 43–64 | **2. Configuration** | Input and output file paths. Edit this section when file names change. `STATUS_PRIORITY` sets the overall-status order. `SYMBOLS` defines which characters are ignored |
| 68–158 | **3. Helper functions** | The reusable building blocks (see below) |
| 162–277 | **4. Main validation** | The row-by-row validation loop, Steps A–H (see below) |
| 281–408 | **5. Excel formatting** | Colours, borders, column widths, frozen header, filters and the Summary sheet |
| 412–415 | **6. Entry point** | Runs `main()` when the file is executed |

### 9.1 Helper functions

| Function | Line | Purpose |
|---|---|---|
| `norm(s)` | 70 | Cleans text: removes ™/®/©, collapses spaces, converts to lower case |
| `proc_key(s)` | 77 | `norm()` plus ignoring the word "processor". Used as the processor matching key |
| `parse_list(v)` | 87 | Converts a cell like `"['EPYC', 'Ryzen']"` into a Python list. Fixes curly quotes first |
| `load_reference()` | 103 | Reads the Intel and AMD CSVs into a lookup table: processor key → (source, name, family, line) |
| `find_dups(values)` | 119 | Returns any values that appear more than once |
| `compare(expected, actual)` | 130 | Compares expected and actual values. Returns PASS / FAIL / MISSING / DUPLICATED, missing values, extra values and reason |

### 9.2 Main validation steps (inside `main()`)

| Step | Line | What happens |
|---|---|---|
| Load | 164 | Loads the reference lookup and the Fujitsu CSV (all values read as text) |
| Loop | 171 | Visits **every** Fujitsu row and parses its processor, family and line lists |
| **A** | 177 | Looks up each processor. Builds the expected family/line sets and records unmatched processors |
| **B** | 191 | Compares family and line **separately**, so a failure in one never stops the other |
| **C** | 199 | Duplicate checks: whole row identical to an earlier row, or a processor listed twice |
| **D** | 211 | Works out the overall status (FAIL > MISSING > DUPLICATED > PASS) |
| **E** | 215 | Adds a line to the full report |
| **F** | 237 | Adds a line to the details report if the row is not PASS |
| **G** | 258 | Saves the CSV files (UTF-8 with BOM so ™/® display correctly in Excel) and calls `write_xlsx()` |
| **H** | 269 | Prints the summary in the terminal |

### 9.3 Excel functions

| Function | Line | Purpose |
|---|---|---|
| `_sheet(wb, title, df)` | 312 | Writes one table to a worksheet and applies the header style, column widths, status colours, light tints, zebra rows, frozen header, filters and print settings |
| `write_xlsx(out, det, total_rows)` | 362 | Builds the Summary sheet and saves both Excel files |

---

## 10. Updating for New Data Files

When a new dated export arrives, edit **Section 2 – Configuration** at the top of the script:

```python
FUJITSU = HERE / "31032026_fujitsu_db_import.csv"      # <- new Fujitsu file name
REFS = [
    ("Intel", HERE / "16062026_intel_db_import.csv"),  # <- new Intel file name
    ("AMD", HERE / "02062026_amd_db_import.csv"),      # <- new AMD file name
]
```

Required columns:

| File | Required columns |
|---|---|
| Fujitsu | `processor`, `processor_family`, `processor_line` (`file_name` is optional) |
| Intel / AMD | `processor`, `processor_family`, `processor_line` |

---

## 11. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `PermissionError` when saving | An output `.xlsx` or `.csv` is open in Excel | Close the file and run again |
| `'python' is not recognized` | Python is not on PATH | Use `py` instead of `python`, or reinstall Python with "Add to PATH" ticked |
| `ModuleNotFoundError: pandas` / `openpyxl` | Library not installed | `pip install pandas openpyxl` |
| `FileNotFoundError` | Input file name changed | Update the file names in Section 2 |
| `KeyError: 'processor'` | A required column was renamed or deleted | Restore the column name in the CSV |
| Running `& '...\validate_processor_line.py'` does nothing or opens an editor | PowerShell opens `.py` files with the default program | Run with `python "<path>"` |
| ™/® show as `�` in the terminal | Windows console display only | No action needed. The files contain the correct symbols |
| Many rows marked `DUPLICATED` | Columns that made rows unique (e.g. `server_specification`) were removed from the Fujitsu CSV | Use the original, unedited export |

---

## 12. Known Limitations

- **The check is per row, not per processor.** The Fujitsu file stores each row's family and line as one combined list, so the script cannot say which family belongs to which processor.
- **Unmatched processors are reported but not validated.** Series-level entries (e.g. `AMD EPYC 9004 series`, `Intel Xeon E-2400`) and models missing from the reference files (e.g. `Intel Xeon Platinum 8280M`) are listed in `unmatched_processors`. A family or line that only those processors would supply cannot be checked.
- **Re-saving the CSV in Excel can change the data.** Excel may convert quotes or alter encoding. Use the original export where possible.
