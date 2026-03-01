import os
from pathlib import Path
from typing import Optional

from lastfm_export.errors import ConfigError
from lastfm_export.io.state import (
    read_watermark_from_csv,
    read_watermark_from_json,
    read_watermark_from_ndjson,
)


def get_env_or_value(name: str, value: Optional[str]) -> str:
    if value:
        return value
    env = os.getenv(name)
    if env:
        return env
    raise ConfigError(f"Missing required config: {name}")


def infer_format(path: Path, fmt: Optional[str]) -> str:
    if fmt:
        return fmt.lower()
    ext = path.suffix.lower()
    if ext == ".ndjson":
        return "ndjson"
    if ext == ".json":
        return "json"
    if ext == ".csv":
        return "csv"
    raise ConfigError("Could not infer format from file extension. Use --format.")


def read_watermark(path: Path, fmt: str) -> Optional[int]:
    if fmt == "ndjson":
        return read_watermark_from_ndjson(path)
    if fmt == "json":
        return read_watermark_from_json(path)
    if fmt == "csv":
        return read_watermark_from_csv(path)
    return None


def ensure_overwrite_allowed(*, out: Path, fmt: str, overwrite: bool) -> None:
    """
    Enforce predictable output semantics.

    - NDJSON is append-friendly; overwrite controls truncation.
    - JSON/CSV are not append-friendly in this package; overwrite must be True if file exists.
    """
    if not out.exists():
        return
    if overwrite:
        return

    if fmt == "ndjson":
        return

    raise ConfigError(
        f"Output file already exists and format '{fmt}' is not append-friendly. "
        "Use --overwrite to replace it, or choose --format ndjson."
    )