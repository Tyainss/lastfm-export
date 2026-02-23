from typing import Iterator, Optional

from lastfm_export.models import Scrobble
from lastfm_export.pipelines.lastfm_export import export_scrobbles


class _FakeLastFMClient:
    def __init__(self, items: list[Scrobble]) -> None:
        self._items = items
        self.calls: list[dict] = []

    def iter_recent_tracks(
        self,
        *,
        from_unix: Optional[int] = None,
        to_unix: Optional[int] = None,
        page_size: int = 200,
        page_limit: Optional[int] = None,
    ) -> Iterator[Scrobble]:
        self.calls.append(
            {
                "from_unix": from_unix,
                "to_unix": to_unix,
                "page_size": page_size,
                "page_limit": page_limit,
            }
        )
        yield from self._items


def test_export_scrobbles_yields_all_without_watermark():
    items = [
        Scrobble(artist_name="A", track_name="T1", album_name=None, timestamp_unix=10),
        Scrobble(artist_name="A", track_name="T2", album_name=None, timestamp_unix=20),
    ]
    lastfm = _FakeLastFMClient(items)

    out = list(export_scrobbles(lastfm=lastfm))

    assert [s.track_name for s in out] == ["T1", "T2"]
    assert len(lastfm.calls) == 1
    assert lastfm.calls[0]["page_size"] == 200


def test_export_scrobbles_filters_by_watermark():
    items = [
        Scrobble(artist_name="A", track_name="T1", album_name=None, timestamp_unix=10),
        Scrobble(artist_name="A", track_name="T2", album_name=None, timestamp_unix=20),
        Scrobble(artist_name="A", track_name="T3", album_name=None, timestamp_unix=21),
    ]
    lastfm = _FakeLastFMClient(items)

    out = list(export_scrobbles(lastfm=lastfm, watermark=20))

    assert [s.track_name for s in out] == ["T3"]