import pytest

from lastfm_export.clients.http import JsonResponse
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import HttpRequestError, ScrobbleIntegrityError
from lastfm_export.integrity import WindowReport, WindowResult
from lastfm_export.models import Scrobble
from lastfm_export.pipelines.lastfm_export import collect_verified_scrobbles
from lastfm_export.pipelines.scrobble_windows import iter_verified_scrobble_windows


class WindowClient:
    def __init__(self, *, month_failures=0, bad_day=None, error=None):
        self.calls = []
        self.month_failures = month_failures
        self.bad_day = bad_day
        self.error = error

    def fetch_recent_tracks_window(self, *, from_unix, to_unix, page_size):
        self.calls.append((from_unix, to_unix))
        if self.error:
            raise self.error
        bad = from_unix == self.bad_day
        if to_unix - from_unix >= 86_400 and self.month_failures:
            self.month_failures -= 1
            bad = True
        records = [Scrobble("A", "Song", None, to_unix)] * 2
        report = WindowReport(
            from_unix,
            to_unix,
            api_total=2,
            materialized_count=2,
            page_count=1,
            violations=["simulated mismatch"] if bad else [],
        )
        return WindowResult(records, report)


def test_retry_then_daily_fallback_yields_disjoint_days_and_preserves_occurrences():
    client = WindowClient(month_failures=2)
    rejected = []
    results = list(
        iter_verified_scrobble_windows(
            lastfm=client, from_unix=1, to_unix=86_401, on_rejected=rejected.append
        )
    )
    assert client.calls == [(1, 86_401), (1, 86_401), (86_400, 86_401), (1, 86_399)]
    assert len(rejected) == 2
    assert len(results) == 2
    assert sum(len(result.scrobbles) for result in results) == 4


def test_retry_recovers_without_daily_fallback():
    client = WindowClient(month_failures=1)
    results = list(
        iter_verified_scrobble_windows(lastfm=client, from_unix=1, to_unix=86_401)
    )
    assert len(client.calls) == 2
    assert len(results) == 1


def test_iteration_stops_for_consumer_checkpoint_before_next_request():
    client = WindowClient()
    iterator = iter_verified_scrobble_windows(
        lastfm=client, from_unix=0, to_unix=2_678_400
    )
    first = next(iterator)
    assert client.calls == [(2_678_400, 2_678_400)]
    assert first.report.ok
    assert len(list(iterator)) == 1


def test_failing_day_raises_without_yielding_invalid_records():
    client = WindowClient(bad_day=1)
    iterator = iter_verified_scrobble_windows(
        lastfm=client, from_unix=1, to_unix=86_401, period="day"
    )
    assert next(iterator).report.from_unix == 86_400
    with pytest.raises(ScrobbleIntegrityError) as caught:
        next(iterator)
    assert caught.value.report.from_unix == 1


def test_transport_failure_does_not_start_integrity_fallback():
    error = HttpRequestError("GET", "", retryable=True)
    client = WindowClient(error=error)
    with pytest.raises(HttpRequestError) as caught:
        list(iter_verified_scrobble_windows(lastfm=client, from_unix=1, to_unix=86_401))
    assert caught.value is error
    assert len(client.calls) == 1


def test_rejection_persistence_failure_stops_before_more_requests():
    client = WindowClient(month_failures=2)

    def reject(report):
        raise OSError("disk unavailable")

    with pytest.raises(OSError):
        list(
            iter_verified_scrobble_windows(
                lastfm=client, from_unix=1, to_unix=86_401, on_rejected=reject
            )
        )
    assert len(client.calls) == 1


@pytest.mark.parametrize("size", [0, 201, True])
def test_invalid_page_size_never_requests_data(size):
    client = WindowClient()
    with pytest.raises(ValueError):
        list(
            iter_verified_scrobble_windows(
                lastfm=client, from_unix=0, to_unix=1, page_size=size
            )
        )
    assert not client.calls


