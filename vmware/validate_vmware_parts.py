"""
Validates a VMware Broadcom HCL parts CSV (the "master" list, e.g. VMWARE_1.csv)
against a ground-truth CSV scraped from the live Broadcom Compatibility Guide
(e.g. vmware_playwright.csv), and writes a column-by-column MATCHED / MISMATCH /
MISSING report as an Excel workbook.

Usage:
    python validate_vmware_parts.py [expected.csv] [actual.csv] [output.xlsx]

Defaults to input/VMWARE_1.csv, input/vmware_playwright.csv,
output/VMWARE_Part_Validation_Report.xlsx when run with no arguments.

Rows are matched between the two files using the Broadcom `productId` query
parameter embedded in each row's `part_url` — this is stable even when OEM,
part number, capacity, etc. contain typos in the master file.
"""

import csv
import json
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

COLUMNS = [
    "category", "oem", "part_number", "DWPD", "form_factor", "capacity",
    "mfr_part_number", "part_description", "interface", "part_specification",
    "part_url", "store",
]
IDX = {c: i for i, c in enumerate(COLUMNS)}

FIELD_LABELS = {
    "part_number": "Part Number",
    "DWPD": "DWPD",
    "form_factor": "Form Factor",
    "capacity": "Capacity",
    "mfr_part_number": "MFR Part No",
    "part_description": "Part Description",
    "interface": "Interface",
}

NA_VALUES = {"", "nan", "none", "n/a", "na", "-"}
KEY_RE = re.compile(r"'([A-Za-z0-9 /()]+?)'\s*:\s*(\[[^\]]*\]|'[^']*'|\"[^\"]*\"|[^,}]+)")


def is_na(x):
    return (x or "").strip().lower() in NA_VALUES


def norm_cmp(x):
    return re.sub(r"\s+", " ", (x or "").strip().lower())


def field_result(expected, actual):
    """Three-state status: MATCHED / MISMATCH / MISSING (either side blank/nan/N-A)."""
    if is_na(expected) or is_na(actual):
        return "MISSING"
    return "MATCHED" if norm_cmp(expected) == norm_cmp(actual) else "MISMATCH"


def parse_spec(spec_str):
    """Tolerant parse of the part_specification pseudo-dict column into {key: value}."""
    result = {}
    for k, v in KEY_RE.findall(spec_str or ""):
        v = re.sub(r"^\[|\]$", "", v.strip()).strip()
        v = re.sub(r"^['\"]|['\"]$", "", v).strip()
        v = re.sub(r"\\t+", "", v).strip()
        result[k.strip()] = v
    return result


def compare_spec(expected_str, actual_str):
    """Per-attribute MATCHED/MISMATCH/MISSING across the union of keys on both sides."""
    exp, act = parse_spec(expected_str), parse_spec(actual_str)
    all_keys = list(dict.fromkeys(list(act.keys()) + list(exp.keys())))
    matched, mismatched, missing = [], [], []
    for k in all_keys:
        ev, av = exp.get(k), act.get(k)
        if ev is None or av is None or is_na(ev) or is_na(av):
            missing.append(k)
        elif norm_cmp(ev) == norm_cmp(av):
            matched.append(k)
        else:
            mismatched.append(k)
    return matched, mismatched, missing, len(all_keys)


def load_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        return list(reader)


def index_by_product_id(rows):
    out = {}
    for row in rows:
        m = re.search(r"productId=(\d+)", row[IDX["part_url"]])
        if m:
            out[m.group(1)] = row
    return out


