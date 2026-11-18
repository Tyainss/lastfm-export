import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any, Dict, Iterator, Optional, TypeVar

from lastfm_export.clients.http import HttpClient, JsonResponse, validate_base_url
from lastfm_export.errors import (
    ConfigError,
    HttpRequestError,
    LastFMRecentTracksAccessError,
)
from lastfm_export.integrity import WindowReport, WindowResult
from lastfm_export.models import (
    ArtistInput,
    ArtistMetadataResponse,
    LastFMUser,
    Scrobble,
    UserTopArtist,
)

logger = logging.getLogger(__name__)
_UserRecord = TypeVar("_UserRecord")


class LastFMClient:
    """
    Last.fm API client for scrobbles and public artist metadata.

    Notes:
    - `iter_recent_tracks()` yields scrobbles newest -> oldest, matching API order.
    - "Now playing" items are skipped.
    """

    def __init__(
        self,
        *,
        api_key: str,
        username: str | None = None,
        user_agent: str,
        base_url: str = "https://ws.audioscrobbler.com/2.0/",
        http: Optional[HttpClient] = None,
    ) -> None:
        self.api_key = api_key
        self.username = username
        self.base_url = base_url
        self.http = http or HttpClient(user_agent=user_agent)

    def _require_username(self) -> str:
        if not self.username or not self.username.strip():
            raise ConfigError("A Last.fm username is required for user operations")
        return self.username

    def get_artist_top_tags(
        self,
        *,
        artist_name: str,
        mbid: str | None = None,
        autocorrect: bool = False,
    ) -> ArtistMetadataResponse:
        return self._get_artist_metadata(
            "artist.getTopTags",
            ArtistInput(artist_name, mbid),
            autocorrect=autocorrect,
        )

    def get_artist_info(
        self,
        *,
        artist_name: str,
        mbid: str | None = None,
        autocorrect: bool = False,
        lang: str = "en",
    ) -> ArtistMetadataResponse:
        return self._get_artist_metadata(
            "artist.getInfo",
            ArtistInput(artist_name, mbid),
            autocorrect=autocorrect,
            lang=lang,
        )

    def get_similar_artists(
        self,
        *,
        artist_name: str,
        mbid: str | None = None,
        limit: int = 25,
        autocorrect: bool = False,
    ) -> ArtistMetadataResponse:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        return self._get_artist_metadata(
            "artist.getSimilar",
            ArtistInput(artist_name, mbid),
            autocorrect=autocorrect,
            limit=limit,
        )

    def _get_artist_metadata(
        self,
        method: str,
        artist: ArtistInput,
        *,
        autocorrect: bool,
        limit: int | None = None,
        lang: str | None = None,
    ) -> ArtistMetadataResponse:
        validate_base_url(self.base_url)
        if not isinstance(autocorrect, bool):
            raise ValueError("autocorrect must be boolean")
        if not self.api_key or not self.api_key.strip():
            raise ConfigError("A Last.fm API key is required")
        params = artist_metadata_params(
            method, artist, autocorrect=autocorrect, limit=limit, lang=lang
        )
        response = self.http.get_response(
            self.base_url,
            params={**params, "api_key": self.api_key},
            response_error=_lastfm_api_error,
            secrets=(self.api_key,),
        )
        payload = response.payload
        if method == "artist.getInfo":
            artist_info = payload.get("artist") if isinstance(payload, dict) else None
            if not isinstance(artist_info, dict) or not isinstance(
                artist_info.get("name"), str
            ):
                raise HttpRequestError(
                    "GET",
                    self.base_url,
                    response.status_code,
                    "Malformed artist info response",
                    payload=payload,
                    attempts=response.attempts,
                )
            bio = artist_info.get("bio") or {}
            if not isinstance(bio, dict) or any(
                bio.get(field) is not None and not isinstance(bio[field], str)
                for field in ("summary", "content")
            ):
                raise HttpRequestError(
                    "GET",
                    self.base_url,
                    response.status_code,
                    "Malformed artist biography",
                    payload=payload,
                    attempts=response.attempts,
                )
            has_text = any(
                (bio.get(field) or "").strip() for field in ("summary", "content")
            )
            return ArtistMetadataResponse(
                "success" if has_text else "empty",
                payload,
                response.status_code,
                response.attempts,
            )
        container, field = (
            ("toptags", "tag")
            if method == "artist.getTopTags"
            else ("similarartists", "artist")
        )
        block = payload.get(container) if isinstance(payload, dict) else None
        items = block.get(field) if isinstance(block, dict) else None
        if (
            isinstance(items, dict)
            and isinstance(items.get("name"), str)
            and items["name"]
        ):
            items = [items]
        if not isinstance(items, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"]
            for item in items
        ):
            raise HttpRequestError(
                "GET",
                self.base_url,
                response.status_code,
                "Malformed artist response",
                payload=payload,
                attempts=response.attempts,
            )
        return ArtistMetadataResponse(
            "success" if items else "empty",
            payload,
            response.status_code,
            response.attempts,
        )

    def iter_user_friends(
        self, *, page_size: int = 50, limit: int | None = None
    ) -> Iterator[LastFMUser]:
        """Yield the connections returned by user.getFriends in API order."""
        yield from self._iter_user_records(
            method="user.getFriends",
            container="friends",
            field="user",
            page_size=page_size,
            limit=limit,
            parse=_parse_user,
            extra_params={"recenttracks": 0},
        )

    def iter_user_top_artists(
        self,
        *,
        period: str = "overall",
        page_size: int = 50,
        limit: int | None = None,
    ) -> Iterator[UserTopArtist]:
        """Yield a user's ranked artists; limit caps the total records returned."""
        if not isinstance(period, str) or period not in {
            "overall",
            "7day",
            "1month",
            "3month",
            "6month",
            "12month",
        }:
            raise ValueError("Unsupported top-artists period")
        yield from self._iter_user_records(
            method="user.getTopArtists",
            container="topartists",
            field="artist",
            page_size=page_size,
            limit=limit,
            parse=_parse_user_artist,
            extra_params={"period": period},
        )

    def _iter_user_records(
        self,
        *,
        method: str,
        container: str,
        field: str,
        page_size: int,
        limit: int | None,
        parse: Callable[[dict[str, Any]], _UserRecord],
        extra_params: dict[str, Any],
    ) -> Iterator[_UserRecord]:
        if type(page_size) is not int or page_size < 1:
            raise ValueError("page_size must be a positive integer")
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer or None")
        username = self._require_username()
        request_size = min(page_size, limit) if limit is not None else page_size
        page = 1
        yielded = 0
        expected_totals = None
        seen_pages: set[str] = set()
        while True:
            payload = self._get_user_payload(
                {
                    "method": method,
                    "user": username,
                    "api_key": self.api_key,
                    "format": "json",
                    "page": page,
                    "limit": request_size,
                    **extra_params,
                },
                container=container,
            )
            block = payload[container]
            try:
                attrs = block["@attr"]
                if not isinstance(attrs, dict):
                    raise ValueError("Invalid pagination metadata")
                pages = _user_count(attrs["totalPages"])
                total = _user_count(attrs["total"])
                if (total > 0 and pages == 0) or (total == 0 and pages > 1):
                    raise ValueError("Inconsistent pagination totals")
                if (
                    "page" in attrs
                    and _user_count(attrs["page"], positive=True) != page
                ):
                    raise ValueError("Unexpected page number")
                if "user" in attrs and (
                    not isinstance(attrs["user"], str)
                    or attrs["user"].casefold() != username.casefold()
                ):
                    raise ValueError("Response belongs to another user")
                totals = (pages, total)
                if expected_totals is not None and totals != expected_totals:
                    raise ValueError("Pagination totals changed")
                expected_totals = totals
                items = block[field]
                if isinstance(items, dict):
                    items = [items]
                if not isinstance(items, list):
                    raise ValueError("Invalid record list")
                records = [parse(item) for item in items]
                if not records and (total != 0 or page < pages):
                    raise ValueError("Empty page before the reported end")
                if records and (total == 0 or yielded + len(records) > total):
                    raise ValueError("Records exceed the reported total")
                signature = hashlib.sha256(
                    json.dumps(items, sort_keys=True, ensure_ascii=False).encode(
                        "utf-8"
                    )
                ).hexdigest()
                if records and signature in seen_pages:
                    raise ValueError("Repeated page")
                if page >= pages and yielded + len(records) != total:
                    raise ValueError("Record count differs from the reported total")
            except (KeyError, TypeError, ValueError) as exc:
                raise HttpRequestError(
                    "GET", self.base_url, message=f"Invalid {method} response: {exc}"
                ) from None
            seen_pages.add(signature)
            for record in records:
                yield record
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if page >= pages:
                return
            page += 1

    def get_user_info(self) -> Dict[str, Any]:
        params = {
            "method": "user.getinfo",
            "user": self._require_username(),
            "api_key": self.api_key,
            "format": "json",
        }
        return self._get_user_payload(params, container="user")

    def _get_user_payload(
        self, params: Dict[str, Any], *, container: str
    ) -> Dict[str, Any]:
        validate_base_url(self.base_url)
        if not self.api_key or not self.api_key.strip():
            raise ConfigError("A Last.fm API key is required")
        response = self.http.get_response(
            self.base_url,
            params=params,
            response_error=_lastfm_api_error,
            secrets=(self.api_key,),
        )
        payload = response.payload
        if not isinstance(payload, dict) or not isinstance(
            payload.get(container), dict
        ):
            raise HttpRequestError(
                "GET",
                self.base_url,
                response.status_code,
                f"Malformed Last.fm {container} response",
                attempts=response.attempts,
            )
        return payload

    def get_user_registration_unix(self) -> Optional[int]:
        """Return the account registration timestamp when Last.fm exposes it."""
        registered = self.get_user_info().get("user", {}).get("registered")
        if isinstance(registered, dict):
            registered = registered.get("unixtime")
        try:
            return int(registered)
        except (TypeError, ValueError):
            return None

    def get_user_history_start_unix(self) -> int:
        """Choose a full-history lower bound without assuming registration is earliest.

        One small request checks for older scrobbles. If any exist, use the Unix
        epoch as a conservative bound; otherwise registration avoids empty years.
        """
        registered = self.get_user_registration_unix()
        if registered is None or registered < 0:
            raise ConfigError("Could not determine the Last.fm registration timestamp")
        if registered == 0:
            return 0
        payload = self._get_recent_tracks_page(
            page=1, from_unix=0, to_unix=registered - 1, limit=1
        )
        total = self._extract_total(payload["recenttracks"])
        if total is None or total < 0:
            raise HttpRequestError(
                "GET",
                self.base_url,
                message="Missing or invalid total in pre-registration history probe",
            )
        tracks = payload["recenttracks"]["track"]
        if isinstance(tracks, dict):
            tracks = [tracks]
        has_dated_records = any(
            self._parse_scrobble(item) is not None for item in tracks
        )
        if bool(total) != has_dated_records:
            raise HttpRequestError(
                "GET",
                self.base_url,
                message="Inconsistent pre-registration history probe",
            )
        return 0 if total else registered

    def fetch_recent_tracks_window(
        self, *, from_unix: int, to_unix: int, page_size: int = 200
    ) -> WindowResult:
        """Fetch one bounded window while collecting integrity evidence."""
        if page_size <= 0:
            raise ValueError("page_size must be > 0")
        if from_unix > to_unix:
            raise ValueError("from_unix must not exceed to_unix")

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
        on_page: Optional[Callable[[int], None]] = None,
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
        if from_unix is not None and to_unix is not None and from_unix > to_unix:
            raise ValueError("from_unix must not exceed to_unix")

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
            if on_page is not None:
                on_page(pages_seen)
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
            "user": self._require_username(),
            "api_key": self.api_key,
            "format": "json",
            "limit": limit,
            "page": page,
        }
        if from_unix is not None:
            params["from"] = int(from_unix)
        if to_unix is not None:
            # The API excludes `to`; the public Python interface includes it.
            params["to"] = int(to_unix) + 1

        try:
            payload = self._get_user_payload(params, container="recenttracks")
            recent = payload["recenttracks"]
            tracks = recent.get("track")
            if not isinstance(tracks, (list, dict)) or (
                isinstance(tracks, list)
                and any(not isinstance(item, dict) for item in tracks)
            ):
                raise HttpRequestError(
                    "GET", self.base_url, message="Malformed Last.fm track list"
                )
            attributes = recent.get("@attr", {})
            if not isinstance(attributes, dict):
                raise HttpRequestError(
                    "GET",
                    self.base_url,
                    message="Malformed Last.fm pagination metadata",
                )
            returned_user = attributes.get("user")
            if returned_user is not None and (
                not isinstance(returned_user, str)
                or returned_user.casefold() != self._require_username().casefold()
            ):
                raise HttpRequestError(
                    "GET", self.base_url, message="Last.fm returned a different user"
                )
            return payload
        except HttpRequestError as e:
            if e.provider_code == 17 or (
                isinstance(e.payload, dict) and str(e.payload.get("error")) == "17"
            ):
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


