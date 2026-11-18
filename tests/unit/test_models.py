from lastfm_export.models import (
    EnrichedScrobble,
    LastFMUser,
    Scrobble,
    SpotifyTrackEnrichment,
    UserTopArtist,
)


def test_user_profile_records_exclude_raw_by_default():
    raw = {"name": "Listener"}
    user = LastFMUser(username="Listener", raw=raw)
    assert user.to_record() == {"username": "Listener"}
    assert user.to_record(include_raw=True) == {"username": "Listener", "raw": raw}

    artist = UserTopArtist(artist_name="Artist", playcount=12, rank=1, raw=raw)
    assert artist.to_record() == {
        "artist_name": "Artist",
        "playcount": 12,
        "rank": 1,
        "mbid": None,
    }
    assert artist.to_record(include_raw=True)["raw"] == raw


def test_scrobble_to_record_excludes_raw_by_default():
    scrobble = Scrobble(
        artist_name="Artist",
        track_name="Track",
        album_name="Album",
        timestamp_unix=123,
        mbid="mbid",
        raw={"url": "https://example.com/track"},
    )

    assert scrobble.to_record() == {
        "artist_name": "Artist",
        "track_name": "Track",
        "album_name": "Album",
        "timestamp_unix": 123,
        "mbid": "mbid",
    }


def test_scrobble_to_record_can_include_raw():
    raw = {"url": "https://example.com/track"}
    scrobble = Scrobble(
        artist_name="Artist",
        track_name="Track",
        album_name=None,
        timestamp_unix=123,
        raw=raw,
    )

    assert scrobble.to_record(include_raw=True)["raw"] == raw


def test_enriched_scrobble_to_record_propagates_include_raw():
    lastfm_raw = {"name": "Track"}
    spotify_raw = {"id": "spotify-track"}
    enriched = EnrichedScrobble(
        scrobble=Scrobble(
            artist_name="Artist",
            track_name="Track",
            album_name=None,
            timestamp_unix=123,
            raw=lastfm_raw,
        ),
        spotify=SpotifyTrackEnrichment(
            spotify_track_id="spotify-track",
            spotify_artist_id=None,
            spotify_album_id=None,
            spotify_track_url=None,
            raw=spotify_raw,
        ),
    )

    record = enriched.to_record(include_raw=True)

    assert record["raw"] == lastfm_raw
    assert record["spotify"]["raw"] == spotify_raw
