from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Iterator, Literal, Optional

from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.integrity import WindowReport, WindowResult
from lastfm_export.models import Scrobble


@dataclass(frozen=True, slots=True)
class VerifiedProgress:
    day: date
    days_checked: int
    tracks_collected: int
    report: WindowReport


def _overlap_retry_size(report: WindowReport, page_size: int) -> int | None:
    if any(v.startswith("exact raw-payload overlap") for v in report.violations):
        return 199 if page_size >= 200 else page_size + 1
    return None


def _fetch_verified_range(
    *,
    lastfm: LastFMClient,
    from_unix: int,
    to_unix: int,
    page_size: int,
) -> WindowResult:
    result = lastfm.fetch_recent_tracks_window(
        from_unix=from_unix,
        to_unix=to_unix,
        page_size=page_size,
    )
    retry_size = _overlap_retry_size(result.report, page_size)
    if retry_size is not None:
        result = lastfm.fetch_recent_tracks_window(
            from_unix=from_unix,
            to_unix=to_unix,
            page_size=retry_size,
        )
    return result


def iter_utc_windows(
    *, from_unix: int, to_unix: int, period: Literal["day", "month"] = "day"
) -> Iterator[tuple[int, int]]:
    """Plan disjoint inclusive UTC windows, newest first, clipping both ends."""
    if from_unix > to_unix:
        raise ValueError("from_unix must not exceed to_unix")
    if period not in {"day", "month"}:
        raise ValueError("period must be 'day' or 'month'")
    end = to_unix
    while end >= from_unix:
        current = datetime.fromtimestamp(end, timezone.utc)
        start = int(
            datetime(
                current.year,
                current.month,
                1 if period == "month" else current.day,
                tzinfo=timezone.utc,
            ).timestamp()
        )
        yield max(start, from_unix), end
        end = start - 1


def export_scrobbles(
    *,
    lastfm: LastFMClient,
    from_unix: Optional[int] = None,
    to_unix: Optional[int] = None,
    page_size: int = 200,
    page_limit: Optional[int] = None,
    watermark: Optional[int] = None,
    on_page: Optional[Callable[[int, int], None]] = None,
) -> Iterator[Scrobble]:
    """
    Yield scrobbles from Last.fm with optional filtering for incremental exports.

    Args:
        lastfm: Configured LastFMClient.
        from_unix: Inclusive lower bound Unix timestamp (seconds).
        to_unix: Inclusive upper bound Unix timestamp (seconds).
        page_size: Page size forwarded to the client.
        page_limit: Stops after this many pages (useful for testing/sampling).
        watermark: If set, yields only scrobbles with timestamp_unix > watermark.

    Notes:
        The Last.fm API returns recent tracks newest -> oldest. Because of that ordering,
        once we hit a scrobble with timestamp_unix <= watermark we can stop early.
    """
    tracks_collected = 0

    def _on_page(page: int) -> None:
        if on_page is not None:
            on_page(page, tracks_collected)

    iterator_args = {
        "from_unix": from_unix,
        "to_unix": to_unix,
        "page_size": page_size,
        "page_limit": page_limit,
    }
    if on_page is not None:
        iterator_args["on_page"] = _on_page

    for scrobble in lastfm.iter_recent_tracks(**iterator_args):
        if watermark is not None and scrobble.timestamp_unix <= watermark:
            return
        yield scrobble
        tracks_collected += 1


def collect_verified_scrobbles(
    *,
    lastfm: LastFMClient,
    from_unix: int,
    to_unix: int,
    page_size: int = 200,
    watermark: Optional[int] = None,
    stop_on_violation: bool = True,
    on_window_start: Optional[Callable[[date, int, int], None]] = None,
    on_window_complete: Optional[Callable[[VerifiedProgress], None]] = None,
) -> tuple[list[Scrobble], list[WindowReport]]:
    """Collect newest-to-oldest UTC-day windows with validation reports."""
    if from_unix > to_unix:
        raise ValueError("from_unix must not exceed to_unix")
    records: list[Scrobble] = []
    reports: list[WindowReport] = []
    day = datetime.fromtimestamp(to_unix, timezone.utc).date()
    earliest = datetime.fromtimestamp(from_unix, timezone.utc).date()

    while day >= earliest:
        if on_window_start is not None:
            on_window_start(day, len(reports), len(records))
        day_start = int(
            datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
        )
        day_end = day_start + 86_399
        result = _fetch_verified_range(
            lastfm=lastfm,
            from_unix=max(day_start, from_unix),
            to_unix=min(day_end, to_unix),
            page_size=page_size,
        )
        reports.append(result.report)
        if not result.report.ok and stop_on_violation:
            if on_window_complete is not None:
                on_window_complete(
                    VerifiedProgress(day, len(reports), len(records), result.report)
                )
            break

        for scrobble in result.scrobbles:
            if watermark is not None and scrobble.timestamp_unix <= watermark:
                if on_window_complete is not None:
                    on_window_complete(
                        VerifiedProgress(day, len(reports), len(records), result.report)
                    )
                return records, reports
            records.append(scrobble)
        if on_window_complete is not None:
            on_window_complete(
                VerifiedProgress(day, len(reports), len(records), result.report)
            )
        day -= timedelta(days=1)

    return records, reports
