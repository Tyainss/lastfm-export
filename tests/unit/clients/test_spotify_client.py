from typing import Any, Dict, List


from lastfm_export.clients.spotify import SpotifyClient
from lastfm_export.models import SpotifyTrackEnrichment


class _FakeTokenResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if not (200 <= self.status_code <= 299):
            raise RuntimeError("bad status")

    def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeHttp:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: List[Dict[str, Any]] = []

    def get_json(self, url: str, *, params=None, headers=None) -> Dict[str, Any]:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self._responses:
            raise AssertionError("No more fake responses configured")
        return self._responses.pop(0)


def test_search_track_first_sets_bearer_header(monkeypatch):
    http = _FakeHttp(
        [
            {
                "tracks": {
                    "items": [
                        {"id": "t1", "artists": [{"id": "a1"}], "album": {"id": "al1"}, "external_urls": {"spotify": "u"}}
                    ]
                }
            }
        ]
    )

    client = SpotifyClient(client_id="id", client_secret="sec", user_agent="ua", http=http)

    monkeypatch.setattr(
        client._token_session,
        "post",
        lambda *args, **kwargs: _FakeTokenResponse({"access_token": "TOKEN", "expires_in": 3600}),
    )

    item = client.search_track_first(track_name="Song", artist_name="Artist")
    assert item is not None

    assert len(http.calls) == 1
    auth = http.calls[0]["headers"]["Authorization"]
    assert auth == "Bearer TOKEN"


def test_token_is_cached(monkeypatch):
    http = _FakeHttp(
        [
            {"tracks": {"items": [{"id": "t1"}]}},
            {"tracks": {"items": [{"id": "t2"}]}},
        ]
    )
    client = SpotifyClient(client_id="id", client_secret="sec", user_agent="ua", http=http)

    post_calls = {"n": 0}

    def fake_post(*args, **kwargs):
        post_calls["n"] += 1
        return _FakeTokenResponse({"access_token": "TOKEN", "expires_in": 3600})

    monkeypatch.setattr(client._token_session, "post", fake_post)

    _ = client.search_track_first(track_name="Song1", artist_name="Artist")
    _ = client.search_track_first(track_name="Song2", artist_name="Artist")

    assert post_calls["n"] == 1


def test_build_track_enrichment_maps_fields(monkeypatch):
    http = _FakeHttp(
        [
            {
                "tracks": {
                    "items": [
                        {
                            "id": "track_id",
                            "artists": [{"id": "artist_id"}],
                            "album": {"id": "album_id"},
                            "external_urls": {"spotify": "https://open.spotify.com/track/track_id"},
                            "popularity": 42,
                        }
                    ]
                }
            }
        ]
    )

    client = SpotifyClient(client_id="id", client_secret="sec", user_agent="ua", http=http)
    monkeypatch.setattr(
        client._token_session,
        "post",
        lambda *args, **kwargs: _FakeTokenResponse({"access_token": "TOKEN", "expires_in": 3600}),
    )

    enr = client.build_track_enrichment(track_name="Song", artist_name="Artist")
    assert isinstance(enr, SpotifyTrackEnrichment)
    assert enr.spotify_track_id == "track_id"
    assert enr.spotify_artist_id == "artist_id"
    assert enr.spotify_album_id == "album_id"
    assert enr.spotify_track_url == "https://open.spotify.com/track/track_id"
    assert enr.popularity == 42


def test_clean_artist_name_basic_cases():
    # internal helper, but behavior matters for consistent search queries
    assert SpotifyClient._clean_artist_name("Artist feat. Someone") == "Artist"
    assert SpotifyClient._clean_artist_name("Artist Ft. Someone") == "Artist"
    assert SpotifyClient._clean_artist_name("Artist [Live]") == "Artist"
    assert SpotifyClient._clean_artist_name("Artist X Other") == "Artist"
    assert SpotifyClient._clean_artist_name("Artist") == "Artist"