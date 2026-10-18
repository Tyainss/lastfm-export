import time

import pytest
import responses

from lastfm_export.clients.http import HttpClient, RetryConfig
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.clients.musicbrainz import (
    MusicBrainzClient,
    _RequestPacer,
    artist_search_params,
)
from lastfm_export.errors import ConfigError, HttpRequestError

MBID = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
MB_URL = "https://musicbrainz.org/ws/2/artist"
USER_AGENT = "metadata-tests/1.0 (https://example.com)"


def http(attempts=3):
    return HttpClient(
        user_agent=USER_AGENT,
        retry=RetryConfig(max_attempts=attempts, backoff_base_secs=0, jitter_ratio=0),
    )


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("lastfm_export.clients.musicbrainz._pacer.wait", lambda: None)


@responses.activate
def test_lastfm_artist_requests_use_mbid_and_preserve_source_fields():
    payload = {
        "similarartists": {
            "artist": {"name": "Related", "match": "0.734", "mbid": MBID},
            "@attr": {"artist": "A"},
        }
    }
    responses.get(LASTFM_URL, json=payload)
    result = LastFMClient(
        api_key="private-api-key", user_agent="tests"
    ).get_similar_artists(artist_name="A", mbid=MBID, limit=17)
    assert result.raw_response == payload
    assert result.status == "success"
    assert result.http_status == 200
    assert result.wire_attempts == 1
    params = responses.calls[0].request.params
    assert params["mbid"] == MBID
    assert "artist" not in params
    assert params["autocorrect"] == "0"
    assert params["limit"] == "17"


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"toptags": {"tag": []}}, "empty"),
        ({"toptags": {"tag": {"name": "rock", "count": 100}}}, "success"),
    ],
)
@responses.activate
def test_lastfm_empty_and_singleton_lists(payload, expected):
    responses.get(LASTFM_URL, json=payload)
    result = LastFMClient(
        api_key="private-api-key", user_agent="tests"
    ).get_artist_top_tags(artist_name="A")
    assert result.status == expected
    assert result.raw_response == payload


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"toptags": {}},
        {"toptags": {"tag": [None]}},
        {"toptags": {"tag": {}}},
        [],
        None,
    ],
)
@responses.activate
def test_lastfm_malformed_is_not_empty(payload):
    responses.get(
        LASTFM_URL,
        body=__import__("json").dumps(payload),
        content_type="application/json",
    )
    with pytest.raises(HttpRequestError, match="Malformed artist response"):
        LastFMClient(api_key="private-api-key", user_agent="tests").get_artist_top_tags(
            artist_name="A"
        )


@responses.activate
def test_http_and_lastfm_provider_retries_share_one_budget(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    responses.get(LASTFM_URL, status=503, json={"error": 11})
    responses.get(LASTFM_URL, json={"error": 29, "message": "Slow down"})
    responses.get(LASTFM_URL, json={"error": 16, "message": "Temporary"})
    responses.get(LASTFM_URL, json={"toptags": {"tag": []}})
    with pytest.raises(HttpRequestError) as caught:
        LastFMClient(
            api_key="private-api-key", user_agent="tests", http=http()
        ).get_artist_top_tags(artist_name="A")
    assert len(responses.calls) == 3
    assert caught.value.provider_code == 16
    assert caught.value.payload == {"error": 16, "message": "Temporary"}
    assert caught.value.attempts == 3
    assert caught.value.retryable


@responses.activate
def test_lastfm_transient_retries_then_success():
    responses.get(LASTFM_URL, json={"error": 11})
    responses.get(LASTFM_URL, json={"toptags": {"tag": []}})
    result = LastFMClient(
        api_key="private-api-key", user_agent="tests", http=http()
    ).get_artist_top_tags(artist_name="A")
    assert result.wire_attempts == 2


@responses.activate
def test_permanent_error_is_not_retried_and_credentials_are_redacted():
    secret = "private-api-key"
    responses.get(
        LASTFM_URL,
        json={"error": 10, "message": f"Invalid key {secret}", "api_key": secret},
    )
    with pytest.raises(HttpRequestError) as caught:
        LastFMClient(api_key=secret, user_agent="tests").get_artist_top_tags(
            artist_name="A"
        )
    assert caught.value.fatal
    assert caught.value.provider_code == 10
    assert len(responses.calls) == 1
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.payload)


@pytest.mark.parametrize(
    "operation", ["get_user_info", "iter_recent_tracks", "fetch_recent_tracks_window"]
)
def test_user_operations_require_username(operation):
    client = LastFMClient(api_key="key", user_agent="tests")
    with pytest.raises(ConfigError, match="username"):
        if operation == "iter_recent_tracks":
            list(client.iter_recent_tracks())
        elif operation == "fetch_recent_tracks_window":
            client.fetch_recent_tracks_window(from_unix=1, to_unix=2)
        else:
            client.get_user_info()


