"""Verifies kingston_memory_parts.csv's dynamic-column structure:

- every specification key becomes its own CSV column, value under it
- parts with different specification keys don't clobber each other's
  columns — the file grows to the union, blank where a key doesn't apply
- General/Description info, Compatible Server/Server URL, and Status all
  get their own columns, separate from the dynamic specification columns
- part_specifications_json stays the authoritative complete JSON, and
  every value it carries matches the corresponding flat CSV column
  exactly (no drift between the two representations)
- no duplicate rows, including across a resumed run that discovers a new
  specification key partway through (DynamicCsvWriter must preserve
  already-written rows' data when the column set grows)
"""

import csv
import json

from agent.memory_extractor import build_csv_row, migrate_legacy_row
from models.models import PartRecord
from utils.io_utils import DynamicCsvWriter


def _key_fn(row):
    return f"{(row.get('Server URL') or '').lower()}::{(row.get('Part Number') or '').lower()}"


def _make_part(part_number, sections, **overrides):
    defaults = dict(
        brand="Kingston",
        server_name="Server A",
        server_url="https://example.com/a",
        component_type="Memory",
        component_name=sections.get("General", {}).get("Component Name", ""),
        component_model=sections.get("General", {}).get("Component Model", ""),
        part_number=part_number,
        component_url="",
        datasheet_url="https://www.kingston.com/datasheets/x.pdf",
        description=sections.get("Description", {}).get("Description", ""),
        specifications="",
        compatibility="Server A",
        part_specifications_json=json.dumps(sections),
        status=sections.get("Status", {}).get("Status", "Available"),
    )
    defaults.update(overrides)
    return PartRecord(**defaults)


def test_build_csv_row_flattens_every_section_with_expected_column_names():
    sections = {
        "General": {
            "Part Number": "KVR800D2E6/1G",
            "Component Name": "1GB DDR2 800MT/s ECC Unbuffered DIMM",
            "Component Model": "KVR800D2E6",
            "Component Type": "Memory",
        },
        "Specifications": {
            "Release Date": "05/02/2008 00:00:00.0000",
            "Capacity": "1GB",
            "Memory Type": "DDR2",
            "Speed": "800MT/s",
            "Module Type": "DIMM",
            "ECC": "ECC",
            "Registered/Unbuffered": "Unbuffered",
            "CAS Latency": "CL6",
            "Voltage": "1.8V",
            "Pin Count": "240",
        },
        "Description": {"Description": "DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin"},
        "Compatibility": {
            "Server": "ABIT- AN9 32X Motherboard",
            "Server URL": "https://www.kingston.com/en/memory/search/model/36475/abit-an9-32x-motherboard",
        },
        "Status": {"Status": "Discontinued: Get support"},
    }
    part = _make_part(
        "KVR800D2E6/1G",
        sections,
        server_name="ABIT- AN9 32X Motherboard",
        server_url="https://www.kingston.com/en/memory/search/model/36475/abit-an9-32x-motherboard",
        component_name="1GB DDR2 800MT/s ECC Unbuffered DIMM",
        component_model="KVR800D2E6",
        description="DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin",
        status="Discontinued: Get support",
    )

    row = build_csv_row(part)

    # Identity columns.
    assert row["Brand"] == "Kingston"
    assert row["Server Name"] == "ABIT- AN9 32X Motherboard"
    assert row["Part Number"] == "KVR800D2E6/1G"
    assert row["Component Model"] == "KVR800D2E6"
    assert row["Description"] == "DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin"

    # Compatibility renamed per the task's own wording, kept separate from
    # specifications rather than one combined string.
    assert row["Compatible Server"] == "ABIT- AN9 32X Motherboard"
    assert row["Compatible Server URL"].endswith("abit-an9-32x-motherboard")

    # Every specification key is its own column, exact Kingston wording
    # preserved (not renamed/simplified).
    assert row["Release Date"] == "05/02/2008 00:00:00.0000"
    assert row["Capacity"] == "1GB"
    assert row["Memory Type"] == "DDR2"
    assert row["Speed"] == "800MT/s"
    assert row["Module Type"] == "DIMM"
    assert row["ECC"] == "ECC"
    assert row["Registered/Unbuffered"] == "Unbuffered"
    assert row["CAS Latency"] == "CL6"
    assert row["Voltage"] == "1.8V"
    assert row["Pin Count"] == "240"

    # Status is its own column, separate from Specifications.
    assert row["Status"] == "Discontinued: Get support"

    # The complete JSON stays intact and exactly matches what's asked for.
    assert row["part_specifications_json"] == part.part_specifications_json
    round_tripped = json.loads(row["part_specifications_json"])
    assert round_tripped == sections

    # No leftover generic 'specifications' blob column.
    assert "specifications" not in row
    assert "Specifications" not in row  # the bucket name itself, not a column


