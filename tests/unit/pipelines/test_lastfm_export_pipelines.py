from typing import Iterator, Optional

from lastfm_export.models import Scrobble
from lastfm_export.integrity import WindowReport, WindowResult
from lastfm_export.pipelines.lastfm_export import collect_verified_scrobbles
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
        Scrobble(artist_name="A", track_name="T3", album_name=None, timestamp_unix=21),
        Scrobble(artist_name="A", track_name="T2", album_name=None, timestamp_unix=20),
        Scrobble(artist_name="A", track_name="T1", album_name=None, timestamp_unix=10),
    ]
    lastfm = _FakeLastFMClient(items)

    out = list(export_scrobbles(lastfm=lastfm, watermark=20))

    assert [s.track_name for s in out] == ["T3"]


def test_export_scrobbles_stops_early_when_reaching_watermark():
    seen: list[int] = []

    class _StopAwareLastFMClient(_FakeLastFMClient):
        def iter_recent_tracks(self, **kwargs):
            for sc in self._items:
                seen.append(sc.timestamp_unix)
                yield sc

    items = [
        Scrobble(artist_name="A", track_name="T3", album_name=None, timestamp_unix=21),
        Scrobble(artist_name="A", track_name="T2", album_name=None, timestamp_unix=20),
        Scrobble(artist_name="A", track_name="T1", album_name=None, timestamp_unix=10),
    ]
    lastfm = _StopAwareLastFMClient(items)

    out = list(export_scrobbles(lastfm=lastfm, watermark=20))

    assert [s.timestamp_unix for s in out] == [21]
    # Stops as soon as it sees <= watermark
    assert seen == [21, 20]


def test_export_scrobbles_reports_completed_pages_and_collected_tracks():
    class _PagedClient(_FakeLastFMClient):
        def iter_recent_tracks(self, *, on_page=None, **kwargs):
            yield self._items[0]
            if on_page is not None:
                on_page(1)
            yield self._items[1]
            if on_page is not None:
                on_page(2)

    items = [
        Scrobble(artist_name="A", track_name="T1", album_name=None, timestamp_unix=2),
        Scrobble(artist_name="A", track_name="T2", album_name=None, timestamp_unix=1),
    ]
    reported = []

    assert (
        list(
            export_scrobbles(
                lastfm=_PagedClient(items),
                on_page=lambda page, tracks: reported.append((page, tracks)),
            )
        )
        == items
    )
    assert reported == [(1, 1), (2, 2)]


def test_collect_verified_scrobbles_uses_disjoint_utc_days_newest_first():
    class _WindowClient:
        def __init__(self):
            self.calls = []

        def fetch_recent_tracks_window(self, *, from_unix, to_unix, page_size):
            self.calls.append((from_unix, to_unix, page_size))
            report = WindowReport(
                from_unix, to_unix, api_total=1, materialized_count=1, page_count=1
            )
            return WindowResult([Scrobble("A", str(to_unix), None, to_unix)], report)

    client = _WindowClient()
    records, reports = collect_verified_scrobbles(
        lastfm=client, from_unix=1, to_unix=86_400, page_size=50
    )

    assert client.calls == [(86_400, 86_400, 50), (1, 86_399, 50)]
    assert [record.timestamp_unix for record in records] == [86_400, 86_399]
    assert len(reports) == 2


def test_collect_verified_scrobbles_reports_window_start_and_completion():
    class _WindowClient:
        def fetch_recent_tracks_window(self, *, from_unix, to_unix, page_size):
            report = WindowReport(
                from_unix, to_unix, api_total=1, materialized_count=1, page_count=1
            )
            return WindowResult([Scrobble("A", str(to_unix), None, to_unix)], report)

    started = []
    completed = []
    collect_verified_scrobbles(
        lastfm=_WindowClient(),
        from_unix=1,
        to_unix=86_400,
        on_window_start=lambda day, days, tracks: started.append(
            (str(day), days, tracks)
        ),
        on_window_complete=lambda event: completed.append(
            (str(event.day), event.days_checked, event.tracks_collected)
        ),
    )

    assert started == [("1970-01-02", 0, 0), ("1970-01-01", 1, 1)]
    assert completed == [("1970-01-02", 1, 1), ("1970-01-01", 2, 2)]
