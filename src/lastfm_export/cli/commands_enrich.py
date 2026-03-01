import os
from pathlib import Path
from typing import Any, Iterable, Optional

import typer

from lastfm_export.clients.spotify import SpotifyClient
from lastfm_export.errors import ConfigError
from lastfm_export.io.readers import read_csv_records, read_json_records, read_ndjson_records
from lastfm_export.io.sinks import csv_sink, json_sink, ndjson_sink
from lastfm_export.models import Scrobble
from lastfm_export.pipelines.spotify_enrich import enrich_scrobbles_with_spotify

enrich_app = typer.Typer(no_args_is_help=True)


def _get_env_or_value(name: str, value: Optional[str]) -> str:
    if value:
        return value
    env = os.getenv(name)
    if env:
        return env
    raise ConfigError(f"Missing required config: {name}")


def _infer_format(path: Path, fmt: Optional[str]) -> str:
    if fmt:
        return fmt.lower()
    ext = path.suffix.lower()
    if ext == ".ndjson":
        return "ndjson"
    if ext == ".json":
        return "json"
    if ext == ".csv":
        return "csv"
    raise ConfigError("Could not infer format from file extension. Use --format.")


def _records_to_scrobbles(records: Iterable[dict[str, Any]]) -> Iterable[Scrobble]:
    for rec in records:
        ts = rec.get("timestamp_unix")
        if ts is None:
            raise ValueError("Missing timestamp_unix in input record")
        yield Scrobble(
            artist_name=str(rec.get("artist_name") or ""),
            track_name=str(rec.get("track_name") or ""),
            album_name=rec.get("album_name"),
            timestamp_unix=int(ts),
            mbid=rec.get("mbid"),
            raw=rec,
        )


@enrich_app.command("spotify")
def enrich_spotify_cmd(
    in_path: Path = typer.Option(..., "--in", help="Input file path containing scrobbles."),
    out: Path = typer.Option(..., "--out", help="Output file path."),
    in_format: Optional[str] = typer.Option(None, "--in-format", help="ndjson | json | csv (default: inferred)."),
    out_format: Optional[str] = typer.Option(None, "--out-format", help="ndjson | json | csv (default: inferred)."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite output file."),
    dedupe: bool = typer.Option(True, "--dedupe/--no-dedupe", help="Cache lookups by (artist, track)."),
    client_id: Optional[str] = typer.Option(None, "--client-id", help="Spotify client id (default: env SPOTIFY_CLIENT_ID)."),
    client_secret: Optional[str] = typer.Option(
        None, "--client-secret", help="Spotify client secret (default: env SPOTIFY_CLIENT_SECRET)."
    ),
    user_agent: str = typer.Option("lastfm-export", "--user-agent", help="HTTP User-Agent header."),
) -> None:
    in_fmt = _infer_format(in_path, in_format)
    out_fmt = _infer_format(out, out_format)

    cid = _get_env_or_value("SPOTIFY_CLIENT_ID", client_id)
    csec = _get_env_or_value("SPOTIFY_CLIENT_SECRET", client_secret)

    if in_fmt == "ndjson":
        records = read_ndjson_records(in_path)
    elif in_fmt == "json":
        records = read_json_records(in_path)
    elif in_fmt == "csv":
        records = read_csv_records(in_path)
    else:
        raise ConfigError(f"Unsupported input format: {in_fmt}")

    scrobbles = _records_to_scrobbles(records)

    spotify = SpotifyClient(client_id=cid, client_secret=csec, user_agent=user_agent)
    enriched = enrich_scrobbles_with_spotify(spotify=spotify, scrobbles=scrobbles, dedupe=dedupe)

    if out_fmt == "ndjson":
        sink = ndjson_sink(out, overwrite=overwrite)
    elif out_fmt == "json":
        sink = json_sink(out, overwrite=True)
    elif out_fmt == "csv":
        sink = csv_sink(out, overwrite=True)
    else:
        raise ConfigError(f"Unsupported output format: {out_fmt}")

    sink((e.to_record() for e in enriched))

    typer.echo(f"Wrote enriched scrobbles to {out}")