def test_different_parts_with_different_spec_keys_dont_clobber_columns():
    """One part has CAS Latency; another has Rank/DRAM Density instead —
    both must be supported, each getting blank cells for the other's
    columns rather than losing data or crashing."""
    part_a = _make_part(
        "KVR800D2E6/1G",
        {
            "General": {"Part Number": "KVR800D2E6/1G", "Component Type": "Memory"},
            "Specifications": {"Capacity": "1GB", "CAS Latency": "CL6"},
        },
    )
    part_b = _make_part(
        "KSM32RD8/16HDR",
        {
            "General": {"Part Number": "KSM32RD8/16HDR", "Component Type": "Memory"},
            "Specifications": {"Capacity": "16GB", "Rank": "Dual Rank", "DRAM Density": "16Gbit"},
        },
        server_url="https://example.com/b",
    )

    writer = DynamicCsvWriter("/tmp/does-not-exist-memory-parts-test.csv", key_fn=_key_fn)
    assert writer.write_row(build_csv_row(part_a))
    assert writer.write_row(build_csv_row(part_b))

    assert "CAS Latency" in writer.columns
    assert "Rank" in writer.columns
    assert "DRAM Density" in writer.columns

    row_a = next(r for r in writer.rows if r["Part Number"] == "KVR800D2E6/1G")
    row_b = next(r for r in writer.rows if r["Part Number"] == "KSM32RD8/16HDR")
    assert row_a.get("Rank", "") == ""  # blank, not fabricated
    assert row_a.get("DRAM Density", "") == ""
    assert row_b.get("CAS Latency", "") == ""
    assert row_b["Rank"] == "Dual Rank"
    assert row_b["DRAM Density"] == "16Gbit"


def test_dynamic_writer_persists_and_reloads_across_a_resumed_run(tmp_path):
    path = tmp_path / "kingston_memory_parts.csv"

    part_a = _make_part(
        "PN-A",
        {"General": {"Part Number": "PN-A"}, "Specifications": {"Capacity": "1GB"}},
    )
    writer1 = DynamicCsvWriter(path, key_fn=_key_fn)
    assert writer1.write_row(build_csv_row(part_a))
    writer1.flush()

    # "Resume": a second writer picks up where the first left off, and a
    # brand-new specification key (never seen before) must extend the
    # header without disturbing part_a's already-written row.
    part_b = _make_part(
        "PN-B",
        {"General": {"Part Number": "PN-B"}, "Specifications": {"Capacity": "2GB", "Rank": "Single Rank"}},
        server_url="https://example.com/b",
    )
    writer2 = DynamicCsvWriter(path, key_fn=_key_fn)
    assert "PN-A" not in writer2.seen_keys  # sanity: keys are url::part_number, not bare part numbers
    assert _key_fn({"Server URL": "https://example.com/a", "Part Number": "PN-A"}) in writer2.seen_keys
    assert writer2.write_row(build_csv_row(part_b))
    # Re-adding part_a must be rejected as a duplicate, exactly like the
    # fixed-column CsvWriter does.
    assert writer2.write_row(build_csv_row(part_a)) is False
    writer2.flush()

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 2  # part_a preserved, part_b added, no duplicate
    assert "Rank" in rows[0].keys()  # header grew to include the new key
    row_a = next(r for r in rows if r["Part Number"] == "PN-A")
    row_b = next(r for r in rows if r["Part Number"] == "PN-B")
    assert row_a["Capacity"] == "1GB"
    assert row_a["Rank"] == ""  # part_a never had this key — blank, not lost/corrupted
    assert row_b["Rank"] == "Single Rank"


def test_resuming_onto_a_legacy_fixed_schema_file_does_not_duplicate_columns(tmp_path):
    """Regression test: a kingston_memory_parts.csv written under the old
    fixed, all-lowercase/snake_case schema (before this file had dynamic
    per-specification columns) must be upgraded on load, not left sitting
    alongside a second, differently-cased column set — that was the exact
    bug behind "the Status column and data is missing": old rows got a
    lowercase 'status' column, new rows got 'Status', and each was blank
    for the other schema's rows.
    """
    path = tmp_path / "kingston_memory_parts.csv"
    legacy_sections = {
        "General": {"Part Number": "PN-OLD", "Component Type": "Memory"},
        "Specifications": {"Capacity": "1GB"},
        "Compatibility": {"Server": "Server A", "Server URL": "https://example.com/a"},
        "Status": {"Status": "Available"},
    }
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["brand", "server_name", "server_url", "part_number", "status", "part_specifications_json"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "brand": "Kingston",
                "server_name": "Server A",
                "server_url": "https://example.com/a",
                "part_number": "PN-OLD",
                "status": "Available",
                "part_specifications_json": json.dumps(legacy_sections),
            }
        )

    dyn_writer = DynamicCsvWriter(path, key_fn=_key_fn, row_migrator=migrate_legacy_row)

    # The legacy row's data was preserved and upgraded — not lost, not
    # duplicated under a second column name.
    assert "status" not in dyn_writer.columns
    assert dyn_writer.columns.count("Status") == 1
    old_row = dyn_writer.rows[0]
    assert old_row["Status"] == "Available"
    assert old_row["Server Name"] == "Server A"
    assert old_row["Capacity"] == "1GB"

    # A new-shape row added afterward shares the same "Status" column —
    # no parallel mismatched column gets created.
    new_part = _make_part(
        "PN-NEW",
        {
            "General": {"Part Number": "PN-NEW"},
            "Specifications": {"Capacity": "2GB"},
            "Status": {"Status": "Discontinued: Get support"},
        },
        server_url="https://example.com/b",
    )
    assert dyn_writer.write_row(build_csv_row(new_part))
    dyn_writer.flush()

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    assert list(rows[0].keys()).count("Status") == 1  # DictReader would list duplicate fieldnames only once anyway,
    # so the real check is that every row's Status is populated:
    statuses = {r["Part Number"]: r["Status"] for r in rows}
    assert statuses == {"PN-OLD": "Available", "PN-NEW": "Discontinued: Get support"}
