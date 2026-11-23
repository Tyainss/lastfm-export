from typing import Any, Dict, List
from datetime import datetime, timezone

import pytest
import responses

from lastfm_export.clients.lastfm import LastFMClient, _page_overlap
from lastfm_export.clients.http import HttpClient, JsonResponse, RetryConfig
from lastfm_export.errors import (
    ConfigError,
    HttpRequestError,
    LastFMRecentTracksAccessError,
)
from lastfm_export.models import Scrobble


class _FakeHttp:
    def __init__(self, pages: List[Dict[str, Any]]) -> None:
        self._pages = pages
        self.calls: List[Dict[str, Any]] = []

    def get_response(self, url: str, *, params=None, headers=None, **kwargs):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self._pages:
            raise AssertionError("No more fake pages configured")
        return JsonResponse(self._pages.pop(0), 200, 1)


class _FailingHttp:
    def __init__(self, error: HttpRequestError) -> None:
        self._error = error

    def get_response(self, url: str, **kwargs):
        raise self._error


def test_iter_recent_tracks_skips_nowplaying_and_parses_scrobbles():
    page_1 = {
        "recenttracks": {
            "@attr": {"page": "1", "totalPages": "1"},
            "track": [
                {
                    "@attr": {"nowplaying": "true"},
                    "artist": {"#text": "Artist A"},
                    "name": "Track A",
                },
                {
                    "artist": {"#text": "Artist B"},
                    "name": "Track B",
                    "album": {"#text": "Album B"},
                    "date": {"uts": "1700000000"},
                    "mbid": "",
                },
            ],
        }
    }

    http = _FakeHttp([page_1])
    client = LastFMClient(api_key="k", username="u", user_agent="ua", http=http)

    out = list(client.iter_recent_tracks(page_size=200))

    assert len(out) == 1
    assert isinstance(out[0], Scrobble)
    assert out[0].artist_name == "Artist B"
    assert out[0].track_name == "Track B"
    assert out[0].album_name == "Album B"
    assert out[0].timestamp_unix == 1700000000
    assert out[0].mbid is None


def test_iter_recent_tracks_handles_single_track_dict_payload():
    page_1 = {
        "recenttracks": {
            "@attr": {"page": "1", "totalPages": "1"},
            "track": {
                "artist": {"#text": "Artist"},
                "name": "Track",
                "album": {"#text": ""},
                "date": {"uts": "1700000001"},
                "mbid": "abc",
            },
        }
    }

    http = _FakeHttp([page_1])
    client = LastFMClient(api_key="k", username="u", user_agent="ua", http=http)

    out = list(client.iter_recent_tracks())

    assert len(out) == 1
    assert out[0].artist_name == "Artist"
    assert out[0].album_name is None
    assert out[0].mbid == "abc"


def test_iter_recent_tracks_stops_at_total_pages():
    page_1 = {
        "recenttracks": {
            "@attr": {"page": "1", "totalPages": "2"},
            "track": [
                {
                    "artist": {"#text": "A1"},
                    "name": "T1",
                    "album": {"#text": "AL1"},
                    "date": {"uts": "1700000001"},
                }
            ],
        }
    }
    page_2 = {
        "recenttracks": {
            "@attr": {"page": "2", "totalPages": "2"},
            "track": [
                {
                    "artist": {"#text": "A2"},
                    "name": "T2",
                    "album": {"#text": "AL2"},
                    "date": {"uts": "1700000002"},
                }
            ],
        }
    }

    http = _FakeHttp([page_1, page_2])
    client = LastFMClient(api_key="k", username="u", user_agent="ua", http=http)

    out = list(client.iter_recent_tracks())

    assert [s.track_name for s in out] == ["T1", "T2"]
    assert len(http.calls) == 2
    assert http.calls[0]["params"]["page"] == 1
    assert http.calls[1]["params"]["page"] == 2


def test_iter_recent_tracks_respects_page_limit():
    page_1 = {
        "recenttracks": {
            "@attr": {"page": "1", "totalPages": "99"},
            "track": [
                {
                    "artist": {"#text": "A1"},
                    "name": "T1",
                    "album": {"#text": "AL1"},
                    "date": {"uts": "1700000001"},
                }
            ],
        }
    }
    page_2 = {
        "recenttracks": {
            "@attr": {"page": "2", "totalPages": "99"},
            "track": [
                {
                    "artist": {"#text": "A2"},
                    "name": "T2",
                    "album": {"#text": "AL2"},
                    "date": {"uts": "1700000002"},
                }
            ],
        }
    }

    http = _FakeHttp([page_1, page_2])
    client = LastFMClient(api_key="k", username="u", user_agent="ua", http=http)

    out = list(client.iter_recent_tracks(page_limit=1))

    assert [s.track_name for s in out] == ["T1"]
    assert len(http.calls) == 1


