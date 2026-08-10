from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.integrity import WindowReport
from lastfm_export.models import Scrobble


def export_scrobbles(
    *,
    lastfm: LastFMClient,
    from_unix: Optional[int] = None,
    to_unix: Optional[int] = None,
    page_size: int = 200,
    page_limit: Optional[int] = None,
    watermark: Optional[int] = None,
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
    for scrobble in lastfm.iter_recent_tracks(
        from_unix=from_unix,
        to_unix=to_unix,
        page_size=page_size,
        page_limit=page_limit,
    ):
        if watermark is not None and scrobble.timestamp_unix <= watermark:
            return
        yield scrobble


def collect_verified_scrobbles(
    *,
    lastfm: LastFMClient,
    from_unix: int,
    to_unix: int,
    page_size: int = 200,
    watermark: Optional[int] = None,
    stop_on_violation: bool = True,
) -> tuple[list[Scrobble], list[WindowReport]]:
    """Collect newest-to-oldest UTC-day windows with validation reports."""
    records: list[Scrobble] = []
    reports: list[WindowReport] = []
    day = datetime.fromtimestamp(to_unix, timezone.utc).date()
    earliest = datetime.fromtimestamp(from_unix, timezone.utc).date()

    while day >= earliest:
        day_start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        day_end = day_start + 86_399
        result = lastfm.fetch_recent_tracks_window(
            from_unix=max(day_start, from_unix),
            to_unix=min(day_end, to_unix),
            page_size=page_size,
        )
        reports.append(result.report)
        if not result.report.ok and stop_on_violation:
            break

        for scrobble in result.scrobbles:
            if watermark is not None and scrobble.timestamp_unix <= watermark:
                return records, reports
            records.append(scrobble)
        day -= timedelta(days=1)

    return records, reports
