from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Scrobble:
    """
    A single Last.fm scrobble.

    timestamp_unix:
      - Unix timestamp in seconds (int)
      - For "now playing" items, Last.fm may not provide a timestamp; those should be skipped upstream.

    raw:
      - Original per-track payload returned by Last.fm, when available.
    """

    artist_name: str
    track_name: str
    album_name: str | None
    timestamp_unix: int
    mbid: str | None = None
    raw: dict[str, Any] | None = None

    def to_record(self, *, include_raw: bool = False) -> dict[str, Any]:
        record = {
            "artist_name": self.artist_name,
            "track_name": self.track_name,
            "album_name": self.album_name,
            "timestamp_unix": self.timestamp_unix,
            "mbid": self.mbid,
        }
        if include_raw:
            record["raw"] = self.raw
        return record


@dataclass(frozen=True, slots=True)
class SpotifyTrackEnrichment:
    """
    Minimal Spotify metadata for a track lookup.

    `raw` contains the original per-track payload returned by Spotify, when available.
    """

    spotify_track_id: str
    spotify_artist_id: str | None
    spotify_album_id: str | None
    spotify_track_url: str | None
    popularity: int | None = None
    raw: dict[str, Any] | None = None

    def to_record(self, *, include_raw: bool = False) -> dict[str, Any]:
        record = {
            "spotify_track_id": self.spotify_track_id,
            "spotify_artist_id": self.spotify_artist_id,
            "spotify_album_id": self.spotify_album_id,
            "spotify_track_url": self.spotify_track_url,
            "popularity": self.popularity,
        }
        if include_raw:
            record["raw"] = self.raw
        return record


@dataclass(frozen=True, slots=True)
class EnrichedScrobble:
    scrobble: Scrobble
    spotify: SpotifyTrackEnrichment | None

    def to_record(self, *, include_raw: bool = False) -> dict[str, Any]:
        base = self.scrobble.to_record(include_raw=include_raw)
        if self.spotify is None:
            base["spotify"] = None
        else:
            base["spotify"] = self.spotify.to_record(include_raw=include_raw)
        return base


@dataclass(frozen=True, slots=True)
class ArtistInput:
    """Caller-supplied name and optional artist MBID; no identity inference."""

    artist_name: str
    mbid: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artist_name, str) or not self.artist_name.strip():
            raise ValueError("artist_name must be a nonblank string")
        if self.mbid is not None:
            if not isinstance(self.mbid, str):
                raise ValueError("mbid must be an artist UUID string")
            try:
                valid = str(UUID(self.mbid)) == self.mbid.lower()
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("mbid must be an artist UUID string")

    def to_record(self) -> dict[str, Any]:
        return {"artist_name": self.artist_name, "mbid": self.mbid}


@dataclass(frozen=True, slots=True)
class ArtistMetadataResponse:
    """Provider outcome without an assertion about artist identity or tag quality."""

    status: str
    raw_response: Any
    http_status: int
    wire_attempts: int


@dataclass(frozen=True, slots=True)
class ArtistMetadataResult:
    schema_version: int
    collection_id: str
    request_id: str
    attempt: int
    input_artist: dict[str, Any]
    provider: str
    endpoint: str
    base_url: str
    request_params: dict[str, Any]
    started_at: str
    collected_at: str
    status: str
    http_status: int | None
    provider_code: int | None
    wire_attempts: int
    raw_response: Any
    error: dict[str, Any] | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)
