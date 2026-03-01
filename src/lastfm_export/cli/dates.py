from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

from lastfm_export.errors import ConfigError


UTC = timezone.utc


@dataclass(frozen=True)
class TimeWindow:
    from_unix: Optional[int]
    to_unix_inclusive: Optional[int]


def resolve_time_window(
    *,
    from_unix: Optional[int],
    to_unix: Optional[int],
    from_text: Optional[str],
    to_text: Optional[str],
) -> TimeWindow:
    """
    Resolve a time window into unix seconds (UTC), enforcing clean semantics.

    Accepted formats for *text* inputs:
      - Date: YYYY-MM-DD
      - Datetime: YYYY-MM-DDTHH:MM:SS (also accepts space instead of 'T')
      - Datetime with offset: YYYY-MM-DDTHH:MM:SS+00:00 (converted to UTC)

    Semantics:
      - Date-only:
          from = YYYY-MM-DD 00:00:00 UTC
          to   = (YYYY-MM-DD + 1 day) 00:00:00 UTC, then converted to inclusive by -1 second
      - Datetime:
          treated as UTC if naive, otherwise converted to UTC.

    Returns:
      TimeWindow(from_unix, to_unix_inclusive)
    """
    _ensure_mutual_exclusive("--from", from_text, "--from-unix", from_unix)
    _ensure_mutual_exclusive("--to", to_text, "--to-unix", to_unix)

    resolved_from = from_unix if from_unix is not None else _parse_from_text(from_text)
    resolved_to = to_unix if to_unix is not None else _parse_to_text(to_text)

    if resolved_from is not None and resolved_to is not None and resolved_from > resolved_to:
        raise ConfigError("Invalid time window: from is greater than to.")

    return TimeWindow(from_unix=resolved_from, to_unix_inclusive=resolved_to)


def _ensure_mutual_exclusive(flag_a: str, val_a, flag_b: str, val_b) -> None:
    if val_a is not None and val_b is not None:
        raise ConfigError(f"Use either {flag_a} or {flag_b}, not both.")


def _parse_from_text(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    dt = _parse_date_or_datetime(value)
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt_utc = datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=UTC)
        return int(dt_utc.timestamp())
    dt_utc = _as_utc_datetime(dt)
    return int(dt_utc.timestamp())


def _parse_to_text(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    dt = _parse_date_or_datetime(value)
    if isinstance(dt, date) and not isinstance(dt, datetime):
        # date-only "to" means end-of-day UTC (implemented as next-day exclusive then -1 second)
        next_day = datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=UTC) + timedelta(days=1)
        return int(next_day.timestamp()) - 1
    dt_utc = _as_utc_datetime(dt)
    return int(dt_utc.timestamp())


def _parse_date_or_datetime(value: str) -> date | datetime:
    raw = value.strip()
    raw = raw.replace(" ", "T")

    # Datetime has 'T' separator (after normalization)
    if "T" in raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError as e:
            raise ConfigError(f"Invalid datetime format for '{value}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS.") from e

    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise ConfigError(f"Invalid date format for '{value}'. Use YYYY-MM-DD.") from e


def _as_utc_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
