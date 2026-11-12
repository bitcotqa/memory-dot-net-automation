# Fujitsu DIMM Rank Validation — `productid.py`

`productid.py` is a QA script for the Fujitsu product CSV. It checks that the **Rank**, **Rank Width** and **DIMM Rank** values of every memory part agree with each other and with the server they belong to. The result is a colour-coded Excel report (`.xlsx`) with a status and a failure reason for every row.

It also keeps the older **memory SKU generation** check, which you can run with a command-line switch.

---

## Table of Contents

1. [What the Script Checks](#1-what-the-script-checks)
2. [Prerequisites](#2-prerequisites)
3. [How to Run](#3-how-to-run)
4. [Input File and Columns Used](#4-input-file-and-columns-used)
5. [Status Definitions](#5-status-definitions)
6. [Output Report](#6-output-report)
7. [Line-by-Line Code Explanation](#7-line-by-line-code-explanation)
8. [Changing the Input File or Rules](#8-changing-the-input-file-or-rules)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. What the Script Checks

### Row level (each memory part)

| Value | Source column | Example |
|---|---|---|
| Expected DIMM rank | the rank written in `part_description` | `... DIMM, 2Rx4` → `2Rx4` |
| CSV Rank + Rank Width | `ranks` + `rank_width` | `2` + `4` → `2Rx4` |
| CSV DIMM Rank | `dimm_ranks` | `2Rx4` |
| Server DIMM rank list | `server_dimm_ranks` | `['1Rx4', '1Rx8', '2Rx4', '2Rx8']` |

A row passes when all three rank values are the same **and** the rank appears in the server's DIMM rank list.

### Server level (grouped by `server_description`)

1. Rows are grouped by **Server Description**.
2. The server's **expected ranks** are all the distinct DIMM ranks used by its memory parts. Duplicate parts are not counted.
3. Every row of that server must have a `server_dimm_ranks` list that contains **all** the expected ranks, with **no extra** and **no repeated** values.

---

## 2. Prerequisites

| Requirement | Version used |
|---|---|
| Python | 3.9 or later |
| pandas | any recent version |
| openpyxl | 3.1+ |

Install the libraries:

```bash
pip install pandas openpyxl
```

---

## 3. How to Run

Open a terminal in the project folder (`C:\Automation\fujistu\processor line`).

| Command | What it does |
|---|---|
| `python productid.py` | Runs the DIMM rank validation and writes `dimm_rank_validation_report.xlsx` |
| `python productid.py --sku` | Runs the older memory SKU check and writes `processed_memory_sku_with_status.csv` |

Example console output:

```
DIMM rank report saved to dimm_rank_validation_report.xlsx
                          Validation  Total  Matched  Unmatched  Duplicate Pass Rate
Memory Part Rows (Rank vs DIMM Rank)    120      113          0          7     94.2%
    Servers (DIMM Rank completeness)     43       43          0          0    100.0%
```

> **Close the report in Excel before running.** Excel locks the file while it is open, so the script can't overwrite it and fails with `PermissionError`.

---

## 4. Input File and Columns Used

Input file: `31032026_fujitsu_db_import.csv`. Change it with `INPUT_FILE` on line 28.

| Column | Used for |
|---|---|
| `server_description` | Grouping rows by server; shown in the report |
| `host_url` | Shown in the report (clickable link) |
| `part_url` | Shown in the report (clickable link) |
| `part_description` | Source of the expected DIMM rank (`1Rx8`, `2Rx4`, …); duplicate detection |
| `category` | Selecting memory rows (`memory`) |
| `ranks` | CSV Rank |
| `rank_width` | CSV Rank Width |
| `dimm_ranks` | CSV DIMM Rank |
| `server_dimm_ranks` | List of DIMM ranks the server supports |

If any of these columns is missing, the script stops and prints the missing column names.

---

## 5. Status Definitions

| Status | Row level meaning | Server level meaning |
|---|---|---|
| **Matched** | Rank + Rank Width, DIMM Rank and Part Description agree, and the rank is in the server list | Server list contains exactly the expected ranks |
| **Unmatched** | The values disagree, or the rank is not in the server list | Server list has missing **and** extra ranks, differs between rows, or lists ranks with no memory part |
| **Missing** → shown as **Matched** | Rank, Rank Width or DIMM Rank is blank | Server list is empty, or expected ranks are absent |
| **Duplicate** | Same Server Description + Part Description as an earlier row | A rank appears more than once in the server list |

> **Important:** The script still detects *Missing* cases, but the report shows them as **Matched** (lines 426–428). The **Failure Reason** column still explains what is missing, and the **Row Failures** tab still lists these rows.

---

## 6. Output Report

File: `dimm_rank_validation_report.xlsx`

| Tab | Contents |
|---|---|
| **Summary** | Count of each status, total and pass rate for rows and for servers, plus a colour legend |
| **Server DIMM Ranks** | One row per server: expected ranks, ranks listed in the CSV, missing and extra ranks, rows checked and failed, status, failure reason |
| **Memory Rank Validation** | One row per memory part: CSV row number, rank values, server list, status, failure reason |
| **Row Failures** | Memory part rows that are not Matched **or** that have a failure reason |

### Formatting

- Light-blue bold header row, **frozen** so it stays visible while scrolling.
- Filter drop-downs on every header.
- Rows are tinted by status: **green** = Matched, **red** = Unmatched, **blue** = Duplicate.
- The Status cell is bold and coloured.
- Host URL and Part URL are clickable links.
- Long text (Failure Reason, Part Description) wraps inside the cell.

---

## 7. Line-by-Line Code Explanation

### 7.1 Module header (lines 1–16)

| Line(s) | Code | Why it is there |
|---|---|---|
| 1–16 | `""" ... """` | Docstring describing both checks, the statuses, the output file and the `--sku` switch. Anyone opening the file sees what it does. |

### 7.2 Imports (lines 18–26)

| Line | Code | Why it is there |
|---|---|---|
| 18 | `import ast` | Safely turns the text `"['1Rx4', '2Rx4']"` from `server_dimm_ranks` into a real Python list (`ast.literal_eval`). Unlike `eval`, it can't run code. |
| 19 | `import re` | Regular expressions, used to find rank patterns such as `2Rx4` inside text. |
| 20 | `import sys` | Reads command-line arguments (`--sku`) and stops the script with a message (`sys.exit`). |
| 21 | `from collections import Counter` | Counts how often each rank appears in a server list, to detect repeated values. |
| 23 | `import pandas as pd` | Reads the CSV and holds the data as tables (DataFrames). |
| 24 | `from openpyxl.formatting.rule import FormulaRule` | Creates Excel conditional formatting that colours a row based on its Status. |
| 25 | `from openpyxl.styles import ...` | Excel styles: `Alignment` (wrap and centre), `Border`, `Font`, `PatternFill` (background colour), `Side` (border line). |
| 26 | `from openpyxl.utils import get_column_letter` | Converts a column number to an Excel letter (1 → `A`, 13 → `M`). |

### 7.3 Settings and constants (lines 28–34)

| Line | Code | Why it is there |
|---|---|---|
| 28 | `INPUT_FILE = "31032026_fujitsu_db_import.csv"` | The CSV to validate. Change this when a new data file arrives. |
| 29 | `RANK_REPORT_FILE = "dimm_rank_validation_report.xlsx"` | Name of the Excel report. |
| 30 | `SKU_OUTPUT_FILE = "processed_memory_sku_with_status.csv"` | Output of the older SKU check. |
| 32 | `RANK_RE = re.compile(r"\b(\d+)\s*R\s*x\s*(\d+)\b", re.IGNORECASE)` | Pattern for a DIMM rank: a number, `R`, `x`, a number. It allows spaces and any letter case, so `2Rx4`, `2rx4` and `2 R x 4` all match. The two `( )` groups capture the rank (`2`) and the width (`4`). |
| 34 | `MATCHED, UNMATCHED, MISSING, DUPLICATE = ...` | Status names defined once, so a typo can't create a new, unexpected status. |

### 7.4 Helper functions (lines 40–101)

#### `read_csv` (lines 40–41)

| Line | Why |
|---|---|
| 41 | Reads the CSV with `dtype=str` so every value stays text. Otherwise pandas would turn `4` into `4.0`, or break URLs and ranks. `low_memory=False` avoids mixed-type warnings on large files. |

#### `clean` (lines 44–48)

| Line | Why |
|---|---|
| 46 | Empty cells in pandas are `NaN`, not `""`. This line treats `None` and `NaN` as blank. |
| 47 | Returns `""` for blanks, so later checks can use a simple `if not value`. |
| 48 | Converts the value to text and removes surrounding spaces, so `" 2Rx4 "` equals `"2Rx4"`. |

#### `normalize_rank` (lines 51–54)

| Line | Why |
|---|---|
| 53 | Searches the text for a rank pattern using `RANK_RE`. |
| 54 | Rebuilds it in one standard format, `<rank>Rx<width>`. `int()` removes leading zeros. Returns `""` if no rank is found. This makes `2rx4` and `2Rx4` compare as equal. |

#### `rank_from_columns` (lines 57–65)

| Line | Why |
|---|---|
| 59 | Cleans the `ranks` and `rank_width` values. |
| 60–61 | If either is blank, there's nothing to combine, so it returns `""`. The row is later reported as missing. |
| 62–63 | Combines them into `2Rx4`. `int(float(...))` also handles values such as `"2.0"`. |
| 64–65 | If a value isn't a number, it combines the raw text anyway, so the mismatch shows up in the report instead of crashing the script. |

#### `rank_from_description` (lines 68–74)

| Line | Why |
|---|---|
| 70 | Finds **all** rank patterns in `part_description`. |
| 71–72 | No rank in the description returns `""`. |
| 73–74 | Takes the **last** match, because the rank is written at the end of the description (`..., DIMM, 2Rx4`). Returns it in standard format. This is the **expected** value that the CSV columns are checked against. |

#### `parse_rank_list` (lines 77–87)

| Line | Why |
|---|---|
| 79–81 | Blank `server_dimm_ranks` returns an empty list. |
| 83 | `ast.literal_eval` turns the text `"['1Rx4', '2Rx4']"` into a real list. |
| 84 | If the value was a single item instead of a list, it is wrapped in a list. |
| 85–86 | If the text isn't valid Python list syntax (for example `1Rx4, 2Rx4`), it is split on `,`, `;` or `|` instead. |
| 87 | Normalises every item and drops empty ones. **Duplicates are kept on purpose**, so repeated ranks can be reported. |

#### `rank_sort_key` (lines 90–92)

| Line | Why |
|---|---|
| 91–92 | Sorts ranks by number instead of alphabetically, so `2Rx4` comes before `10Rx4`. Unrecognised text goes last (`999, 999`). |

#### `fmt_list` (lines 95–96)

| Line | Why |
|---|---|
| 96 | Turns a set or list of ranks into a sorted, readable string for the report, for example `1Rx4, 1Rx8, 2Rx4`. |

#### `join_unique` (lines 99–101)

| Line | Why |
|---|---|
| 100 | Collects the non-blank values in a column. |
| 101 | Removes duplicates while keeping their order (`dict.fromkeys`) and joins them with ` | `. Used to show all Host and Part URLs of a server in one cell. |

### 7.5 Row-level validation — `validate_rows` (lines 107–200)

| Line(s) | Why |
|---|---|
| 108 | Marks rows whose `category` is `memory` (ignores case and spaces). |
| 109–111 | Also marks any row that has rank data, even if the category is wrong, so no rank value is skipped. |
| 112 | Keeps only those rows. `.copy()` avoids changing the original table. |
| 114–118 | Builds a duplicate key from **server description + part description** (lowercase, trimmed). The same part listed twice for the same server is a duplicate. |
| 119 | `duplicated(keep="first")` marks the second and later copies. The first copy is treated as the original. |
| 120–122 | Remembers the row index of each key's first copy, so the report can say *"Duplicate of CSV row 357"*. |
| 124 | List that collects one result per row. |
| 125 | Loops over each memory row. `idx` is the row's position in the CSV. |
| 126 | **Expected** rank from the Part Description. |
| 127–128 | Raw CSV `ranks` and `rank_width`. |
| 129 | Combined CSV rank, for example `2Rx4`. |
| 130–131 | Raw `dimm_ranks` value (shown in the report) and its normalised form (used for comparing). |
| 132 | The server's DIMM rank list for this row. |
| 134–135 | Starts with no reasons and status **Matched**. Each failed check adds a reason. |
| 137–142 | **Duplicate check, done first.** A duplicate row is reported as Duplicate with the CSV row number of the original, and isn't checked again. |
| 144–152 | Collects which values are blank: Rank, Rank Width, DIMM Rank, or a rank in the Part Description. |
| 154–156 | If anything is blank, the status is **Missing** and the blank fields are listed. |
| 157–161 | If nothing is blank, compares **Rank + Width** with **DIMM Rank**. |
| 162–165 | Compares **DIMM Rank** with the **Part Description** rank. |
| 166–169 | Otherwise compares **Rank + Width** with the Part Description. `elif` avoids reporting the same problem twice. |
| 170–171 | Any mismatch reason means **Unmatched**. |
| 173–181 | **Server list check:** the part's rank must appear in `server_dimm_ranks`. If it doesn't, a reason is added. A row that was Matched becomes Unmatched; Missing stays Missing. |
| 183–199 | Saves the result row. Only the columns needed for QA are kept. `idx + 2` turns the index into the line number you see when opening the CSV (one line for the header, plus Excel counting from 1). |
| 197 | All reasons are joined with `; `, so one cell can list several problems. |
| 200 | Returns the results as a table. |

### 7.6 Server-level validation — `validate_servers` (lines 206–292)

| Line(s) | Why |
|---|---|
| 208 | Ignores duplicate parts, so they don't affect what the server should list. |
| 209–213 | For each server, collects the set of ranks its memory parts use. This is the **expected** server list. The Part Description rank is used first, and the DIMM Rank column if the description has none. |
| 216 | Adds a cleaned copy of the server name, so names that differ only by spaces are grouped together. |
| 217 | Loops over each server and all of its rows (memory, SSD, HDD, …). |
| 218 | Expected ranks for this server, or an empty set if it has no memory parts. |
| 219 | Number of memory parts, shown in the report. |
| 221–223 | Counters for failed rows, plus every missing, extra and repeated rank found on any row. |
| 224–225 | Reads each row's `server_dimm_ranks` list. |
| 226 | Stores the list as text, to detect rows of the same server that have **different** lists. |
| 227–228 | Ranks that appear more than once in the list. |
| 229 | Expected ranks that are **not** in the list (**missing**). |
| 230 | Ranks in the list with **no** memory part behind them (**extra**). |
| 231–232 | A row fails if it has missing, extra or repeated ranks, or an empty list. |
| 233–235 | Adds this row's findings to the server totals. |
| 237 | The distinct lists found for this server (normally one). |
| 238 | `True` if every row of the server has an empty list. |
| 241–243 | No memory parts and an empty list: nothing to compare, so the status is **Missing**. |
| 244–248 | Memory parts exist but the list is empty: **Missing**, with the expected ranks in the reason. |
| 250–251 | Reason: expected ranks that are missing from the list. |
| 252–255 | Reason: listed ranks with no memory part. |
| 256–257 | Reason: repeated ranks. |
| 258–259 | Reason: rows of the same server have different lists. |
| 260–261 | Reason: the server has a list but no memory parts to check it against. |
| 263–264 | Only repeated values: **Duplicate**. |
| 265–266 | Only some expected ranks absent, with consistent rows: **Missing**. |
| 267–268 | Any other problem: **Unmatched**. |
| 269–270 | No problems: **Matched**. |
| 272–291 | Saves one result row per server: URLs, expected ranks and count, actual list(s) and count, missing and extra ranks, rows checked and failed, status, reason. |
| 281–283 | **Actual Count** is the number of distinct ranks across all rows' lists. |
| 292 | Returns the server results table. |

### 7.7 Excel styles (lines 298–308)

| Line(s) | Why |
|---|---|
| 298–302 | Colour for each status as (light background, dark text): Matched green, Unmatched red, Duplicate blue. There's no Missing colour, because Missing is shown as Matched. |
| 303 | Light-blue header background. |
| 304 | Bold dark-blue header text. |
| 305–306 | Thin grey border around every cell. |
| 307–308 | Fixed widths for long columns, so URLs and reasons are readable. |

### 7.8 `style_sheet` — format one tab (lines 311–355)

| Line(s) | Why |
|---|---|
| 312 | **Freezes the first row**, so headers stay visible while scrolling. |
| 313 | Adds filter drop-downs to the header row. |
| 314 | Makes the header row taller. |
| 315–319 | Applies the header fill, font, border and centred, wrapped text. |
| 321–328 | Sets each column width: a fixed width for long columns, otherwise sized to the content (12 to 30 characters). Only the first 500 rows are measured, to keep it fast. |
| 330 | Columns whose text should wrap. |
| 331–335 | Adds borders and top alignment to every data cell, and wraps long text. |
| 336–339 | Makes single URLs in Host URL and Part URL clickable, blue and underlined. Cells that hold several URLs joined with ` | ` stay plain text. |
| 341–344 | Finds the Status column letter and the whole data range. |
| 345–350 | Adds conditional formatting: if the Status cell equals a status, the **whole row** gets that status's light colour. This is a rule, so it updates if someone edits the status in Excel. |
| 351–355 | Makes each Status cell bold, coloured and centred. |

### 7.9 `build_summary` (lines 358–372)

| Line(s) | Why |
|---|---|
| 360–361 | Builds one summary row for memory parts and one for servers. |
| 362–363 | Counts each status and the total. |
| 364–371 | Stores the counts per status and the **pass rate** (Matched ÷ Total). Shows `N/A` if there are no rows, to avoid dividing by zero. |

### 7.10 `write_report` — create the Excel file (lines 375–412)

| Line(s) | Why |
|---|---|
| 376 | Builds the Summary table. |
| 378 | **Row Failures** keeps rows that aren't Matched **or** that still have a reason. Rows that were Missing (now shown as Matched) stay visible here. |
| 379–384 | The four tabs and their data, in display order. |
| 385 | Opens the Excel file for writing with openpyxl. |
| 386–388 | Writes each tab (without the pandas index column) and formats it. |
| 391–396 | On the Summary tab, colours the Matched, Unmatched and Duplicate header cells in their status colours. |
| 397–398 | Adds a **Status Legend** title two rows below the summary table. |
| 399–403 | Text explaining each status. |
| 404–410 | Writes each legend row: a coloured status cell plus its meaning. |
| 411–412 | Widens columns A and B so the legend is readable. |

### 7.11 `run_dimm_rank_validation` — main flow (lines 415–432)

| Line(s) | Why |
|---|---|
| 416 | Reads the input CSV. |
| 417–418 | Columns the check needs. |
| 419–421 | If any are missing, stops with a clear message instead of an error later on. |
| 423 | Runs the row-level checks. |
| 424 | Runs the server-level checks (they use the row results). |
| 426–428 | **Changes "Missing" to "Matched"** in both result tables. The Failure Reason is **not** changed, so the cause of the problem is still shown. |
| 429 | Writes the Excel report. |
| 431–432 | Prints the file name and the summary table to the console. |

### 7.12 Legacy memory SKU check (lines 438–467)

#### `generate_expected_sku` (lines 438–449)

Builds the SKU the part *should* have from its columns, for example `32D5ER24560011S1`:

| Line | Part of SKU | Rule |
|---|---|---|
| 439 | Capacity | `32GB` → `32` |
| 440 | Memory type | First and last character for DDR (`DDR5` → `D5`), otherwise `D4` |
| 441 | ECC | `E` if the value contains `ECC`, otherwise `N` |
| 442 | DIMM type | First letter (`RDIMM` → `R`) |
| 443 | Ranks | e.g. `2` |
| 444 | Rank width | e.g. `4` |
| 445 | Speed | e.g. `5600` |
| 446 | Voltage | `1.1V` → `11` (removes `V` and `.`) |
| 447 | Height | First letter |
| 448 | Quantity | e.g. `1` |
| 449 | — | Joins all parts into the SKU string |

#### `process_and_save_csv` (lines 452–467)

| Line(s) | Why |
|---|---|
| 453 | Reads the input CSV. |
| 454 | Lists for the generated SKUs and their statuses. |
| 456–459 | If the row has no `memory_SKU`, it's marked **Missing** with no generated SKU. |
| 460–463 | Otherwise it generates the expected SKU and compares it: **Match** or **Unmatch**. |
| 464–466 | Adds the two new columns and saves everything to `processed_memory_sku_with_status.csv`. |
| 467 | Prints where the file was saved. |

### 7.13 Entry point (lines 470–474)

| Line | Why |
|---|---|
| 470 | Runs the code below only when the file is executed directly (`python productid.py`), not when another script imports it. |
| 471–472 | With `--sku`, runs the older SKU check. |
| 473–474 | Otherwise runs the DIMM rank validation (the default). |

---

## 8. Changing the Input File or Rules

| Need | Change |
|---|---|
| New data file | Set `INPUT_FILE` (line 28) |
| Different report name | Set `RANK_REPORT_FILE` (line 29) |
| Show Missing as Missing again | Delete lines 426–428 and add `MISSING: ("FFEB9C", "9C5700"),` back to `STATUS_STYLE` (line 298) |
| Different colours | Edit the hex colours in `STATUS_STYLE` (line 298) or `HEADER_FILL` (line 303) |
| Treat duplicates differently | Change the duplicate key on lines 114–118 (for example, add `host_url`) |

---

## 9. Troubleshooting

| Problem | Cause / fix |
|---|---|
| `PermissionError: ... dimm_rank_validation_report.xlsx` | The report is open in Excel. Close it and run again. |
| `Input file is missing column(s): ...` | The CSV doesn't have the required columns. Check the file and its headers. |
| `ModuleNotFoundError: No module named 'openpyxl'` | Run `pip install pandas openpyxl`. |
| A server shows `Matched` but has a Failure Reason | Expected behaviour: it was *Missing* and is shown as Matched. Read the Failure Reason. |
| Report looks like an old version | Close and reopen the file in Excel after running the script. |
