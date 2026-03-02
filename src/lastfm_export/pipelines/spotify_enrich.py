from typing import Dict, Iterable, Iterator, Optional, Tuple

from lastfm_export.clients.spotify import SpotifyClient
from lastfm_export.models import EnrichedScrobble, Scrobble, SpotifyTrackEnrichment


def enrich_scrobbles_with_spotify(
    *,
    spotify: SpotifyClient,
    scrobbles: Iterable[Scrobble],
    dedupe: bool = True,
) -> Iterator[EnrichedScrobble]:
    """
    Enrich scrobbles with Spotify metadata.

    Args:
        spotify: Configured SpotifyClient.
        scrobbles: Iterable of Scrobble.
        dedupe: If True, caches lookups by (artist_name, track_name).

    Yields:
        EnrichedScrobble, with spotify enrichment or None when not found.
    """
    cache: Dict[Tuple[str, str], Optional[SpotifyTrackEnrichment]] = {}

    for scrobble in scrobbles:
        key = (_norm_key(scrobble.artist_name), _norm_key(scrobble.track_name))

        if dedupe and key in cache:
            enrichment = cache[key]
        else:
            enrichment = spotify.build_track_enrichment(
                track_name=scrobble.track_name,
                artist_name=scrobble.artist_name,
            )
            if dedupe:
                cache[key] = enrichment

        yield EnrichedScrobble(scrobble=scrobble, spotify=enrichment)


def _norm_key(value: str) -> str:
    return " ".join(value.split()).strip().lower()