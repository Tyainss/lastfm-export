import csv
import json
from pathlib import Path
from typing import Any, Iterator


def read_ndjson_records(path: Path) -> Iterator[dict[str, Any]]:
    """
    Read newline-delimited JSON (one JSON object per line).
    Skips empty lines.
    """
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)

def read_json_records(path: Path) -> Iterator[dict[str, Any]]:
    """
    Read a JSON file containing a list of objects.
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected a JSON array (list of objects).")

    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Expected all JSON array items to be objects (dict).")
        yield item


def read_csv_records(path: Path) -> Iterator[dict[str, Any]]:
    """
    Read a CSV file into dict records.
    """
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield dict(row)