def test_musicbrainz_search_escapes_literal_name():
    params = artist_search_params('A+B AND "C" (live):x\\y')
    assert params["query"] == 'artist:"A\\+B AND \\"C\\" \\(live\\)\\:x\\\\y"'
    assert params["limit"] == 25


@responses.activate
def test_musicbrainz_lookup_preserves_partial_dates_and_empty_tags():
    payload = {
        "id": MBID,
        "name": "A",
        "life-span": {"begin": "1978"},
        "genres": [],
        "tags": [],
    }
    responses.get(f"{MB_URL}/{MBID}", json=payload)
    result = MusicBrainzClient(user_agent=USER_AGENT).get_artist(MBID)
    assert result.status == "success"
    assert result.raw_response == payload
    assert responses.calls[0].request.params["inc"] == "genres+tags+aliases"
    assert responses.calls[0].request.headers["User-Agent"] == USER_AGENT


@responses.activate
def test_musicbrainz_search_returns_every_candidate_without_selection():
    payload = {
        "artists": [
            {"id": MBID, "name": "A", "score": 100},
            {"id": MBID, "name": "Other", "score": 50},
        ]
    }
    responses.get(MB_URL, json=payload)
    result = MusicBrainzClient(user_agent=USER_AGENT).search_artists("A")
    assert result.raw_response == payload
    assert result.status == "success"


@pytest.mark.parametrize(
    "agent", ["app", "app/1.0", "app/1.0 (no contact)", "app/1.0 (a@b)\nInjected: x"]
)
def test_musicbrainz_requires_identifying_user_agent(agent):
    with pytest.raises(ConfigError):
        MusicBrainzClient(user_agent=agent)


@responses.activate
def test_musicbrainz_pacing_is_shared_and_includes_retries(monkeypatch):
    clock = [0.0]
    starts = []

    def sleep(delay):
        clock[0] += delay

    pacer = _RequestPacer()
    monkeypatch.setattr("lastfm_export.clients.musicbrainz._pacer", pacer)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", sleep)

    def callback(request):
        starts.append(clock[0])
        return (503, {}, "{}") if len(starts) == 1 else (200, {}, '{"artists": []}')

    responses.add_callback(
        responses.GET, MB_URL, callback=callback, content_type="application/json"
    )
    MusicBrainzClient(user_agent=USER_AGENT, http=http()).search_artists("A")
    MusicBrainzClient(user_agent=USER_AGENT, http=http()).search_artists("B")
    assert starts == pytest.approx([0, 1.1, 2.2])


@responses.activate
def test_http_rate_limit_exhaustion_preserves_payload_and_status():
    responses.get(
        "https://example.com",
        status=429,
        json={"error": "limited"},
        headers={"Retry-After": "0"},
    )
    with pytest.raises(HttpRequestError) as caught:
        http(2).get_json("https://example.com")
    assert caught.value.status_code == 429
    assert caught.value.payload == {"error": "limited"}
    assert caught.value.attempts == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"artists": [{"id": "bad-id", "name": "A"}]},
        {"artists": {}},
        {},
    ],
)
@responses.activate
def test_musicbrainz_malformed_search_is_not_empty(payload):
    responses.get(MB_URL, json=payload)
    with pytest.raises(HttpRequestError, match="Malformed artist response"):
        MusicBrainzClient(user_agent=USER_AGENT).search_artists("A")


def test_direct_musicbrainz_lookup_rejects_missing_mbid():
    with pytest.raises(ValueError, match="requires an MBID"):
        MusicBrainzClient(user_agent=USER_AGENT).get_artist(None)


@responses.activate
def test_musicbrainz_search_empty_result():
    responses.get(MB_URL, json={"artists": [], "count": 0})
    result = MusicBrainzClient(user_agent=USER_AGENT).search_artists("Unknown")
    assert result.status == "empty"


def test_artist_request_rejects_credentials_in_base_url():
    with pytest.raises(ConfigError, match="without credentials"):
        LastFMClient(
            api_key="key",
            user_agent="tests",
            base_url="https://user:secret@example.com",
        ).get_artist_top_tags(artist_name="A")


@responses.activate
def test_transport_exceptions_do_not_expose_credential_bearing_url():
    import requests

    secret = "private-api-key"
    responses.get(
        LASTFM_URL,
        body=requests.ConnectionError(
            f"Unable to connect to {LASTFM_URL}?api_key={secret}"
        ),
    )
    with pytest.raises(HttpRequestError) as caught:
        LastFMClient(
            api_key=secret, user_agent="tests", http=http(1)
        ).get_artist_top_tags(artist_name="A")
    assert secret not in str(caught.value)
    assert caught.value.payload is None
    assert caught.value.retryable
