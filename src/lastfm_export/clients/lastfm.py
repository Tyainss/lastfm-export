import logging
from typing import Any, Dict, Iterator, Optional

from lastfm_export.clients.http import HttpClient
from lastfm_export.errors import HttpRequestError
from lastfm_export.models import Scrobble

logger = logging.getLogger(__name__)


class LastFMClient:
    """
    Minimal Last.fm API client for exporting scrobbles.

    Notes:
    - `iter_recent_tracks()` yields scrobbles newest -> oldest, matching API order.
    - "Now playing" items are skipped.
    """

    def __init__(
        self,
        *,
        api_key: str,
        username: str,
        user_agent: str,
        base_url: str = "https://ws.audioscrobbler.com/2.0/",
        http: Optional[HttpClient] = None,
    ) -> None:
        self.api_key = api_key
        self.username = username
        self.base_url = base_url
        self.http = http or HttpClient(user_agent=user_agent)

    def get_user_info(self) -> Dict[str, Any]:
        params = {
            "method": "user.getinfo",
            "user": self.username,
            "api_key": self.api_key,
            "format": "json",
        }
        return self.http.get_json(self.base_url, params=params)

    def iter_recent_tracks(
        self,
        *,
        from_unix: Optional[int] = None,
        to_unix: Optional[int] = None,
        page_size: int = 200,
        page_limit: Optional[int] = None,
    ) -> Iterator[Scrobble]:
        """
        Yield scrobbles newest -> oldest.

        Args:
            from_unix: Inclusive start timestamp (seconds).
            to_unix: Inclusive end timestamp (seconds).
            page_size: Last.fm limit per page (commonly up to 200).
            page_limit: If set, stops after yielding pages up to this count.
        """
        if page_size <= 0:
            raise ValueError("page_size must be > 0")

        page = 1
        pages_seen = 0

        while True:
            payload = self._get_recent_tracks_page(
                page=page,
                from_unix=from_unix,
                to_unix=to_unix,
                limit=page_size,
            )

            recent = payload.get("recenttracks", {})
            tracks = recent.get("track", [])

            # Last.fm sometimes returns a dict for a single track, normalize to list.
            if isinstance(tracks, dict):
                tracks = [tracks]

            if not tracks:
                return

            yielded_any = False
            for item in tracks:
                scrobble = self._parse_scrobble(item)
                if scrobble is None:
                    continue
                yielded_any = True
                yield scrobble

            # If the page contained only "now playing" items, move on.
            if not yielded_any:
                pass

            pages_seen += 1
            if page_limit is not None and pages_seen >= page_limit:
                return

            total_pages = self._extract_total_pages(recent)
            if total_pages is not None and page >= total_pages:
                return

            page += 1

    def _get_recent_tracks_page(
        self,
        *,
        page: int,
        from_unix: Optional[int],
        to_unix: Optional[int],
        limit: int,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "method": "user.getrecenttracks",
            "user": self.username,
            "api_key": self.api_key,
            "format": "json",
            "limit": limit,
            "page": page,
        }
        if from_unix is not None:
            params["from"] = int(from_unix)
        if to_unix is not None:
            params["to"] = int(to_unix)

        try:
            return self.http.get_json(self.base_url, params=params)
        except HttpRequestError as e:
            # Add contextual info for debugging without changing exception shape.
            logger.error("Last.fm request failed: %s", e)
            raise

    @staticmethod
    def _extract_total_pages(recenttracks: Dict[str, Any]) -> Optional[int]:
        attr = recenttracks.get("@attr")
        if not isinstance(attr, dict):
            return None
        total = attr.get("totalPages")
        if total is None:
            return None
        try:
            return int(total)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_scrobble(item: Dict[str, Any]) -> Optional[Scrobble]:
        # Skip "now playing" (has no stable timestamp).
        attr = item.get("@attr")
        if isinstance(attr, dict) and attr.get("nowplaying") == "true":
            return None

        date = item.get("date")
        if not isinstance(date, dict) or "uts" not in date:
            return None

        try:
            ts = int(date["uts"])
        except (TypeError, ValueError):
            return None

        artist = item.get("artist")
        artist_name = None
        if isinstance(artist, dict):
            artist_name = artist.get("#text")

        track_name = item.get("name")
        album = item.get("album")
        album_name = None
        if isinstance(album, dict):
            album_name = album.get("#text") or None

        if not artist_name or not track_name:
            return None

        mbid = item.get("mbid") or None

        return Scrobble(
            artist_name=str(artist_name),
            track_name=str(track_name),
            album_name=str(album_name) if album_name is not None else None,
            timestamp_unix=ts,
            mbid=str(mbid) if mbid is not None and str(mbid) else None,
            raw=item,
        )