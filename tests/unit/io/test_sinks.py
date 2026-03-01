
import json
from pathlib import Path

from lastfm_export.io.state import (
    read_watermark_from_csv,
    read_watermark_from_json,
    read_watermark_from_ndjson,
)


def test_read_watermark_from_ndjson_returns_last_timestamp(tmp_path: Path):
    path = tmp_path / "out.ndjson"
    path.write_text('{"timestamp_unix": 1}\n{"timestamp_unix": 5}\n', encoding="utf-8")

    assert read_watermark_from_ndjson(path) == 5


def test_read_watermark_from_json_returns_last_timestamp(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text(json.dumps([{"timestamp_unix": 1}, {"timestamp_unix": 5}]), encoding="utf-8")

    assert read_watermark_from_json(path) == 5


def test_read_watermark_from_csv_returns_last_timestamp(tmp_path: Path):
    path = tmp_path / "out.csv"
    path.write_text("timestamp_unix\n1\n5\n", encoding="utf-8")

    assert read_watermark_from_csv(path) == 5


def test_read_watermark_returns_none_if_missing(tmp_path: Path):
    assert read_watermark_from_ndjson(tmp_path / "missing.ndjson") is None
    assert read_watermark_from_json(tmp_path / "missing.json") is None
    assert read_watermark_from_csv(tmp_path / "missing.csv") is None