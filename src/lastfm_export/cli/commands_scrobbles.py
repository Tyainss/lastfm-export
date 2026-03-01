
from pathlib import Path
from typing import Optional

import typer

from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import ConfigError
from lastfm_export.cli._common import ensure_overwrite_allowed, get_env_or_value, infer_format, read_watermark
from lastfm_export.io.sinks import csv_sink, json_sink, ndjson_sink
from lastfm_export.pipelines.lastfm_export import export_scrobbles

scrobbles_app = typer.Typer(no_args_is_help=True)


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
    fmt = infer_format(out, format)
    ensure_overwrite_allowed(out=out, fmt=fmt, overwrite=overwrite)

    api_key_val = get_env_or_value("LASTFM_API_KEY", api_key)
    username_val = get_env_or_value("LASTFM_USERNAME", username)

    watermark = None
    if resume.lower() == "auto" and out.exists() and not overwrite:
        watermark = read_watermark(out, fmt)

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
        sink = json_sink(out, overwrite=overwrite)
    elif fmt == "csv":
        sink = csv_sink(out, overwrite=overwrite)
    else:
        raise ConfigError(f"Unsupported format: {fmt}")

    sink((s.to_record() for s in scrobbles))

    typer.echo(f"Wrote scrobbles to {out}")