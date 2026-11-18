import pytest

from lastfm_export.errors import HttpRequestError, ScrobbleIntegrityError
from lastfm_export.integrity import WindowReport, WindowResult
from lastfm_export.models import Scrobble
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
