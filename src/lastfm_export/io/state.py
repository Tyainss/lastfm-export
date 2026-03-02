
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from lastfm_export.io.readers import read_csv_records, read_json_records, read_ndjson_records


def read_watermark_from_ndjson(path: Path) -> Optional[int]:
    """Return the max timestamp_unix found in an NDJSON file, else None."""
    return _read_watermark(path, read_ndjson_records, ts_field="timestamp_unix")


def read_watermark_from_json(path: Path) -> Optional[int]:
    """
    Return the max timestamp_unix found in a JSON array file, else None.

    Note: JSON arrays are not append-friendly; this is mainly for symmetry and small files.
    """
    return _read_watermark(path, read_json_records, ts_field="timestamp_unix")


def read_watermark_from_csv(path: Path) -> Optional[int]:
    """
    Return the max timestamp_unix found in a CSV file, else None.

    Note: CSV values are strings; this coerces to int when possible.
    """
    return _read_watermark(path, read_csv_records, ts_field="timestamp_unix")


def _read_watermark(
    path: Path,
    reader: Callable[[Path], Iterator[dict[str, Any]]],
    *,
    ts_field: str,
) -> Optional[int]:
    if not path.exists():
        return None

    max_ts: Optional[int] = None
    for rec in reader(path):
        ts = rec.get(ts_field)
        if ts is None:
            continue
        try:
            val = int(ts)
        except (TypeError, ValueError):
            continue
        
        if max_ts is None or val > max_ts:
            max_ts = val

    return max_ts