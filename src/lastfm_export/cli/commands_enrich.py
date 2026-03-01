
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Tuple

import typer

from lastfm_export.clients.spotify import SpotifyClient
from lastfm_export.errors import ConfigError
from lastfm_export.cli._common import ensure_overwrite_allowed, get_env_or_value, infer_format
from lastfm_export.io.readers import read_csv_records, read_json_records, read_ndjson_records
from lastfm_export.io.sinks import csv_sink, json_sink, ndjson_sink
from lastfm_export.models import Scrobble, SpotifyTrackEnrichment

enrich_app = typer.Typer(no_args_is_help=True)

Record = Dict[str, Any]

def _norm_key(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _record_to_scrobble(rec: Record) -> Scrobble:
    ts = rec.get("timestamp_unix")
    if ts is None:
        raise ValueError("Missing timestamp_unix in input record")

    artist_name = str(rec.get("artist_name") or "")
    track_name = str(rec.get("track_name") or "")
    if not artist_name or not track_name:
        raise ValueError("Input record missing artist_name or track_name")

    return Scrobble(
        artist_name=artist_name,
        track_name=track_name,
        album_name=rec.get("album_name"),
        timestamp_unix=int(ts),
        mbid=rec.get("mbid"),
        raw=rec,
    )

def _spotify_from_record(value: Any) -> Optional[SpotifyTrackEnrichment]:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    track_id = value.get("spotify_track_id")
    if not track_id:
        return None
    return SpotifyTrackEnrichment(
        spotify_track_id=str(track_id),
        spotify_artist_id=value.get("spotify_artist_id"),
        spotify_album_id=value.get("spotify_album_id"),
        spotify_track_url=value.get("spotify_track_url"),
        popularity=value.get("popularity"),
        raw=value,
    )


def _load_records(in_path: Path, fmt: str) -> Iterable[Record]:
    if fmt == "ndjson":
        return read_ndjson_records(in_path)
    if fmt == "json":
        return read_json_records(in_path)
    if fmt == "csv":
        return read_csv_records(in_path)
    raise ConfigError(f"Unsupported input format: {fmt}")


def _resolve_sink(out: Path, fmt: str, *, overwrite: bool) -> Callable[[Iterable[Record]], None]:
    if fmt == "ndjson":
        return ndjson_sink(out, overwrite=overwrite)
    if fmt == "json":
        return json_sink(out, overwrite=overwrite)
    if fmt == "csv":
        return csv_sink(out, overwrite=overwrite)
    raise ConfigError(f"Unsupported output format: {fmt}")


@dataclass(slots=True)
class SpotifyEnrichStats:
    records_total: int = 0
    records_skipped_existing: int = 0
    cache_hits: int = 0
    spotify_lookups: int = 0
    spotify_misses: int = 0

    def to_log_line(self) -> str:
        return (
            "Spotify enrich stats: "
            f"records={self.records_total}, "
            f"skipped_existing={self.records_skipped_existing}, "
            f"lookups={self.spotify_lookups}, "
            f"cache_hits={self.cache_hits}, "
            f"misses={self.spotify_misses}"
        )


def _iter_enriched_records(
    *,
    records: Iterable[Record],
    spotify: SpotifyClient,
    dedupe: bool,
    only_missing: bool,
    stats: SpotifyEnrichStats,
) -> Iterator[Record]:
    cache: Dict[Tuple[str, str], Optional[SpotifyTrackEnrichment]] = {}

    for rec in records:
        stats.records_total += 1

        sc = _record_to_scrobble(rec)
        key = (_norm_key(sc.artist_name), _norm_key(sc.track_name))

        if only_missing:
            existing = _spotify_from_record(rec.get("spotify"))
            if existing is not None:
                stats.records_skipped_existing += 1
                if dedupe:
                    cache[key] = existing
                yield rec
                continue

        if dedupe and key in cache:
            stats.cache_hits += 1
            enrichment = cache[key]
        else:
            stats.spotify_lookups += 1
            enrichment = spotify.build_track_enrichment(
                track_name=sc.track_name,
                artist_name=sc.artist_name,
            )
            if enrichment is None:
                stats.spotify_misses += 1
            if dedupe:
                cache[key] = enrichment

        out_rec = dict(rec)
        out_rec["spotify"] = None if enrichment is None else enrichment.to_record()
        yield out_rec



@enrich_app.command("spotify")
def enrich_spotify_cmd(
    in_path: Path = typer.Option(..., "--in", help="Input file path containing scrobbles."),
    out: Path = typer.Option(..., "--out", help="Output file path."),
    in_format: Optional[str] = typer.Option(None, "--in-format", help="ndjson | json | csv (default: inferred)."),
    out_format: Optional[str] = typer.Option(None, "--out-format", help="ndjson | json | csv (default: inferred)."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite output file."),
    dedupe: bool = typer.Option(True, "--dedupe/--no-dedupe", help="Cache lookups by (artist, track)."),
    only_missing: bool = typer.Option(False, "--only-missing", help="Skip records that already have spotify data."),
    client_id: Optional[str] = typer.Option(None, "--client-id", help="Spotify client id (default: env SPOTIFY_CLIENT_ID)."),
    client_secret: Optional[str] = typer.Option(
        None, "--client-secret", help="Spotify client secret (default: env SPOTIFY_CLIENT_SECRET)."
    ),
    user_agent: str = typer.Option("lastfm-export", "--user-agent", help="HTTP User-Agent header."),
) -> None:
    in_fmt = infer_format(in_path, in_format)
    out_fmt = infer_format(out, out_format)

    cid = get_env_or_value("SPOTIFY_CLIENT_ID", client_id)
    csec = get_env_or_value("SPOTIFY_CLIENT_SECRET", client_secret)

    records = _load_records(in_path, in_fmt)
    spotify = SpotifyClient(client_id=cid, client_secret=csec, user_agent=user_agent)

    sink = _resolve_sink(out, out_fmt, overwrite=overwrite)

    stats = SpotifyEnrichStats()
    out_records = _iter_enriched_records(
        records=records,
        spotify=spotify,
        dedupe=dedupe,
        only_missing=only_missing,
        stats=stats,
    )
    sink(out_records)

    typer.echo(f"Wrote enriched scrobbles to {out}")
    typer.echo(stats.to_log_line())