import os
from pathlib import Path
from typing import Optional

import typer

from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import ConfigError
from lastfm_export.io.sinks import csv_sink, json_sink, ndjson_sink
from lastfm_export.io.state import (
    read_watermark_from_csv,
    read_watermark_from_json,
    read_watermark_from_ndjson,
)
from lastfm_export.pipelines.lastfm_export import export_scrobbles

scrobbles_app = typer.Typer(no_args_is_help=True)


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


@scrobbles_app.command("export")
def export_cmd(
    out: Path = typer.Option(..., "--out", help="Output file path."),
    format: Optional[str] = typer.Option(None, "--format", help="ndjson | json | csv (default: inferred from --out)."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite output file."),
    resume: str = typer.Option("auto", "--resume", help="auto | off"),
    from_unix: Optional[int] = typer.Option(None, "--from", help="Inclusive start timestamp (unix seconds)."),
    to_unix: Optional[int] = typer.Option(None, "--to", help="Inclusive end timestamp (unix seconds)."),
    page_size: int = typer.Option(200, "--page-size", help="Last.fm page size."),
    page_limit: Optional[int] = typer.Option(None, "--page-limit", help="Stop after this many pages."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Last.fm API key (default: env LASTFM_API_KEY)."),
    username: Optional[str] = typer.Option(None, "--username", help="Last.fm username (default: env LASTFM_USERNAME)."),
    user_agent: str = typer.Option("lastfm-export", "--user-agent", help="HTTP User-Agent header."),
) -> None:
    fmt = _infer_format(out, format)

    api_key_val = _get_env_or_value("LASTFM_API_KEY", api_key)
    username_val = _get_env_or_value("LASTFM_USERNAME", username)

    watermark = None
    if resume.lower() == "auto" and out.exists() and not overwrite:
        if fmt == "ndjson":
            watermark = read_watermark_from_ndjson(out)
        elif fmt == "json":
            watermark = read_watermark_from_json(out)
        elif fmt == "csv":
            watermark = read_watermark_from_csv(out)

    lastfm = LastFMClient(api_key=api_key_val, username=username_val, user_agent=user_agent)
    scrobbles = export_scrobbles(
        lastfm=lastfm,
        from_unix=from_unix,
        to_unix=to_unix,
        page_size=page_size,
        page_limit=page_limit,
        watermark=watermark,
    )

    if fmt == "ndjson":
        sink = ndjson_sink(out, overwrite=overwrite)
    elif fmt == "json":
        sink = json_sink(out, overwrite=True)
    elif fmt == "csv":
        sink = csv_sink(out, overwrite=True)
    else:
        raise ConfigError(f"Unsupported format: {fmt}")

    sink((s.to_record() for s in scrobbles))

    typer.echo(f"Wrote scrobbles to {out}")