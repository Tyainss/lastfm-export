from typing import Iterator, Optional

from lastfm_export.clients.lastfm import LastFMClient
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