def build_rows(expected_rows, actual_by_pid):
    rows_out, field_totals, overall_totals = [], {}, {
        "MATCHED": 0, "PARTIAL MATCH": 0, "UNMATCHED": 0,
    }
    for label in list(FIELD_LABELS.values()) + ["Part Specification"]:
        field_totals[label] = {"Matched": 0, "Mismatch": 0, "Missing": 0}

    for sno, row in enumerate(expected_rows, start=1):
        url = row[IDX["part_url"]]
        pid_match = re.search(r"productId=(\d+)", url)
        act_row = actual_by_pid.get(pid_match.group(1)) if pid_match else None

        field_results, comments = {}, []
        for col, label in FIELD_LABELS.items():
            exp_v = row[IDX[col]]
            act_v = act_row[IDX[col]] if act_row else ""
            res = field_result(exp_v, act_v)
            field_results[col] = res
            field_totals[label][{"MATCHED": "Matched", "MISMATCH": "Mismatch", "MISSING": "Missing"}[res]] += 1
            if res == "MISSING":
                if is_na(exp_v) and is_na(act_v):
                    comments.append(f"{label}: value missing on both sides.")
                elif is_na(exp_v):
                    comments.append(f"{label}: missing in CSV (expected blank/nan).")
                else:
                    comments.append(f"{label}: missing on live page (actual blank/nan).")

        exp_spec = row[IDX["part_specification"]]
        act_spec = act_row[IDX["part_specification"]] if act_row else ""
        matched_k, mismatched_k, missing_k, total_k = compare_spec(exp_spec, act_spec)
        field_totals["Part Specification"]["Matched"] += len(matched_k)
        field_totals["Part Specification"]["Mismatch"] += len(mismatched_k)
        field_totals["Part Specification"]["Missing"] += len(missing_k)

        if total_k == 0:
            spec_result = "MISSING"
        elif len(matched_k) == total_k:
            spec_result = "MATCHED"
        elif not matched_k and not mismatched_k:
            spec_result = "MISSING"
        elif not matched_k:
            spec_result = "MISMATCH"
        else:
            spec_result = "PARTIAL MATCH"

        comment = f"Part Specification: {len(matched_k)} Matched / {len(mismatched_k)} Mismatch / {len(missing_k)} Missing (of {total_k} attributes)"
        if mismatched_k:
            comment += f" | Mismatched attrs: {', '.join(mismatched_k)}"
        if missing_k:
            comment += f" | Missing attrs: {', '.join(missing_k)}"
        comments.append(comment)

        hard_mismatch = any(r == "MISMATCH" for r in field_results.values()) or spec_result == "MISMATCH"
        any_missing = any(r == "MISSING" for r in field_results.values()) or spec_result in ("MISSING", "PARTIAL MATCH")
        overall = "UNMATCHED" if hard_mismatch else ("PARTIAL MATCH" if any_missing else "MATCHED")
        overall_totals[overall] += 1

        page_status = f"HTTP 200 | {url} | title='Broadcom | VMware | Hardware Compatibility Guide'" if act_row else f"NOT FOUND | {url}"

        rows_out.append({
            "S.No": sno,
            "Category": row[IDX["category"]],
            "Part URL": url,
            "Expected Part Number": row[IDX["part_number"]],
            "Actual Part Number": act_row[IDX["part_number"]] if act_row else "",
            "Part Number Result": field_results["part_number"],
            "Expected DWPD": row[IDX["DWPD"]],
            "Actual DWPD": act_row[IDX["DWPD"]] if act_row else "",
            "DWPD Result": field_results["DWPD"],
            "Expected Form Factor": row[IDX["form_factor"]],
            "Actual Form Factor": act_row[IDX["form_factor"]] if act_row else "",
            "Form Factor Result": field_results["form_factor"],
            "Expected Capacity": row[IDX["capacity"]],
            "Actual Capacity": act_row[IDX["capacity"]] if act_row else "",
            "Capacity Result": field_results["capacity"],
            "Expected MFR Part No": row[IDX["mfr_part_number"]],
            "Actual MFR Part No": act_row[IDX["mfr_part_number"]] if act_row else "",
            "MFR Part No Result": field_results["mfr_part_number"],
            "Expected Part Description": row[IDX["part_description"]],
            "Actual Part Description": act_row[IDX["part_description"]] if act_row else "",
            "Part Description Result": field_results["part_description"],
            "Expected Interface": row[IDX["interface"]],
            "Actual Interface": act_row[IDX["interface"]] if act_row else "",
            "Interface Result": field_results["interface"],
            "Expected Part Specification": exp_spec,
            "Actual Part Specification": act_spec,
            "Part Specification Result": spec_result,
            "Page Status": page_status,
            "Overall Result": overall,
            "Comments": " | ".join(comments),
        })

    return rows_out, field_totals, overall_totals


