"""Offline unit tests for audit/diff.py — pure comparison logic, no
network/browser involved (mirrors test_extraction.py's offline style; the
live counterpart of this coverage is audit/run_audit.py itself, exercised
manually per the module docstring, not in the default test run)."""

import json
from types import SimpleNamespace

from audit.diff import diff_server, diff_parts


def _server_record(**overrides):
    base = dict(
        product_name="ABIT - AN9 32X Motherboard",
        server_model="AN9 32X Motherboard",
        server_type="Motherboard",
        processor='["AMD Athlon 64 (AM2) Nvidia nForce 590 SLI"]',
        memory="Standard: 0 MB (Removable); Maximum: 8 GB",
        storage="Bus Architecture: PCI / PCI Express",
        expansion='["4 Socket(s)"]',
        status="extracted",
        specifications_json=json.dumps({"Memory": {"Standard": "0 MB"}, "Storage": {}}),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_diff_server_no_changes_yields_no_mismatch_or_drift():
    old_row = _server_record().__dict__
    live = _server_record()
    mismatches, drift = diff_server(old_row, live)
    assert mismatches == []
    assert drift == []


def test_diff_server_flags_spec_change_as_mismatch():
    old_row = _server_record().__dict__
    live = _server_record(memory="Standard: 0 MB (Removable); Maximum: 16 GB")
    mismatches, drift = diff_server(old_row, live)
    assert any(f == "memory" for f, _, _ in mismatches)
    assert drift == []


def test_diff_server_flags_status_change_as_drift_not_mismatch():
    old_row = _server_record().__dict__
    live = _server_record(status="reprocessed")
    mismatches, drift = diff_server(old_row, live)
    assert mismatches == []
    assert any(f == "status" for f, _, _ in drift)


def test_diff_server_flags_missing_json_category_as_mismatch():
    old_row = _server_record().__dict__
    live = _server_record(specifications_json=json.dumps({"Memory": {"Standard": "0 MB"}}))
    mismatches, _ = diff_server(old_row, live)
    assert any(f == "specifications_json.keys" for f, _, _ in mismatches)


def test_diff_server_ignores_whitespace_and_case_only_differences():
    old_row = _server_record(server_type="  Motherboard ").__dict__
    live = _server_record(server_type="motherboard")
    mismatches, _ = diff_server(old_row, live)
    assert mismatches == []


def _part(**overrides):
    base = dict(
        part_number="KVR800D2E6/1G",
        component_name="1GB DDR2 800MT/s ECC Unbuffered DIMM",
        component_model="KVR800D2E6",
        capacity="1GB",
        form_factor="DIMM",
        component_url="",
        datasheet_url="https://www.kingston.com/datasheets/KVR800D2E6_1G.pdf",
        status="Discontinued: Get support",
        description="DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_diff_parts_matches_stable_fields_and_reports_no_diff():
    old_rows = [_part().__dict__]
    live_parts = [_part()]
    result = diff_parts(old_rows, live_parts)
    assert result["mismatches"] == []
    assert result["missing_on_live"] == []
    assert result["new_on_live"] == []


def test_diff_parts_flags_capacity_change_as_mismatch_keyed_by_part_number():
    old_rows = [_part().__dict__]
    live_parts = [_part(capacity="2GB")]
    result = diff_parts(old_rows, live_parts)
    assert ("KVR800D2E6/1G", "capacity", "1GB", "2GB") in result["mismatches"]


def test_diff_parts_flags_status_change_as_drift():
    old_rows = [_part().__dict__]
    live_parts = [_part(status="Available")]
    result = diff_parts(old_rows, live_parts)
    assert any(pn == "KVR800D2E6/1G" and field == "status" for pn, field, _, _ in result["drift"])
    assert result["mismatches"] == []


def test_diff_parts_detects_part_removed_from_live_site():
    old_rows = [_part(part_number="OLD123").__dict__]
    live_parts = [_part(part_number="NEW456")]
    result = diff_parts(old_rows, live_parts)
    assert result["missing_on_live"] == ["OLD123"]
    assert result["new_on_live"] == ["NEW456"]