def test_iter_recent_tracks_requires_positive_page_size():
    http = _FakeHttp([])
    client = LastFMClient(api_key="k", username="u", user_agent="ua", http=http)

    with pytest.raises(ValueError):
        list(client.iter_recent_tracks(page_size=0))


def test_iter_recent_tracks_raises_access_error_when_listening_history_is_hidden():
    http_error = HttpRequestError(
        method="GET",
        url="https://ws.audioscrobbler.com/2.0/",
        status_code=403,
        message="Non-success response",
        payload={
            "error": 17,
            "message": "Login required",
        },
    )
    client = LastFMClient(
        api_key="k",
        username="u",
        user_agent="ua",
        http=_FailingHttp(http_error),
    )

    with pytest.raises(
        LastFMRecentTracksAccessError,
        match="Hide recent listening information",
    ):
        list(client.iter_recent_tracks())


def test_iter_recent_tracks_preserves_unrelated_http_error():
    http_error = HttpRequestError(
        method="GET",
        url="https://ws.audioscrobbler.com/2.0/",
        status_code=403,
        message="Non-success response",
        payload={
            "error": 10,
            "message": "Invalid API key",
        },
    )
    client = LastFMClient(
        api_key="k",
        username="u",
        user_agent="ua",
        http=_FailingHttp(http_error),
    )

    with pytest.raises(HttpRequestError) as exc:
        list(client.iter_recent_tracks())

    assert exc.value is http_error


def _window_track(timestamp: int, name: str) -> Dict[str, Any]:
    return {
        "artist": {"#text": "Artist"},
        "name": name,
        "album": {"#text": "Album"},
        "date": {"uts": str(timestamp)},
    }


def test_fetch_window_detects_known_cross_page_replay():
    replay = _window_track(9, "Replay")
    page_1 = {
        "recenttracks": {
            "@attr": {"page": "1", "totalPages": "2", "total": "2"},
            "track": [_window_track(10, "First"), replay],
        }
    }
    page_2 = {
        "recenttracks": {
            "@attr": {"page": "2", "totalPages": "2", "total": "2"},
            "track": [replay],
        }
    }
    client = LastFMClient(
        api_key="k", username="u", user_agent="ua", http=_FakeHttp([page_1, page_2])
    )

    result = client.fetch_recent_tracks_window(from_unix=0, to_unix=10, page_size=2)

    assert len(result.scrobbles) == 3
    assert any("overlap" in violation for violation in result.report.violations)
    assert any("differs" in violation for violation in result.report.violations)


def test_fetch_window_detects_total_and_timestamp_violations():
    page = {
        "recenttracks": {
            "@attr": {"page": "1", "totalPages": "1", "total": "3"},
            "track": [
                _window_track(5, "Old"),
                _window_track(6, "New"),
                _window_track(11, "Outside"),
            ],
        }
    }
    client = LastFMClient(
        api_key="k", username="u", user_agent="ua", http=_FakeHttp([page])
    )

    result = client.fetch_recent_tracks_window(from_unix=0, to_unix=10)

    assert any("outside" in violation for violation in result.report.violations)
    assert any("reversal" in violation for violation in result.report.violations)


def test_get_user_registration_unix_reads_lastfm_registered_timestamp():
    http = _FakeHttp([{"user": {"registered": {"unixtime": "123"}}}])
    client = LastFMClient(api_key="k", username="u", user_agent="ua", http=http)

    assert client.get_user_registration_unix() == 123


def test_verified_day_windows_include_final_seconds_without_overlap():
    from lastfm_export.pipelines.lastfm_export import collect_verified_scrobbles

    timestamps = [172_799, 86_400, 86_399, 86_399, 0]

    class RangeHttp:
        def get_response(self, url, *, params, **kwargs):
            # Model the API's inclusive `from` and exclusive `to`.
            tracks = [
                _window_track(ts, str(ts))
                for ts in timestamps
                if params["from"] <= ts < params["to"]
            ]
            return JsonResponse(
                {
                    "recenttracks": {
                        "@attr": {"total": str(len(tracks)), "totalPages": "1"},
                        "track": tracks,
                    }
                },
                200,
                1,
            )

    client = LastFMClient(
        api_key="k", username="u", user_agent="tests", http=RangeHttp()
    )
    records, reports = collect_verified_scrobbles(
        lastfm=client, from_unix=0, to_unix=172_799
    )
    assert [record.timestamp_unix for record in records] == timestamps
    assert all(report.ok for report in reports)
    single = client.fetch_recent_tracks_window(from_unix=86_400, to_unix=86_400)
    assert single.report.ok
    assert [record.timestamp_unix for record in single.scrobbles] == [86_400]


