from typing import Optional

from lastfm_export.models import EnrichedScrobble, Scrobble, SpotifyTrackEnrichment
from lastfm_export.pipelines.spotify_enrich import enrich_scrobbles_with_spotify


class _FakeSpotifyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._responses: dict[tuple[str, str], Optional[SpotifyTrackEnrichment]] = {}

    def set_response(
        self,
        *,
        artist_name: str,
        track_name: str,
        enrichment: Optional[SpotifyTrackEnrichment],
    ) -> None:
        self._responses[(artist_name, track_name)] = enrichment

    def build_track_enrichment(
        self,
        *,
        track_name: str,
        artist_name: str,
    ) -> Optional[SpotifyTrackEnrichment]:
        self.calls.append((artist_name, track_name))
        return self._responses.get((artist_name, track_name))


def test_enrich_scrobbles_dedupes_lookups_and_reuses_result():
    spotify = _FakeSpotifyClient()
    enrichment = SpotifyTrackEnrichment(
        spotify_track_id="t1",
        spotify_artist_id="a1",
        spotify_album_id="al1",
        spotify_track_url="u1",
        popularity=10,
    )
    spotify.set_response(artist_name="Artist", track_name="Track", enrichment=enrichment)

    scrobbles = [
        Scrobble(artist_name="Artist", track_name="Track", album_name=None, timestamp_unix=1),
        Scrobble(artist_name="Artist", track_name="Track", album_name=None, timestamp_unix=2),
    ]

    out = list(enrich_scrobbles_with_spotify(spotify=spotify, scrobbles=scrobbles, dedupe=True))

    assert len(out) == 2
    assert all(isinstance(x, EnrichedScrobble) for x in out)
    assert spotify.calls == [("Artist", "Track")]
    assert out[0].spotify is enrichment
    assert out[1].spotify is enrichment


def test_enrich_scrobbles_dedupes_misses_too():
    spotify = _FakeSpotifyClient()
    spotify.set_response(artist_name="Artist", track_name="Missing", enrichment=None)

    scrobbles = [
        Scrobble(artist_name="Artist", track_name="Missing", album_name=None, timestamp_unix=1),
        Scrobble(artist_name="Artist", track_name="Missing", album_name=None, timestamp_unix=2),
    ]

    out = list(enrich_scrobbles_with_spotify(spotify=spotify, scrobbles=scrobbles, dedupe=True))

    assert len(out) == 2
    assert spotify.calls == [("Artist", "Missing")]
    assert out[0].spotify is None
    assert out[1].spotify is None


def test_enrich_scrobbles_no_dedupe_calls_every_time():
    spotify = _FakeSpotifyClient()
    enrichment = SpotifyTrackEnrichment(
        spotify_track_id="t1",
        spotify_artist_id=None,
        spotify_album_id=None,
        spotify_track_url=None,
        popularity=None,
    )
    spotify.set_response(artist_name="Artist", track_name="Track", enrichment=enrichment)

    scrobbles = [
        Scrobble(artist_name="Artist", track_name="Track", album_name=None, timestamp_unix=1),
        Scrobble(artist_name="Artist", track_name="Track", album_name=None, timestamp_unix=2),
    ]

    out = list(enrich_scrobbles_with_spotify(spotify=spotify, scrobbles=scrobbles, dedupe=False))

    assert len(out) == 2
    assert spotify.calls == [("Artist", "Track"), ("Artist", "Track")]
    assert out[0].spotify is enrichment
    assert out[1].spotify is enrichment


def test_enrich_scrobbles_normalizes_cache_keys():
    spotify = _FakeSpotifyClient()
    enrichment = SpotifyTrackEnrichment(
        spotify_track_id="t1",
        spotify_artist_id=None,
        spotify_album_id=None,
        spotify_track_url=None,
        popularity=None,
    )
    spotify.set_response(artist_name="  ARTIST  ", track_name="Track", enrichment=enrichment)

    scrobbles = [
        Scrobble(artist_name="  ARTIST  ", track_name="Track", album_name=None, timestamp_unix=1),
        Scrobble(artist_name="artist", track_name="  track  ", album_name=None, timestamp_unix=2),
    ]

    out = list(enrich_scrobbles_with_spotify(spotify=spotify, scrobbles=scrobbles, dedupe=True))

    assert len(out) == 2
    assert spotify.calls == [("  ARTIST  ", "Track")]
    assert out[0].spotify is enrichment
    assert out[1].spotify is enrichment