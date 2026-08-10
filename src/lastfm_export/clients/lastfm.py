import json
import logging
from typing import Any, Dict, Iterator, Optional

from lastfm_export.clients.http import HttpClient
from lastfm_export.errors import HttpRequestError, LastFMRecentTracksAccessError
from lastfm_export.integrity import WindowReport, WindowResult
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

    def get_user_registration_unix(self) -> Optional[int]:
        """Return the account registration timestamp when Last.fm exposes it."""
        registered = self.get_user_info().get("user", {}).get("registered")
        if isinstance(registered, dict):
            registered = registered.get("unixtime")
        try:
            return int(registered)
        except (TypeError, ValueError):
            return None

    def fetch_recent_tracks_window(
        self, *, from_unix: int, to_unix: int, page_size: int = 200
    ) -> WindowResult:
        """Fetch one bounded window while collecting integrity evidence."""
        if page_size <= 0:
            raise ValueError("page_size must be > 0")

        report = WindowReport(from_unix=from_unix, to_unix=to_unix)
        scrobbles: list[Scrobble] = []
        page = 1
        previous_raw: list[dict[str, Any]] | None = None
        previous_ts: int | None = None

        while True:
            payload = self._get_recent_tracks_page(
                page=page, from_unix=from_unix, to_unix=to_unix, limit=page_size
            )
            recent = payload.get("recenttracks", {})
            raw_tracks = recent.get("track", [])
            if isinstance(raw_tracks, dict):
                raw_tracks = [raw_tracks]
            if not isinstance(raw_tracks, list):
                raw_tracks = []

            total_pages = self._extract_total_pages(recent)
            api_total = self._extract_total(recent)
            if report.api_total is None:
                report.api_total = api_total
            elif api_total != report.api_total:
                report.violations.append("API total changed between pages")
            report.page_count += 1

            if not raw_tracks:
                if total_pages is None or page < total_pages:
                    report.violations.append(f"empty page {page} before reported end")
                break

            current_raw = [item for item in raw_tracks if isinstance(item, dict)]
            if previous_raw and _page_overlap(previous_raw, current_raw):
                report.violations.append(f"exact raw-payload overlap at page {page}")

            for item in current_raw:
                scrobble = self._parse_scrobble(item)
                if scrobble is None:
                    continue
                if not from_unix <= scrobble.timestamp_unix <= to_unix:
                    report.violations.append("record outside requested UTC window")
                if previous_ts is not None and scrobble.timestamp_unix > previous_ts:
                    report.violations.append("timestamp order reversal")
                previous_ts = scrobble.timestamp_unix
                scrobbles.append(scrobble)

            previous_raw = current_raw
            if total_pages is not None and page >= total_pages:
                break
            if total_pages is None and len(raw_tracks) < page_size:
                break
            page += 1

        report.materialized_count = len(scrobbles)
        if report.api_total is None:
            report.violations.append("API response omitted total")
        elif report.api_total != report.materialized_count:
            report.violations.append(
                f"API total {report.api_total} differs from materialized count {report.materialized_count}"
            )
        return WindowResult(scrobbles=scrobbles, report=report)

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
            if e.status_code == 403 and e.payload and e.payload.get("error") == 17:
                raise LastFMRecentTracksAccessError(
                    "Last.fm denied access to this account's recent listening history. "
                    "Check that 'Hide recent listening information' is disabled "
                    "in your Last.fm privacy settings."
                ) from e

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
    def _extract_total(recenttracks: Dict[str, Any]) -> Optional[int]:
        attr = recenttracks.get("@attr")
        if not isinstance(attr, dict):
            return None
        total = attr.get("total")
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


def _page_overlap(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
    """Detect any exact raw suffix/prefix overlap without deduplicating it."""
    max_overlap = min(len(previous), len(current))
    for size in range(1, max_overlap + 1):
        left = [json.dumps(x, ensure_ascii=False, sort_keys=True) for x in previous[-size:]]
        right = [json.dumps(x, ensure_ascii=False, sort_keys=True) for x in current[:size]]
        if left == right:
            return True
    return False
