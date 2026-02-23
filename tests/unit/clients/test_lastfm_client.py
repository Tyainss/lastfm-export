from typing import Any, Dict, List

import pytest

from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.models import Scrobble


class _FakeHttp:
    def __init__(self, pages: List[Dict[str, Any]]) -> None:
        self._pages = pages
        self.calls: List[Dict[str, Any]] = []

    def get_json(self, url: str, *, params=None, headers=None) -> Dict[str, Any]:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self._pages:
            raise AssertionError("No more fake pages configured")
        return self._pages.pop(0)


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