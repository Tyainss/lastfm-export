from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

from lastfm_export.clients.http import validate_base_url
from lastfm_export.clients.lastfm import LastFMClient, artist_metadata_params
from lastfm_export.clients.musicbrainz import (
    MusicBrainzClient,
    artist_search_params,
)
from lastfm_export.errors import CollectionError, ConfigError, HttpRequestError
from lastfm_export.io.artist_metadata import (
    SCHEMA_VERSION,
    CollectionStore,
    load_manifest,
    read_collection,
    request_id,
)
from lastfm_export.models import (
    ArtistInput,
    ArtistMetadataResponse,
    ArtistMetadataResult,
)

OPERATIONS = (
    "lastfm_tags",
    "lastfm_similar",
    "musicbrainz_lookup",
    "musicbrainz_search",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    collection_id: str
    total: int
    attempted: int
    skipped: int
    success: int
    empty: int
    not_found: int
    failed: int
    remaining: int
    completed: bool


def _summary(
    manifest: dict[str, Any],
    counts: Counter,
    *,
    attempted: int,
    skipped: int,
) -> CollectionSummary:
    total = len(manifest["plan"]["requests"])
    return CollectionSummary(
        manifest["collection_id"],
        total,
        attempted,
        skipped,
        counts["success"],
        counts["empty"],
        counts["not_found"],
        counts["failed"],
        total - sum(counts.values()),
        manifest["state"] == "completed",
    )


def _make_plan(artists: list[ArtistInput], settings: dict[str, Any]) -> dict[str, Any]:
    if not artists:
        raise ConfigError("At least one artist is required")
    operations = settings["operations"]
    if not operations or any(operation not in OPERATIONS for operation in operations):
        raise ConfigError(f"Select operations from: {', '.join(OPERATIONS)}")
    for limit_name in ("similar_limit", "search_limit"):
        limit = settings[limit_name]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ConfigError(f"{limit_name} must be a positive integer")
    if settings["search_limit"] > 100:
        raise ConfigError("search_limit must be at most 100")
    if not isinstance(settings["autocorrect"], bool):
        raise ConfigError("autocorrect must be boolean")
    for provider in ("lastfm", "musicbrainz"):
        validate_base_url(settings[f"{provider}_base_url"])
    planned = {}
    input_requests = []
    for artist in artists:
        if not isinstance(artist, ArtistInput):
            raise ConfigError("artists must contain ArtistInput objects")
        ids = []
        for operation in operations:
            provider = operation.split("_", 1)[0]
            if operation.startswith("lastfm_"):
                endpoint = (
                    "artist.getTopTags"
                    if operation == "lastfm_tags"
                    else "artist.getSimilar"
                )
                params = artist_metadata_params(
                    endpoint,
                    artist,
                    autocorrect=settings["autocorrect"],
                    limit=settings["similar_limit"]
                    if operation == "lastfm_similar"
                    else None,
                )
            elif operation == "musicbrainz_lookup":
                if not artist.mbid:
                    raise ConfigError(
                        "musicbrainz_lookup requires an accepted artist MBID for every input; use musicbrainz_search separately"
                    )
                endpoint = f"artist/{artist.mbid}"
                params = {"inc": "genres+tags+aliases", "fmt": "json"}
            else:
                endpoint = "artist"
                params = artist_search_params(
                    artist.artist_name, settings["search_limit"]
                )
            request = {
                "input_artist": artist.to_record(),
                "provider": provider,
                "endpoint": endpoint,
                "base_url": settings[f"{provider}_base_url"],
                "request_params": params,
            }
            identity = request_id(request)
            planned[identity] = {"request_id": identity, "request": request}
            ids.append(identity)
        input_requests.append(ids)
    return {
        "inputs": [artist.to_record() for artist in artists],
        "input_requests": input_requests,
        "settings": settings,
        "requests": list(planned.values()),
    }


def _collect_request(
    request: dict[str, Any],
    *,
    lastfm: LastFMClient | None,
    musicbrainz: MusicBrainzClient | None,
) -> ArtistMetadataResponse:
    artist = request["input_artist"]
    params = request["request_params"]
    if request["endpoint"] == "artist.getTopTags":
        return lastfm.get_artist_top_tags(
            **artist, autocorrect=bool(params["autocorrect"])
        )
    if request["endpoint"] == "artist.getSimilar":
        return lastfm.get_similar_artists(
            **artist, autocorrect=bool(params["autocorrect"]), limit=params["limit"]
        )
    if request["endpoint"] == "artist":
        return musicbrainz.search_artists(artist["artist_name"], limit=params["limit"])
    return musicbrainz.get_artist(artist["mbid"])


def collect_artist_metadata(
    *,
    out: Path,
    artists: Iterable[ArtistInput] | None = None,
    operations: Iterable[str] | None = None,
    lastfm: LastFMClient | None = None,
    musicbrainz: MusicBrainzClient | None = None,
    similar_limit: int | None = None,
    search_limit: int | None = None,
    autocorrect: bool | None = None,
    mode: str = "new",
    retry_failed: bool = False,
    retry_from: Path | None = None,
    recover_tail: bool = False,
    on_result: Callable[[CollectionSummary], None] | None = None,
) -> CollectionSummary:
    """Collect independent source snapshots; matching and source preferences belong to callers.

    New collections require inputs. Resume may use the stored plan; explicit inputs
    and settings must match it. Reuse validates a finalized snapshot without requests.
    retry_from selects failed requests from a finalized snapshot into a new directory.
    """
    out = Path(out)
    if mode not in {"new", "resume", "reuse"}:
        raise ConfigError("mode must be new, resume or reuse")
    if retry_failed and mode != "resume":
        raise ConfigError("retry_failed requires resume")
    if recover_tail and mode != "resume":
        raise ConfigError("recover_tail requires resume")
    if retry_from is not None and mode != "new":
        raise ConfigError("retry_from requires a new destination")
    stored = None
    if mode != "new" or retry_from is not None:
        if mode == "resume":
            stored = load_manifest(out)
        else:
            stored, latest = read_collection(
                retry_from if retry_from is not None else out
            )
        if retry_from is not None and stored["state"] != "completed":
            raise ConfigError("retry_from requires a completed source collection")
        if mode == "reuse" and stored["state"] != "completed":
            raise ConfigError("Only completed collections can be reused")
    settings = (
        dict(stored["plan"]["settings"])
        if stored
        else {
            "operations": ["lastfm_tags", "lastfm_similar"],
            "similar_limit": 25,
            "search_limit": 25,
            "autocorrect": False,
            "lastfm_base_url": "https://ws.audioscrobbler.com/2.0/",
            "musicbrainz_base_url": "https://musicbrainz.org/ws/2",
        }
    )
    if operations is not None:
        settings["operations"] = list(dict.fromkeys(operations))
    for name, value in (
        ("similar_limit", similar_limit),
        ("search_limit", search_limit),
        ("autocorrect", autocorrect),
    ):
        if value is not None:
            settings[name] = value
    for provider, client in (("lastfm", lastfm), ("musicbrainz", musicbrainz)):
        if client is not None:
            settings[f"{provider}_base_url"] = client.base_url
    input_artists = (
        list(artists)
        if artists is not None
        else (
            [ArtistInput(**item) for item in stored["plan"]["inputs"]] if stored else []
        )
    )
    plan = _make_plan(input_artists, settings)
    if stored and retry_from is None and plan != stored["plan"]:
        # Retry collections can contain a subset of the original operations.
        if (
            settings != stored["plan"]["settings"]
            or plan["inputs"] != stored["plan"]["inputs"]
        ):
            raise ConfigError("Inputs or settings differ from the collection plan")
        plan = stored["plan"]
    if retry_from is not None:
        if (
            artists is not None
            or operations is not None
            or any(
                value is not None
                for value in (similar_limit, search_limit, autocorrect)
            )
        ):
            raise ConfigError(
                "retry_from uses the source plan; do not supply new inputs or settings"
            )
        if settings != stored["plan"]["settings"]:
            raise ConfigError("Retry clients must use the source API base URLs")
        failed = {
            identity for identity, result in latest.items() if result.status == "failed"
        }
        if not failed:
            raise ConfigError("Source collection has no failed requests")
        plan = {
            **stored["plan"],
            "requests": [
                item
                for item in stored["plan"]["requests"]
                if item["request_id"] in failed
            ],
        }
        pairs = [
            (artist, [identity for identity in ids if identity in failed])
            for artist, ids in zip(plan["inputs"], plan["input_requests"])
        ]
        plan["inputs"] = [artist for artist, ids in pairs if ids]
        plan["input_requests"] = [ids for artist, ids in pairs if ids]
    if mode == "reuse":
        return _summary(
            stored,
            Counter(result.status for result in latest.values()),
            attempted=0,
            skipped=len(latest),
        )
    if stored and mode == "resume" and stored["state"] == "completed":
        raise ConfigError(
            "Completed collections are read-only; use reuse or a new destination"
        )
    # Missing clients are rejected before a new snapshot is created.
    providers = {item["request"]["provider"] for item in plan["requests"]}
    for provider, client in (("lastfm", lastfm), ("musicbrainz", musicbrainz)):
        if provider in providers and client is None:
            raise ConfigError(f"A configured {provider} client is required")
    if "lastfm" in providers and (not lastfm.api_key or not lastfm.api_key.strip()):
        raise ConfigError("A Last.fm API key is required")
    metadata = {
        "collection_id": str(uuid4()),
        "package_version": version("lastfm-export"),
        "started_at": _utc_now(),
        "completed_at": None,
        "source_results_sha256": stored["results_sha256"]
        if retry_from is not None
        else None,
        "source_collection_id": stored["collection_id"]
        if retry_from is not None
        else None,
    }
    attempted = 0
    with CollectionStore(
        out, mode=mode, plan=plan, metadata=metadata, recover_tail=recover_tail
    ) as store:
        eligible = [
            item
            for item in plan["requests"]
            if item["request_id"] not in store.latest
            or (retry_failed and store.latest[item["request_id"]].status == "failed")
        ]
        skipped = len(plan["requests"]) - len(eligible)
        counts = Counter(result.status for result in store.latest.values())
        for item in eligible:
            identity, request = item["request_id"], item["request"]
            started = _utc_now()
            previous = store.latest.get(identity)
            fatal = False
            try:
                response = _collect_request(
                    request, lastfm=lastfm, musicbrainz=musicbrainz
                )
            except HttpRequestError as exc:
                not_found = (
                    request["provider"] == "musicbrainz"
                    and request["endpoint"] != "artist"
                    and exc.status_code == 404
                ) or (
                    request["provider"] == "lastfm"
                    and (
                        exc.provider_code == 7
                        or (
                            exc.provider_code == 6
                            and isinstance(exc.payload, dict)
                            and str(exc.payload.get("message", "")).strip().lower()
                            in {
                                "artist not found",
                                "the artist you supplied could not be found",
                            }
                        )
                    )
                )
                status = "not_found" if not_found else "failed"
                raw = exc.payload
                http_status, provider_code, wire_attempts = (
                    exc.status_code,
                    exc.provider_code,
                    exc.attempts,
                )
                error = (
                    None
                    if not_found
                    else {
                        "kind": type(exc).__name__,
                        "message": exc.message,
                        "retryable": exc.retryable,
                        "fatal": exc.fatal,
                    }
                )
                fatal = exc.fatal and not not_found
            else:
                status, raw = response.status, response.raw_response
                http_status, provider_code, wire_attempts = (
                    response.http_status,
                    None,
                    response.wire_attempts,
                )
                error = None
            result = ArtistMetadataResult(
                SCHEMA_VERSION,
                store.manifest["collection_id"],
                identity,
                previous.attempt + 1 if previous else 1,
                **request,
                started_at=started,
                collected_at=_utc_now(),
                status=status,
                http_status=http_status,
                provider_code=provider_code,
                wire_attempts=wire_attempts,
                raw_response=raw,
                error=error,
            )
            store.append(result)
            if previous is not None:
                counts[previous.status] -= 1
            counts[result.status] += 1
            attempted += 1
            if on_result is not None:
                on_result(
                    _summary(
                        store.manifest,
                        counts,
                        attempted=attempted,
                        skipped=skipped,
                    )
                )
            if fatal:
                raise CollectionError(
                    f"Provider rejected global credentials/configuration; failure recorded; "
                    f"failed={sum(result.status == 'failed' for result in store.latest.values())}, "
                    f"remaining={len(plan['requests']) - len(store.latest)}. "
                    "Correct the configuration and resume with retry_failed=True / --retry-failed"
                )
        store.finalize(_utc_now())
        return _summary(store.manifest, counts, attempted=attempted, skipped=skipped)
