from collections.abc import Callable, Iterator
from typing import Literal

from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import ScrobbleIntegrityError
from lastfm_export.integrity import WindowReport, WindowResult
from lastfm_export.pipelines.lastfm_export import _overlap_retry_size, iter_utc_windows


def iter_verified_scrobble_windows(
    *,
    lastfm: LastFMClient,
    from_unix: int,
    to_unix: int,
    period: Literal["day", "month"] = "month",
    page_size: int = 200,
    on_rejected: Callable[[WindowReport], None] | None = None,
) -> Iterator[WindowResult]:
    """Yield verified months newest first, retrying once before daily fallback.

    Failed days raise ScrobbleIntegrityError. Save each result before advancing
    to resume without repeating completed windows. Overlap retries change the page
    size to preserve repeated listens without accepting unverified records.
    """
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= 200
    ):
        raise ValueError("page_size must be an integer between 1 and 200")
    for start, end in iter_utc_windows(
        from_unix=from_unix, to_unix=to_unix, period=period
    ):
        request_size = page_size
        for attempt in range(2):
            result = lastfm.fetch_recent_tracks_window(
                from_unix=start, to_unix=end, page_size=request_size
            )
            if result.report.ok:
                yield result
                break
            if on_rejected is not None:
                on_rejected(result.report)
            retry_size = _overlap_retry_size(result.report, page_size)
            if attempt == 0 and retry_size is not None:
                request_size = retry_size
            elif period == "day":
                raise ScrobbleIntegrityError(result.report)
        else:
            yield from iter_verified_scrobble_windows(
                lastfm=lastfm,
                from_unix=start,
                to_unix=end,
                period="day",
                page_size=page_size,
                on_rejected=on_rejected,
            )
