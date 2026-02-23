import base64
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

import requests

from lastfm_export.clients.http import HttpClient
from lastfm_export.models import SpotifyTrackEnrichment

logger = logging.getLogger(__name__)


class SpotifyClient:
    token_url = "https://accounts.spotify.com/api/token"
    base_url = "https://api.spotify.com/v1"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        user_agent: str,
        timeout_secs: float = 30.0,
        max_retry_after_secs: int = 120,
        artist_cache_size: int = 5000,
        http: Optional[HttpClient] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret

        self._token: Optional[str] = None
        self._token_expiry_unix: float = 0.0

        self._artist_cache: "OrderedDict[str, str]" = OrderedDict()
        self._artist_cache_size = int(artist_cache_size)

        self._token_session = requests.Session()
        self._token_session.headers["User-Agent"] = user_agent

        self.http = http or HttpClient(
            user_agent=user_agent,
            timeout_secs=timeout_secs,
            retry=None,
        )
        # Ensure Retry-After cap is respected for Spotify too
        self.http._retry.max_retry_after_secs = int(max_retry_after_secs)

    def search_track_first(self, *, track_name: str, artist_name: str) -> Optional[Dict[str, Any]]:
        token = self._ensure_token()

        q = f'track:"{track_name}" artist:"{self._clean_artist_name(artist_name)}"'
        params = {"q": q, "type": "track", "limit": 1}

        headers = {"Authorization": f"Bearer {token}"}
        payload = self.http.get_json(f"{self.base_url}/search", params=params, headers=headers)

        tracks = payload.get("tracks", {})
        items = tracks.get("items", [])
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        if not isinstance(first, dict):
            return None
        return first

    def build_track_enrichment(
        self,
        *,
        track_name: str,
        artist_name: str,
    ) -> Optional[SpotifyTrackEnrichment]:
        item = self.search_track_first(track_name=track_name, artist_name=artist_name)
        if item is None:
            return None

        track_id = item.get("id")
        if not track_id:
            return None

        artist_id = None
        artists = item.get("artists")
        if isinstance(artists, list) and artists:
            if isinstance(artists[0], dict):
                artist_id = artists[0].get("id") or None

        album_id = None
        album = item.get("album")
        if isinstance(album, dict):
            album_id = album.get("id") or None

        track_url = None
        external_urls = item.get("external_urls")
        if isinstance(external_urls, dict):
            track_url = external_urls.get("spotify") or None

        popularity = item.get("popularity")
        if popularity is not None:
            try:
                popularity = int(popularity)
            except (TypeError, ValueError):
                popularity = None

        return SpotifyTrackEnrichment(
            spotify_track_id=str(track_id),
            spotify_artist_id=str(artist_id) if artist_id else None,
            spotify_album_id=str(album_id) if album_id else None,
            spotify_track_url=str(track_url) if track_url else None,
            popularity=popularity,
            raw=item,
        )

    def _ensure_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expiry_unix:
            return self._token

        token, expires_in = self._fetch_token()
        # Refresh a bit early to avoid edge-of-expiry failures.
        self._token = token
        self._token_expiry_unix = now + max(0, int(expires_in) - 30)
        return self._token

    def _fetch_token(self) -> Tuple[str, int]:
        auth_b64 = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}

        resp = self._token_session.post(self.token_url, headers=headers, data=data, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        token = payload.get("access_token")
        expires_in = payload.get("expires_in")

        if not token or not expires_in:
            raise RuntimeError("Spotify token response missing access_token or expires_in")

        return str(token), int(expires_in)

    @staticmethod
    def _clean_artist_name(artist_name: str) -> str:
        import re

        patterns = [
            r"\sfeat\.",
            r"\sFeat\.",
            r"\sft\.",
            r"\sFt\.",
            r"\s\[",
            r"\sX\s",
        ]
        for pat in patterns:
            m = re.search(pat, artist_name)
            if m:
                return artist_name[: m.start()].strip()
        return artist_name.strip()