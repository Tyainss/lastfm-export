from pathlib import Path
from typing import Optional

import typer

from lastfm_export.cli._common import get_env_or_value, infer_format
from lastfm_export.cli.progress import ProgressReporter
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.clients.musicbrainz import MusicBrainzClient
from lastfm_export.errors import CollectionError, ConfigError
from lastfm_export.io.artist_metadata import load_manifest, read_collection
from lastfm_export.io.readers import (
    read_csv_records,
    read_json_records,
    read_ndjson_records,
)
from lastfm_export.models import ArtistInput
from lastfm_export.pipelines.artist_metadata import (
    OPERATIONS,
    CollectionSummary,
    collect_artist_metadata,
)


def _summary_line(summary: CollectionSummary) -> str:
    return (
        f"Artist metadata: {summary.total - summary.remaining}/{summary.total} completed; "
        f"attempted={summary.attempted}, skipped={summary.skipped}, "
        f"success={summary.success}, empty={summary.empty}, not_found={summary.not_found}, "
        f"failed={summary.failed}, remaining={summary.remaining}"
    )


def enrich_artists_cmd(
    out: Path = typer.Option(
        ...,
        "--out",
        help="Collection directory; must be new unless resuming or reusing.",
    ),
    in_path: Optional[Path] = typer.Option(
        None,
        "--in",
        help="JSON, NDJSON or CSV artist_name and optional artist mbid records.",
    ),
    in_format: Optional[str] = typer.Option(
        None, "--in-format", help="json | ndjson | csv; inferred by default."
    ),
    operation: Optional[list[str]] = typer.Option(
        None,
        "--operation",
        help=f"Repeat for each operation: {', '.join(OPERATIONS)}. Default: Last.fm tags and similar artists.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Continue an unfinished collection using its stored plan.",
    ),
    reuse: bool = typer.Option(
        False,
        "--reuse",
        help="Validate a completed collection without making requests.",
    ),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="Reattempt recorded failures while resuming."
    ),
    retry_from: Optional[Path] = typer.Option(
        None,
        "--retry-from",
        help="Create a new collection for failures from a completed source.",
    ),
    recover_tail: bool = typer.Option(
        False,
        "--recover-tail",
        help="Preserve and remove an incomplete log tail during resume.",
    ),
    similar_limit: Optional[int] = typer.Option(
        None, "--similar-limit", help="Number of Last.fm similar artists; default 25."
    ),
    search_limit: Optional[int] = typer.Option(
        None, "--search-limit", help="MusicBrainz search candidates, 1–100; default 25."
    ),
    autocorrect: Optional[bool] = typer.Option(
        None,
        "--autocorrect/--no-autocorrect",
        help="Allow Last.fm name correction; disabled by default.",
    ),
    progress: str = typer.Option("auto", "--progress", help="auto | on | off."),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="Last.fm API key; defaults to LASTFM_API_KEY."
    ),
    user_agent: Optional[str] = typer.Option(
        None,
        "--user-agent",
        help="MusicBrainz requires app/version (contact); defaults to MUSICBRAINZ_USER_AGENT when needed.",
    ),
) -> None:
    reporter = None
    try:
        reporter = ProgressReporter(progress)
        if resume and reuse:
            raise ConfigError("Choose only one of --resume and --reuse")
        mode = "resume" if resume else "reuse" if reuse else "new"
        artists = None
        if in_path is not None:
            if not in_path.is_file():
                raise ConfigError("--in must identify an existing input file")
            fmt = infer_format(in_path, in_format)
            readers = {
                "json": read_json_records,
                "ndjson": read_ndjson_records,
                "csv": read_csv_records,
            }
            if fmt not in readers:
                raise ConfigError("--in-format must be json, ndjson or csv")
            artists = []
            for record in readers[fmt](in_path):
                if not isinstance(record, dict):
                    raise ConfigError("Every artist input must be an object")
                if set(record) - {"artist_name", "mbid"}:
                    raise ConfigError(
                        "Artist input accepts only artist_name and optional artist mbid; do not pass scrobble records"
                    )
                artists.append(
                    ArtistInput(record.get("artist_name"), record.get("mbid") or None)
                )
        elif in_format is not None:
            raise ConfigError("--in-format requires --in")
        stored = (
            load_manifest(retry_from if retry_from else out)
            if retry_from or resume or reuse
            else None
        )
        selected = (
            operation
            if operation is not None
            else (
                stored["plan"]["settings"]["operations"]
                if stored
                else ["lastfm_tags", "lastfm_similar"]
            )
        )
        if not selected or any(item not in OPERATIONS for item in selected):
            raise ConfigError(f"Select operations from: {', '.join(OPERATIONS)}")
        providers = {item.split("_", 1)[0] for item in selected}
        if retry_from is not None:
            _, source_results = read_collection(retry_from)
            providers = {
                result.provider
                for result in source_results.values()
                if result.status == "failed"
            }
        lastfm = None
        musicbrainz = None
        if not reuse:
            if "lastfm" in providers:
                lastfm = LastFMClient(
                    api_key=get_env_or_value("LASTFM_API_KEY", api_key),
                    user_agent=user_agent or "lastfm-export",
                    base_url=stored["plan"]["settings"]["lastfm_base_url"]
                    if stored
                    else "https://ws.audioscrobbler.com/2.0/",
                )
            if "musicbrainz" in providers:
                musicbrainz = MusicBrainzClient(
                    user_agent=get_env_or_value("MUSICBRAINZ_USER_AGENT", user_agent),
                    base_url=stored["plan"]["settings"]["musicbrainz_base_url"]
                    if stored
                    else "https://musicbrainz.org/ws/2",
                )
        reporter.start(f"Artist metadata collection: {out}")
        summary = collect_artist_metadata(
            out=out,
            artists=artists,
            operations=operation,
            lastfm=lastfm,
            musicbrainz=musicbrainz,
            mode=mode,
            retry_failed=retry_failed,
            retry_from=retry_from,
            recover_tail=recover_tail,
            similar_limit=similar_limit,
            search_limit=search_limit,
            autocorrect=autocorrect,
            on_result=lambda value: reporter.update(_summary_line(value)),
        )
    except (ConfigError, ValueError) as exc:
        typer.echo(f"Invalid input/configuration: {exc}", err=True)
        raise typer.Exit(2) from None
    except (CollectionError, OSError) as exc:
        typer.echo(f"Collection failed: {exc}", err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.echo(
            "Collection interrupted; resume with the same --out and --resume.", err=True
        )
        raise typer.Exit(130) from None
    finally:
        if reporter is not None:
            reporter.close()
    typer.echo(_summary_line(summary))
    if summary.failed:
        raise typer.Exit(1)
