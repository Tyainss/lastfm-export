
import json
from pathlib import Path

import pytest

from lastfm_export.io.readers import read_csv_records, read_json_records, read_ndjson_records


def test_read_ndjson_records_reads_objects(tmp_path: Path):
    path = tmp_path / "in.ndjson"
    path.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")

    out = list(read_ndjson_records(path))
    assert out == [{"a": 1}, {"b": 2}]


def test_read_json_records_reads_array(tmp_path: Path):
    path = tmp_path / "in.json"
    path.write_text(json.dumps([{"a": 1}, {"b": 2}]), encoding="utf-8")

    out = list(read_json_records(path))
    assert out == [{"a": 1}, {"b": 2}]


def test_read_json_records_raises_if_not_array(tmp_path: Path):
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")

    with pytest.raises(ValueError):
        list(read_json_records(path))


def test_read_csv_records_reads_rows(tmp_path: Path):
    path = tmp_path / "in.csv"
    path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    out = list(read_csv_records(path))
    assert out == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]