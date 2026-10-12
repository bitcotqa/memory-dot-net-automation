# Hpe QuickSpecs Processor Extractor

Extracts processor model codes and processor description lines from HPE
QuickSpecs PDFs using position-aware text extraction (PyMuPDF).

## Setup

```
pip install -r requirements.txt
```

Place the source QuickSpecs PDFs under `19062026_hpe_pdfs/pdf/` inside this
folder (not included in the repo — ~450MB of vendor PDFs, download them
separately) and run:

```
python extract_processors.py
```

## Output

- `output/hpe_processor_extraction_result.csv` — file_name, processor,
  processor_description (the extracted dataset)
- `output/hpe_processor_missing_issues_report.csv` — QA report per file,
  flagging anything missing so a human can review the flagged subset instead
  of re-reading every PDF

The script writes incrementally (flushes after every file), so an
interruption never loses completed work.

## Input

- `input/hpe_quickspec_host_processors.csv` — reference dataset of known
  processor codes/descriptions per QuickSpecs file, used for QA comparison.
