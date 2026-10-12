import csv
import json

from utils.io_utils import read_input_servers, CsvWriter, validate_csv_file, validate_and_log_csv_file


def test_read_input_servers(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text("name,url\nBoard A,https://example.com/a\nBoard B,https://example.com/b\n")
    servers = read_input_servers(path)
    assert servers == [
        {"name": "Board A", "url": "https://example.com/a"},
        {"name": "Board B", "url": "https://example.com/b"},
    ]


def test_read_input_servers_skips_blank_urls(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text("name,url\nBoard A,https://example.com/a\nBoard C,\n")
    servers = read_input_servers(path)
    assert len(servers) == 1


def test_read_input_servers_missing_url_column_raises(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text("name,foo\nBoard A,bar\n")
    try:
        read_input_servers(path)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_csv_writer_dedupes_across_runs(tmp_path):
    path = tmp_path / "out.csv"
    columns = ["server_url", "value"]
    writer = CsvWriter(path, columns, key_fn=lambda r: r.get("server_url", ""))
    assert writer.write_row({"server_url": "https://x", "value": "1"}) is True
    assert writer.write_row({"server_url": "https://x", "value": "2"}) is False  # dup, same run

    # simulate a fresh process re-opening the same output file
    writer2 = CsvWriter(path, columns, key_fn=lambda r: r.get("server_url", ""))
    assert writer2.write_row({"server_url": "https://x", "value": "3"}) is False

    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1


def test_csv_writer_heals_header_when_schema_changed_since_last_write(tmp_path):
    """Regression test: if the on-disk header no longer matches the
    current column set (a column was added/removed/reordered since this
    file was last written), CsvWriter must rewrite the file under the
    current schema before appending anything new — otherwise the header
    line and the data rows beneath it silently drift out of alignment
    (every reader then mislabels values under the wrong column name)."""
    path = tmp_path / "servers.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["brand", "server_name", "server_url"])  # old, narrower schema
        w.writerow(["Kingston", "Old Server", "https://example.com/old"])

    new_columns = ["brand", "server_name", "server_url", "server_model", "status"]
    writer = CsvWriter(path, new_columns, key_fn=lambda r: r.get("server_url", ""))
    assert writer.write_row(
        {
            "brand": "Kingston",
            "server_name": "New Server",
            "server_url": "https://example.com/new",
            "server_model": "New Model",
            "status": "SERVER_SUCCESS",
        }
    )

    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == new_columns
        rows = list(reader)

    assert len(rows) == 2
    old_row = next(r for r in rows if r["server_name"] == "Old Server")
    new_row = next(r for r in rows if r["server_name"] == "New Server")
    # The healed old row's original values stay under their correct
    # (unchanged) column names; the new column is blank, not fabricated.
    assert old_row["server_url"] == "https://example.com/old"
    assert old_row["server_model"] == ""
    # The new row's data lands under exactly the columns it was written
    # with — nothing shifted.
    assert new_row["server_model"] == "New Model"
    assert new_row["status"] == "SERVER_SUCCESS"


def test_csv_writer_leaves_a_consistent_file_untouched(tmp_path):
    """No header/schema change -> no rewrite, no data disturbed."""
    path = tmp_path / "out.csv"
    columns = ["server_url", "value"]
    writer1 = CsvWriter(path, columns, key_fn=lambda r: r.get("server_url", ""))
    writer1.write_row({"server_url": "https://x", "value": "1"})
    before = path.read_text()

    writer2 = CsvWriter(path, columns, key_fn=lambda r: r.get("server_url", ""))
    assert path.read_text() == before  # untouched — nothing needed healing
    assert writer2.write_row({"server_url": "https://y", "value": "2"})


def test_validate_csv_file_passes_on_a_well_formed_file(tmp_path):
    path = tmp_path / "servers.csv"
    columns = ["server_name", "processor", "memory"]
    writer = CsvWriter(path, columns, key_fn=lambda r: r.get("server_name", ""))
    writer.write_row(
        {
            "server_name": "Server A",
            "processor": json.dumps(["Intel Xeon E5-2600", "Intel Xeon E5-2600 v2", "Intel Xeon E5-2600 v3"]),
            "memory": "128GB",
        }
    )
    assert validate_csv_file(path) == []
    assert validate_and_log_csv_file(path) is True


def test_validate_csv_file_catches_a_row_with_the_wrong_field_count(tmp_path):
    """Reproduces the literal reported symptom: a multi-value field that
    spilled into extra raw comma-separated cells instead of staying one
    properly-quoted JSON array — the header still says N columns, but this
    row has more than N fields."""
    path = tmp_path / "servers.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["server_name", "processor", "memory"])
        # "Processor 1,Processor 2,Processor 3" written unquoted/unescaped
        # instead of as one JSON-array field — exactly the bug being fixed.
        w.writerow(["Server A", "Processor 1", "Processor 2", "Processor 3", "128GB"])

    problems = validate_csv_file(path)
    assert len(problems) == 1
    assert "5 field(s), header has 3" in problems[0]
    assert validate_and_log_csv_file(path) is False


def test_validate_csv_file_catches_a_malformed_json_looking_cell(tmp_path):
    path = tmp_path / "servers.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["server_name", "processor"])
        w.writerow(["Server A", "[Intel Xeon, Intel Xeon v2]"])  # looks array-ish but unquoted -> invalid JSON

    problems = validate_csv_file(path)
    assert len(problems) == 1
    assert "processor" in problems[0]
    assert "does not parse" in problems[0]


def test_validate_csv_file_on_missing_file_reports_a_problem_not_an_exception(tmp_path):
    problems = validate_csv_file(tmp_path / "does-not-exist.csv")
    assert len(problems) == 1
    assert "does not exist" in problems[0]
