import json
import os
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

import typer

from lastfm_export.cli._common import (
    ensure_overwrite_allowed,
    get_env_or_value,
    infer_format,
    read_watermark,
)
from lastfm_export.cli.dates import resolve_time_window
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import ConfigError, LastFMRecentTracksAccessError
from lastfm_export.io.readers import (
    read_csv_records,
    read_json_records,
    read_ndjson_records,
)
from lastfm_export.io.sinks import csv_sink, json_sink, ndjson_sink
from lastfm_export.pipelines.lastfm_export import collect_verified_scrobbles

scrobbles_app = typer.Typer(no_args_is_help=True)


@scrobbles_app.command("export")
def export_cmd(
    out: Path = typer.Option(..., "--out", help="Output file path."),
    format: Optional[str] = typer.Option(
        None, "--format", help="ndjson | json | csv (default: inferred from --out)."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite output file."),
    include_raw: bool = typer.Option(
        False,
        "--include-raw",
        help="Include the original Last.fm track payload in each exported record.",
    ),
    resume: str = typer.Option("auto", "--resume", help="auto | off"),
    from_text: Optional[str] = typer.Option(
        None,
        "--from",
        help="Start date/datetime (UTC). YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS",
    ),
    to_text: Optional[str] = typer.Option(
        None, "--to", help="End date/datetime (UTC). YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS"
    ),
    from_unix: Optional[int] = typer.Option(
        None, "--from-unix", help="Inclusive start timestamp (unix seconds, UTC)."
    ),
    to_unix: Optional[int] = typer.Option(
        None, "--to-unix", help="Inclusive end timestamp (unix seconds, UTC)."
    ),
    page_size: int = typer.Option(200, "--page-size", help="Last.fm page size."),
    page_limit: Optional[int] = typer.Option(
        None, "--page-limit", help="Stop after this many pages."
    ),
    integrity_policy: str = typer.Option(
        "strict", "--integrity-policy", help="strict | warn (default: strict)."
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="Last.fm API key (default: env LASTFM_API_KEY)."
    ),
    username: Optional[str] = typer.Option(
        None, "--username", help="Last.fm username (default: env LASTFM_USERNAME)."
    ),
    user_agent: str = typer.Option(
        "lastfm-export", "--user-agent", help="HTTP User-Agent header."
    ),
) -> None:
    fmt = infer_format(out, format)
    policy = integrity_policy.lower()
    if policy not in {"strict", "warn"}:
        raise ConfigError("--integrity-policy must be 'strict' or 'warn'.")
    if resume.lower() not in {"auto", "off"}:
        raise ConfigError("--resume must be 'auto' or 'off'.")
    if resume.lower() == "off":
        ensure_overwrite_allowed(out=out, fmt=fmt, overwrite=overwrite)
    if page_limit is not None:
        raise ConfigError("--page-limit is not supported by verified exports.")

    api_key_val = get_env_or_value("LASTFM_API_KEY", api_key)
    username_val = get_env_or_value("LASTFM_USERNAME", username)

    watermark = None
    if resume.lower() == "auto" and out.exists() and not overwrite:
        watermark = read_watermark(out, fmt)

    window = resolve_time_window(
        from_unix=from_unix,
        to_unix=to_unix,
        from_text=from_text,
        to_text=to_text,
    )

    lastfm = LastFMClient(
        api_key=api_key_val, username=username_val, user_agent=user_agent
    )
    snapshot_to = window.to_unix_inclusive
    if snapshot_to is None:
        snapshot_to = int(time.time())
    snapshot_from = window.from_unix
    if snapshot_from is None:
        snapshot_from = lastfm.get_user_registration_unix()
    if snapshot_from is None:
        raise ConfigError("Could not determine account registration time; use --from.")

    try:
        scrobbles, reports = collect_verified_scrobbles(
            lastfm=lastfm,
            from_unix=snapshot_from,
            to_unix=snapshot_to,
            page_size=page_size,
            watermark=watermark,
            stop_on_violation=policy == "strict",
        )
    except LastFMRecentTracksAccessError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    has_violations = any(not report.ok for report in reports)
    status = "failed" if has_violations and policy == "strict" else (
        "warnings" if has_violations else "ok"
    )
    report_path = Path(f"{out}.integrity.json")
    _write_integrity_report(
        report_path,
        {
            "status": status,
            "integrity_policy": policy,
            "from_unix": snapshot_from,
            "to_unix": snapshot_to,
            "watermark": watermark,
            "windows": [report.to_record() for report in reports],
        },
    )
    if has_violations and policy == "strict":
        typer.echo(f"Integrity check failed; destination was not modified. Report: {report_path}", err=True)
        raise typer.Exit(code=1)

    merge_existing = out.exists() and not overwrite
    existing = _read_existing_records(out, fmt) if merge_existing else []
    new_records = [s.to_record(include_raw=include_raw) for s in scrobbles]
    # Preserve legacy NDJSON append behavior outside resume; resume keeps newest rows first.
    records = new_records + existing if watermark is not None else existing + new_records

    try:
        _write_records_transactionally(out=out, fmt=fmt, records=records)
    except LastFMRecentTracksAccessError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Wrote scrobbles to {out}")
    if has_violations:
        typer.echo(f"Warning: integrity violations recorded in {report_path}", err=True)


def _read_existing_records(path: Path, fmt: str) -> list[dict]:
    if fmt == "ndjson":
        return list(read_ndjson_records(path))
    if fmt == "json":
        return list(read_json_records(path))
    if fmt == "csv":
        return list(read_csv_records(path))
    raise ConfigError(f"Unsupported format: {fmt}")


def _write_records_transactionally(*, out: Path, fmt: str, records: list[dict]) -> None:
    temporary = out.with_name(f".{out.name}.{uuid4().hex}.tmp")
    try:
        if fmt == "ndjson":
            ndjson_sink(temporary, overwrite=True)(records)
        elif fmt == "json":
            json_sink(temporary, overwrite=True)(records)
        elif fmt == "csv":
            csv_sink(temporary, overwrite=True)(records)
        else:
            raise ConfigError(f"Unsupported format: {fmt}")
        os.replace(temporary, out)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_integrity_report(path: Path, report: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