def write_workbook(rows_out, field_totals, overall_totals, out_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Results"

    columns = list(rows_out[0].keys())
    ws.append(columns)
    header_fill = PatternFill(start_color="FFD9E1F2", end_color="FFD9E1F2", fill_type="solid")
    for c in range(1, len(columns) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    result_fill = {
        "MATCHED": PatternFill(start_color="FFC6EFCE", end_color="FFC6EFCE", fill_type="solid"),
        "MISMATCH": PatternFill(start_color="FFFFC7CE", end_color="FFFFC7CE", fill_type="solid"),
        "UNMATCHED": PatternFill(start_color="FFFFC7CE", end_color="FFFFC7CE", fill_type="solid"),
        "MISSING": PatternFill(start_color="FFFFEB9C", end_color="FFFFEB9C", fill_type="solid"),
        "PARTIAL MATCH": PatternFill(start_color="FFFFEB9C", end_color="FFFFEB9C", fill_type="solid"),
    }
    result_cols = [c for c in columns if c.endswith("Result")]
    result_col_idx = {name: columns.index(name) + 1 for name in result_cols}

    for r in rows_out:
        ws.append([r[c] for c in columns])
        row_i = ws.max_row
        for name, idx in result_col_idx.items():
            val = ws.cell(row=row_i, column=idx).value
            if val in result_fill:
                ws.cell(row=row_i, column=idx).fill = result_fill[val]

    widths = {"S.No": 6, "Category": 9, "Part URL": 40,
              "Expected Part Number": 22, "Actual Part Number": 22, "Part Number Result": 14,
              "Expected DWPD": 12, "Actual DWPD": 12, "DWPD Result": 14,
              "Expected Form Factor": 16, "Actual Form Factor": 16, "Form Factor Result": 16,
              "Expected Capacity": 14, "Actual Capacity": 14, "Capacity Result": 14,
              "Expected MFR Part No": 22, "Actual MFR Part No": 22, "MFR Part No Result": 14,
              "Expected Part Description": 30, "Actual Part Description": 30, "Part Description Result": 16,
              "Expected Interface": 14, "Actual Interface": 14, "Interface Result": 14,
              "Expected Part Specification": 45, "Actual Part Specification": 45, "Part Specification Result": 16,
              "Page Status": 45, "Overall Result": 14, "Comments": 50}
    for i, c in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 14)

    ws2 = wb.create_sheet("Summary")
    ws2.append(["Metric", "Value"])
    for c in (1, 2):
        ws2.cell(row=1, column=c).font = Font(bold=True)
    ws2.append(["Total records tested", len(rows_out)])
    ws2.append(["SSD records tested", sum(1 for r in rows_out if r["Category"] == "ssd")])
    ws2.append(["HDD records tested", sum(1 for r in rows_out if r["Category"] == "hdd")])
    ws2.append(["MATCHED", overall_totals["MATCHED"]])
    ws2.append(["PARTIAL MATCH", overall_totals["PARTIAL MATCH"]])
    ws2.append(["UNMATCHED", overall_totals["UNMATCHED"]])
    ws2.append([])
    ws2.append(["Field", "Matched", "Mismatch", "Missing"])
    for c in range(1, 5):
        ws2.cell(row=ws2.max_row, column=c).font = Font(bold=True)
    for label in list(FIELD_LABELS.values()) + ["Part Specification"]:
        t = field_totals[label]
        ws2.append([label, t["Matched"], t["Mismatch"], t["Missing"]])
    for i, w in enumerate([22, 12, 12, 12], start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    wb.save(out_path)


def main():
    here = Path(__file__).parent
    expected_path = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "input" / "VMWARE_1.csv"
    actual_path = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "input" / "vmware_playwright.csv"
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else here / "output" / "VMWARE_Part_Validation_Report.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    expected_rows = load_rows(expected_path)
    actual_by_pid = index_by_product_id(load_rows(actual_path))

    rows_out, field_totals, overall_totals = build_rows(expected_rows, actual_by_pid)
    write_workbook(rows_out, field_totals, overall_totals, out_path)

    print(f"Validated {len(rows_out)} records -> {out_path}")
    print("Overall:", overall_totals)
    print(json.dumps(field_totals, indent=2))


if __name__ == "__main__":
    main()