def _user_count(value: Any, *, positive: bool = False) -> int:
    if type(value) is not int and not (isinstance(value, str) and value.isdecimal()):
        raise ValueError("Expected an integer count")
    count = int(value)
    if count < (1 if positive else 0):
        raise ValueError("Count is out of range")
    return count


def _user_name(item: dict[str, Any]) -> str:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("name"), str)
        or not item["name"].strip()
    ):
        raise ValueError("Missing or invalid name")
    return item["name"]


def _parse_user(item: dict[str, Any]) -> LastFMUser:
    return LastFMUser(username=_user_name(item), raw=item)


def _parse_user_artist(item: dict[str, Any]) -> UserTopArtist:
    name = _user_name(item)
    playcount = _user_count(item["playcount"])
    attrs = item.get("@attr", {})
    if not isinstance(attrs, dict):
        raise ValueError("Invalid artist rank metadata")
    rank = _user_count(attrs["rank"], positive=True) if "rank" in attrs else None
    mbid = item.get("mbid")
    if mbid is not None and not isinstance(mbid, str):
        raise ValueError("Invalid artist MBID")
    return UserTopArtist(
        artist_name=name, playcount=playcount, rank=rank, mbid=mbid or None, raw=item
    )


def _page_overlap(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> bool:
    """Detect any exact raw suffix/prefix overlap without deduplicating it."""
    max_overlap = min(len(previous), len(current))
    left = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in previous]
    right = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in current]
    for size in range(1, max_overlap + 1):
        if left[-size:] == right[:size]:
            return True
    return False


def artist_metadata_params(
    method: str,
    artist: ArtistInput,
    *,
    autocorrect: bool = False,
    limit: int | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    params = {"method": method, "format": "json", "autocorrect": int(autocorrect)}
    if lang is not None:
        if not isinstance(lang, str) or not lang.strip():
            raise ValueError("lang must be a nonempty string")
        params["lang"] = lang
    params.update(
        {"mbid": artist.mbid} if artist.mbid else {"artist": artist.artist_name}
    )
    if limit is not None:
        params["limit"] = limit
    return params


def _lastfm_api_error(response: JsonResponse) -> HttpRequestError | None:
    payload = response.payload
    if not isinstance(payload, dict) or "error" not in payload:
        return None
    try:
        code = int(payload["error"])
    except (TypeError, ValueError):
        code = None
    return HttpRequestError(
        "GET",
        "",
        message="Last.fm API error",
        payload=payload,
        provider_code=code,
        retryable=code in {8, 11, 16, 29},
        fatal=code in {2, 3, 4, 5, 9, 10, 13, 26},
    )
