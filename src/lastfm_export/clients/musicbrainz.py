import re
import threading
import time
from typing import Any

from lastfm_export.clients.http import HttpClient, validate_base_url
from lastfm_export.errors import ConfigError, HttpRequestError
from lastfm_export.models import ArtistInput, ArtistMetadataResponse


class _RequestPacer:
    """Serialize request starts across MusicBrainz clients in this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_start: float | None = None

    def wait(self) -> None:
        with self._lock:
            if self._last_start is not None:
                delay = 1.1 - (time.monotonic() - self._last_start)
                if delay > 0:
                    time.sleep(delay)
            self._last_start = time.monotonic()


_pacer = _RequestPacer()


def artist_search_params(artist_name: str, limit: int = 25) -> dict[str, Any]:
    ArtistInput(artist_name)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("MusicBrainz search limit must be between 1 and 100")
    escaped = "".join(
        chr(92) + char if char in '+-&|!(){}[]^"~*?:/\\' else char
        for char in artist_name
    )
    return {"query": f'artist:"{escaped}"', "limit": limit, "fmt": "json"}


class MusicBrainzClient:
    """Public artist metadata; searches return candidates without choosing one."""

    def __init__(
        self,
        *,
        user_agent: str,
        base_url: str = "https://musicbrainz.org/ws/2",
        http: HttpClient | None = None,
    ) -> None:
        if not re.fullmatch(
            r"[^/\s]+/[^\s]+\s+\([^()\n]*(?:@|https?://)[^()\n]+\)", user_agent
        ):
            raise ConfigError(
                "MusicBrainz User-Agent must include application/version and contact, e.g. app/1.0 (https://example.com)"
            )
        validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.http = http or HttpClient(user_agent=user_agent)

    def get_artist(self, mbid: str) -> ArtistMetadataResponse:
        if mbid is None:
            raise ValueError("Artist lookup requires an MBID")
        ArtistInput("lookup", mbid)
        return self._get(
            f"{self.base_url}/artist/{mbid}",
            {"inc": "genres+tags+aliases", "fmt": "json"},
            lookup=True,
        )

    def search_artists(
        self, artist_name: str, *, limit: int = 25
    ) -> ArtistMetadataResponse:
        return self._get(
            f"{self.base_url}/artist",
            artist_search_params(artist_name, limit),
            lookup=False,
        )

    def _get(
        self, url: str, params: dict[str, Any], *, lookup: bool
    ) -> ArtistMetadataResponse:
        response = self.http.get_response(
            url,
            params=params,
            headers={"User-Agent": self.user_agent},
            before_request=_pacer.wait,
        )
        payload = response.payload
        if lookup:
            valid = (
                isinstance(payload, dict)
                and isinstance(payload.get("name"), str)
                and bool(payload["name"])
            )
            if valid:
                try:
                    ArtistInput(payload["name"], payload.get("id") or "")
                except ValueError:
                    valid = False
            status = "success"
        else:
            items = payload.get("artists") if isinstance(payload, dict) else None
            valid = isinstance(items, list) and all(
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"]
                and item.get("id")
                for item in items
            )
            if valid:
                try:
                    for item in items:
                        ArtistInput(item["name"], item["id"])
                except ValueError:
                    valid = False
            status = "success" if items else "empty"
        if not valid:
            raise HttpRequestError(
                "GET",
                url,
                response.status_code,
                "Malformed artist response",
                payload=payload,
                attempts=response.attempts,
            )
        return ArtistMetadataResponse(
            status, payload, response.status_code, response.attempts
        )