@responses.activate
@pytest.mark.parametrize("code", [8, 11, 16, 29])
@pytest.mark.parametrize("operation", ["info", "history"])
def test_user_api_errors_are_retried_even_with_http_200(code, operation):
    url = "https://ws.audioscrobbler.com/2.0/"
    responses.get(url, json={"error": code, "message": "Temporary failure"})
    payload = (
        {"user": {"registered": {"unixtime": "1"}}}
        if operation == "info"
        else {"recenttracks": {"@attr": {"totalPages": "1"}, "track": []}}
    )
    responses.get(url, json=payload)
    client = LastFMClient(
        api_key="key",
        username="u",
        user_agent="tests",
        http=HttpClient(
            user_agent="tests",
            retry=RetryConfig(
                max_attempts=2,
                backoff_base_secs=0,
                jitter_ratio=0,
            ),
        ),
    )
    if operation == "info":
        assert client.get_user_registration_unix() == 1
    else:
        assert list(client.iter_recent_tracks()) == []
    assert len(responses.calls) == 2


@responses.activate
def test_user_invalid_key_is_fatal_and_redacted_without_retry():
    secret = "private-api-key"
    responses.get(
        "https://ws.audioscrobbler.com/2.0/",
        json={
            "error": 10,
            "message": f"Invalid key {secret}",
            "api_key": secret,
        },
    )
    client = LastFMClient(api_key=secret, username="u", user_agent="tests")
    with pytest.raises(HttpRequestError) as caught:
        client.get_user_info()
    assert caught.value.fatal
    assert caught.value.provider_code == 10
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.payload)
    assert len(responses.calls) == 1


@responses.activate
def test_hidden_history_error_is_recognized_with_http_200():
    responses.get("https://ws.audioscrobbler.com/2.0/", json={"error": 17})
    client = LastFMClient(api_key="key", username="u", user_agent="tests")
    with pytest.raises(LastFMRecentTracksAccessError):
        list(client.iter_recent_tracks())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"recenttracks": {}},
        {"recenttracks": {"track": [None]}},
        {"recenttracks": {"track": [], "@attr": None}},
        {"recenttracks": {"track": [], "@attr": {"user": "other"}}},
    ],
)
def test_invalid_history_cannot_be_mistaken_for_empty_success(payload):
    client = LastFMClient(
        api_key="key", username="u", user_agent="tests", http=_FakeHttp([payload])
    )
    with pytest.raises(HttpRequestError):
        list(client.iter_recent_tracks())


def test_reversed_window_is_rejected_before_requesting():
    http = _FakeHttp([])
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    with pytest.raises(ValueError):
        client.fetch_recent_tracks_window(from_unix=2, to_unix=1)
    assert not http.calls


@pytest.mark.parametrize(
    "previous,current,expected",
    [
        ([{"a": 1}, {"b": 2}], [{"b": 2}, {"c": 3}], True),
        ([{"a": 1}, {"b": 2}], [{"a": 1}, {"c": 3}], False),
        ([{"a": 1, "b": 2}], [{"b": 2, "a": 1}], True),
        ([], [{"a": 1}], False),
    ],
)
def test_page_overlap_preserves_suffix_prefix_matching(previous, current, expected):
    assert _page_overlap(previous, current) is expected


@pytest.mark.parametrize("total,expected", [("0", 100), ("5", 0)])
def test_full_history_bound_checks_for_events_before_registration(total, expected):
    http = _FakeHttp(
        [
            {"user": {"registered": {"unixtime": "100"}}},
            {
                "recenttracks": {
                    "@attr": {"total": total},
                    "track": [_window_track(20, "Older")] if int(total) else [],
                }
            },
        ]
    )
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    assert client.get_user_history_start_unix() == expected
    assert http.calls[1]["params"]["from"] == 0
    assert http.calls[1]["params"]["to"] == 100
    assert http.calls[1]["params"]["limit"] == 1


def test_full_history_bound_does_not_assume_an_omitted_total_is_zero():
    http = _FakeHttp(
        [
            {"user": {"registered": {"unixtime": "100"}}},
            {"recenttracks": {"track": []}},
        ]
    )
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    with pytest.raises(HttpRequestError, match="pre-registration"):
        client.get_user_history_start_unix()


def test_full_history_bound_requires_registration_metadata():
    client = LastFMClient(
        api_key="key", username="u", user_agent="tests", http=_FakeHttp([{"user": {}}])
    )
    with pytest.raises(ConfigError):
        client.get_user_history_start_unix()


def test_full_history_probe_rejects_contradictory_zero_total():
    http = _FakeHttp(
        [
            {"user": {"registered": {"unixtime": "100"}}},
            {
                "recenttracks": {
                    "@attr": {"total": "0"},
                    "track": [_window_track(20, "Older")],
                }
            },
        ]
    )
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    with pytest.raises(HttpRequestError, match="Inconsistent"):
        client.get_user_history_start_unix()


