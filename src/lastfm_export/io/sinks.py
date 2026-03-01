import csv
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional


Sink = Callable[[Iterable[Mapping[str, Any]]], None]


def ndjson_sink(path: Path, *, overwrite: bool = False) -> Sink:
    """
    Write records as newline-delimited JSON (one JSON object per line).

    This is streaming-friendly: writes records incrementally without
    loading everything into memory.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "a"

    def _write(records: Iterable[Mapping[str, Any]]) -> None:
        with path.open(mode, encoding="utf-8", newline="\n") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False))
                f.write("\n")

    return _write


def json_sink(path: Path, *, overwrite: bool = True, indent: Optional[int] = 2) -> Sink:
    """
    Write records as a single JSON array.

    This collects all records in memory before writing. Prefer NDJSON for large exports.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"

    def _write(records: Iterable[Mapping[str, Any]]) -> None:
        data = list(records)
        with path.open(mode, encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)

    return _write


def csv_sink(path: Path, *, overwrite: bool = True, fieldnames: Optional[list[str]] = None) -> Sink:
    """
    Write records to CSV.

    If fieldnames is not provided, they are inferred from the first record.
    Missing fields are written as empty cells.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"

    def _write(records: Iterable[Mapping[str, Any]]) -> None:
        it = iter(records)
        first = next(it, None)

        if first is None:
            # Create an empty file with no header
            path.open(mode, encoding="utf-8", newline="").close()
            return

        cols = fieldnames or list(first.keys())

        with path.open(mode, encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerow(_coerce_csv_row(first, cols))

            for rec in it:
                writer.writerow(_coerce_csv_row(rec, cols))

    return _write


def _coerce_csv_row(rec: Mapping[str, Any], cols: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for k in cols:
        v = rec.get(k)
        # Flatten nested dicts/lists into JSON strings so CSV stays valid.
        if isinstance(v, (dict, list)):
            row[k] = json.dumps(v, ensure_ascii=False)
        else:
            row[k] = v
    return row