class PagedHttp:
    def __init__(self, records, *, replay=False):
        self.records = records
        self.replay = replay
        self.calls = []

    def get_response(self, url, *, params, **kwargs):
        self.calls.append(params)
        size = params["limit"]
        start = (params["page"] - 1) * size
        page = self.records[start : start + size]
        if self.replay and params["page"] > 1:
            page = self.records[:size]
        return JsonResponse(
            {
                "recenttracks": {
                    "track": page,
                    "@attr": {
                        "total": str(len(self.records)),
                        "totalPages": str((len(self.records) + size - 1) // size),
                    },
                }
            },
            200,
            1,
        )


def track(timestamp, name="Song"):
    return {
        "artist": {"#text": "Artist"},
        "name": name,
        "date": {"uts": str(timestamp)},
    }


@pytest.mark.parametrize("size", [1, 2, 199, 200])
@pytest.mark.parametrize("period", ["day", "month"])
def test_overlap_retry_preserves_occurrences_with_a_different_page_size(size, period):
    records = [track(1000 - index) for index in range(size - 1)]
    records += [track(50, "Repeated"), track(50, "Repeated"), track(1)]
    http = PagedHttp(records)
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    rejected = []
    windows = list(
        iter_verified_scrobble_windows(
            lastfm=client,
            from_unix=0,
            to_unix=2000,
            page_size=size,
            period=period,
            on_rejected=rejected.append,
        )
    )
    assert len(windows) == 1
    result = windows[0]
    assert result.report.ok
    assert len(result.scrobbles) == len(records)
    assert sum(r.track_name == "Repeated" for r in result.scrobbles) == 2
    assert len(rejected) == 1
    assert {call["limit"] for call in http.calls} == {
        size,
        size - 1 if size == 200 else size + 1,
    }


def test_repeated_pages_are_not_accepted_after_alternate_page_size_retry():
    http = PagedHttp([track(200 - index) for index in range(8)], replay=True)
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    rejected = []
    with pytest.raises(ScrobbleIntegrityError):
        list(
            iter_verified_scrobble_windows(
                lastfm=client,
                from_unix=0,
                to_unix=2000,
                page_size=2,
                period="day",
                on_rejected=rejected.append,
            )
        )
    assert len(rejected) == 2
    assert {call["limit"] for call in http.calls} == {2, 3}


def test_monthly_overlap_failure_still_falls_back_to_daily_windows():
    class OverlapClient(WindowClient):
        def __init__(self):
            super().__init__()
            self.sizes = []

        def fetch_recent_tracks_window(self, **kwargs):
            self.sizes.append(kwargs["page_size"])
            result = super().fetch_recent_tracks_window(**kwargs)
            if kwargs["to_unix"] - kwargs["from_unix"] >= 86_400:
                result.report.violations.append("exact raw-payload overlap at page 2")
            return result

    client = OverlapClient()
    windows = list(
        iter_verified_scrobble_windows(lastfm=client, from_unix=1, to_unix=86_401)
    )
    assert len(windows) == 2
    assert client.sizes == [200, 199, 200, 200]


def test_legacy_daily_collector_preserves_duplicates_after_overlap_retry():
    http = PagedHttp([track(200), track(100), track(100), track(50)])
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    progress = []
    records, reports = collect_verified_scrobbles(
        lastfm=client,
        from_unix=0,
        to_unix=2000,
        page_size=2,
        on_window_complete=progress.append,
    )
    assert len(records) == 4
    assert sum(record.timestamp_unix == 100 for record in records) == 2
    assert len(reports) == len(progress) == 1
    assert reports[0].ok
    assert {call["limit"] for call in http.calls} == {2, 3}


def test_legacy_daily_collector_still_rejects_replayed_pages():
    http = PagedHttp([track(200 - index) for index in range(8)], replay=True)
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    records, reports = collect_verified_scrobbles(
        lastfm=client,
        from_unix=0,
        to_unix=2000,
        page_size=2,
    )
    assert not records
    assert not reports[0].ok
    assert {call["limit"] for call in http.calls} == {2, 3}
