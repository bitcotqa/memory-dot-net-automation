# HPE QuickSpecs Processor Section Validator

Validates the `processor` and `processor_description` values in
`Hpe/input/hpe_quickspec_host_processors.csv` against the actual HPE
QuickSpecs PDFs they claim to come from — checking not just whether each
value appears somewhere in the PDF, but whether it appears in the *right*
part of it (Overview, Standard Features, or Technical Specifications), not
in an unrelated section like Configuration Information or Summary of
Changes.

## How this differs from the other `Hpe/` tools

- **`Hpe/extract_processors.py`** *produces* the reference CSV by extracting
  processor data from the PDFs in the first place.
- **`Hpe/spec_verifier/`** verifies HPE specs fetched live from **URLs**
  (HTML pages), independent of the local PDF set.
- **This tool** takes the CSV `extract_processors.py` already produced and
  cross-checks every individual value against the **local PDF files**
  directly, page by page, section by section — a QA pass on the reference
  data itself.

## Setup

```
pip install -r requirements.txt
```

Place the source QuickSpecs PDFs in a `pdf/` folder next to this script
(not included in this repo — see `Hpe/README.md` for where to get them), or
point at a folder elsewhere via an environment variable:

```
set HPE_PDF_DIR=C:\path\to\your\quickspecs\pdfs
```

## Run

```
python validate_processor_sections.py
```

Optionally pass a number to only process the first N CSV rows (smoke-test):

```
python validate_processor_sections.py 20
```

## Output

Written to `output/`:

- **`host_processors_comparison_detail.csv`** — one row per CSV row, with
  the raw `processor` / `processor_description` values alongside per-value
  breakdowns:
  - `processor_matched_values` / `processor_description_matched_values` —
    values confirmed inside Overview / Standard Features / Technical
    Specifications
  - `processor_not_matched_values` / `processor_description_not_matched_values`
    — values found in the PDF, but only in a different section (the comment
    says which page/section)
  - `processor_missing_values` / `processor_description_missing_values` —
    values not found anywhere in the PDF at all
- **`host_processors_comparison_summary.csv`** — the same per-row counts
  without the full matched/not-matched value lists, for a quick scan.

## How matching works

- **Section detection**: QuickSpecs PDFs repeat a running header on every
  page (`QuickSpecs / <Product> / <Section> / Page N`), so each page is
  tagged with its own section from that header. A handful of real-world
  variants are also recognized as still meaning "Standard Features" or
  "Technical Specifications" — a qualified name like `"Standard Features
  (Server Blade)"`, or a Carrier Grade/NEBS supplement's `"Recommended
  Support Services for <product>"` page that stands in for Standard
  Features when a document has no such page at all. Older/nonstandard PDFs
  with no such header fall back to a heading/phrase-based heuristic instead
  of being skipped.
- **Processor codes** (e.g. `E5-2620v3`) are matched with a regex tolerant
  of the ways PDFs typically re-render them: hyphen vs. space vs. nothing,
  a trademark glyph (®/™/©) glued onto the brand name, the SKU glued
  directly onto the following word "Processor", and two SKUs of the same
  generation stated as one combined `"E7-4800/8800 v2"`-style reference.
- **Processor descriptions** are full spec strings (e.g.
  `"E5-2687Wv4 3.0GHz 12 30MB 160W 9.6GT/s 2400"`); since PDF tables usually
  render each field of a row as its own line/cell, both sides are
  whitespace-normalized and matched by exact substring, falling back to a
  token-overlap check (all significant values found within a small window)
  to tolerate line-wrap and cell-order differences.
- **Changelog notes**: a value that's really a "Summary of Changes"
  sentence (e.g. `"AMD Opteron 6128 processor was added"`) is recognized as
  such and counted as validated, rather than flagged as a spec mismatch.