@pytest.mark.parametrize(
    "oldest",
    [
        datetime(2020, 2, 29, tzinfo=timezone.utc),
        datetime(2020, 12, 1, tzinfo=timezone.utc),
        datetime(2020, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
        datetime(1970, 1, 1, tzinfo=timezone.utc),
    ],
)
@pytest.mark.parametrize("bounded", [False, True])
def test_history_start_search_finds_first_month_without_scanning_empty_years(
    oldest, bounded
):
    registered = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    floor = (
        int(datetime(2002, 1, 15, tzinfo=timezone.utc).timestamp()) if bounded else 0
    )
    timestamps = [int(oldest.timestamp()), registered - 100]
    if bounded:
        timestamps.append(100)

    class ProbeHttp:
        def __init__(self):
            self.calls = []

        def get_response(self, url, *, params=None, **kwargs):
            self.calls.append(params)
            if params["method"] == "user.getinfo":
                return JsonResponse(
                    {"user": {"registered": {"unixtime": str(registered)}}}, 200, 1
                )
            assert params["page"] == params["limit"] == 1
            assert params["from"] == floor
            found = [
                timestamp
                for timestamp in timestamps
                if floor <= timestamp < params["to"]
            ]
            return JsonResponse(
                {
                    "recenttracks": {
                        "@attr": {"total": str(len(found))},
                        "track": [_window_track(max(found), "Song")] if found else [],
                    }
                },
                200,
                1,
            )

    http = ProbeHttp()
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    first = datetime.fromtimestamp(
        min(t for t in timestamps if t >= floor), timezone.utc
    )
    expected = max(
        floor,
        int(datetime(first.year, first.month, 1, tzinfo=timezone.utc).timestamp()),
    )
    assert client.get_user_history_start_unix(min_unix=floor) == expected
    assert len(http.calls) <= 12


@pytest.mark.parametrize("shifted", [False, True])
def test_bounded_oldest_search_checks_skipped_prefix_and_falls_back(shifted):
    floor = 1_010_000_000
    oldest = floor + 200_000
    dates = list(range(oldest + 200, oldest - 1, -1))

    class ProbeHttp:
        def __init__(self):
            self.calls = []

        def get_response(self, url, *, params, **kwargs):
            self.calls.append(params)
            assert params["from"] == floor
            page, size = params["page"], params["limit"]
            found = [timestamp for timestamp in dates if timestamp < params["to"]]
            selected = found[(page - 1) * size : page * size]
            if shifted and page == 2:
                selected = [oldest + 1]
            return JsonResponse(
                {
                    "recenttracks": {
                        "@attr": {
                            "total": str(len(found)),
                            "totalPages": str((len(found) + size - 1) // size),
                            "page": str(page),
                        },
                        "track": [_window_track(t, "Song") for t in selected],
                    }
                },
                200,
                1,
            )

    http = ProbeHttp()
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    start = client.find_oldest_scrobble_unix(from_unix=floor, to_unix=oldest + 300)
    assert oldest - 86_399 <= start <= oldest
    assert [p["page"] for p in http.calls[:3]] == [1, 2, 1]
    if not shifted:
        assert start == oldest
        assert len(http.calls) == 3
    else:
        assert len(http.calls) > 3


def test_history_search_rejects_inconsistent_mid_search_response():
    registered = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    http = _FakeHttp(
        [
            {"user": {"registered": {"unixtime": str(registered)}}},
            {
                "recenttracks": {
                    "@attr": {"total": "1"},
                    "track": [_window_track(registered - 1, "Old")],
                }
            },
            {"recenttracks": {"@attr": {"total": "1"}, "track": []}},
        ]
    )
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    with pytest.raises(HttpRequestError, match="Inconsistent"):
        client.get_user_history_start_unix()


def test_history_probe_rejects_events_outside_requested_interval():
    http = _FakeHttp(
        [
            {"user": {"registered": {"unixtime": "100"}}},
            {
                "recenttracks": {
                    "@attr": {"total": "1"},
                    "track": [_window_track(100, "Out of range")],
                }
            },
        ]
    )
    client = LastFMClient(api_key="key", username="u", user_agent="tests", http=http)
    with pytest.raises(HttpRequestError, match="Inconsistent"):
        client.get_user_history_start_unix()


def test_zero_registration_is_rejected_instead_of_scanning_from_epoch():
    client = LastFMClient(
        api_key="key",
        username="u",
        user_agent="tests",
        http=_FakeHttp(
            [
                {"user": {"registered": {"unixtime": "0"}}},
            ]
        ),
    )
    with pytest.raises(ConfigError):
        client.get_user_history_start_unix()
