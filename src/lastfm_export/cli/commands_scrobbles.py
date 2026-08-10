import json
import os
import time
from datetime import date, datetime, timezone
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
from lastfm_export.cli.progress import ProgressReporter
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import ConfigError, LastFMRecentTracksAccessError
from lastfm_export.io.readers import (
    read_csv_records,
    read_json_records,
    read_ndjson_records,
)
from lastfm_export.io.sinks import csv_sink, json_sink, ndjson_sink
from lastfm_export.pipelines.lastfm_export import (
    VerifiedProgress,
    collect_verified_scrobbles,
    export_scrobbles,
)

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
        None,
        "--page-limit",
        help="Stop after this many pages (fast mode only; useful for sampling).",
    ),
    acquisition_mode: str = typer.Option(
        "verified",
        "--acquisition-mode",
        help="verified | fast (default: verified).",
    ),
    integrity_policy: Optional[str] = typer.Option(
        None,
        "--integrity-policy",
        help="strict | warn (verified mode only; default: strict).",
    ),
    integrity_report: str = typer.Option(
        "auto",
        "--integrity-report",
        help="auto | always | never (default: auto).",
    ),
    progress: str = typer.Option(
        "auto",
        "--progress",
        help="auto | on | off (default: auto).",
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
    mode = acquisition_mode.lower()
    if mode not in {"verified", "fast"}:
        raise ConfigError("--acquisition-mode must be 'verified' or 'fast'.")
    if mode == "fast" and integrity_policy is not None:
        raise ConfigError(
            "--integrity-policy is only available with --acquisition-mode verified."
        )
    policy = (integrity_policy or "strict").lower()
    if mode == "verified" and policy not in {"strict", "warn"}:
        raise ConfigError("--integrity-policy must be 'strict' or 'warn'.")
    report_mode = integrity_report.lower()
    if report_mode not in {"auto", "always", "never"}:
        raise ConfigError("--integrity-report must be 'auto', 'always', or 'never'.")
    try:
        progress_reporter = ProgressReporter(progress)
    except ValueError as e:
        raise ConfigError("--progress must be 'auto', 'on', or 'off'.") from e
    if resume.lower() not in {"auto", "off"}:
        raise ConfigError("--resume must be 'auto' or 'off'.")
    if resume.lower() == "off":
        ensure_overwrite_allowed(out=out, fmt=fmt, overwrite=overwrite)
    if mode == "verified" and page_limit is not None:
        raise ConfigError("--page-limit is not supported by verified exports.")
    if mode == "fast":
        typer.echo(
            "WARNING: fast acquisition uses unverified sequential Last.fm pagination; "
            "records may be duplicated or omitted.",
            err=True,
        )

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

    progress_reporter.start(
        f"Starting {mode} export: {_utc_date(snapshot_from)} to {_utc_date(snapshot_to)} UTC"
    )

    def on_window_start(day: date, days_checked: int, tracks_collected: int) -> None:
        progress_reporter.update(
            f"Working: {days_checked} days checked, {tracks_collected} tracks collected; "
            f"fetching {day.isoformat()}"
        )

    def on_window_complete(event: VerifiedProgress) -> None:
        if _is_completed_full_quarter(
            day=event.day, from_unix=snapshot_from, to_unix=snapshot_to
        ):
            progress_reporter.milestone(
                f"Completed {_quarter_label(event.day)}: {event.days_checked} days checked, "
                f"{event.tracks_collected} tracks collected"
            )

    def on_fast_page(page: int, tracks_collected: int) -> None:
        progress_reporter.update(
            f"Working: {page} pages fetched, {tracks_collected} tracks collected"
        )

    try:
        if mode == "verified":
            scrobbles, reports = collect_verified_scrobbles(
                lastfm=lastfm,
                from_unix=snapshot_from,
                to_unix=snapshot_to,
                page_size=page_size,
                watermark=watermark,
                stop_on_violation=policy == "strict",
                on_window_start=on_window_start,
                on_window_complete=on_window_complete,
            )
        else:
            scrobbles = list(
                export_scrobbles(
                    lastfm=lastfm,
                    from_unix=snapshot_from,
                    to_unix=snapshot_to,
                    page_size=page_size,
                    page_limit=page_limit,
                    watermark=watermark,
                    on_page=on_fast_page,
                )
            )
            reports = []
    except LastFMRecentTracksAccessError as e:
        progress_reporter.close()
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    has_violations = any(not report.ok for report in reports)
    status = (
        "unverified"
        if mode == "fast"
        else "failed"
        if has_violations and policy == "strict"
        else "warnings"
        if has_violations
        else "ok"
    )
    strict_failure = has_violations and policy == "strict"
    should_write_report = (
        strict_failure
        or report_mode == "always"
        or (report_mode == "auto" and has_violations)
    )
    report_path = Path(f"{out}.integrity.json")
    if should_write_report:
        report = {
            "status": status,
            "acquisition_mode": mode,
            "from_unix": snapshot_from,
            "to_unix": snapshot_to,
            "watermark": watermark,
            "windows": [report.to_record() for report in reports],
        }
        if mode == "verified":
            report["integrity_policy"] = policy
        else:
            report["reason"] = (
                "Sequential Last.fm page-number pagination is known to repeat or skip records."
            )
        _write_integrity_report(report_path, report)
    if strict_failure:
        progress_reporter.finish(
            f"Stopped verified export after {len(scrobbles)} tracks from {len(reports)} days; "
            "integrity: failed"
        )
        typer.echo(
            f"Integrity check failed; destination was not modified. Report: {report_path}",
            err=True,
        )
        raise typer.Exit(code=1)

    merge_existing = out.exists() and not overwrite
    existing = _read_existing_records(out, fmt) if merge_existing else []
    new_records = [s.to_record(include_raw=include_raw) for s in scrobbles]
    # Preserve legacy NDJSON append behavior outside resume; resume keeps newest rows first.
    records = (
        new_records + existing if watermark is not None else existing + new_records
    )

    try:
        progress_reporter.update(f"Writing scrobbles to {out}", force=True)
        _write_records_transactionally(out=out, fmt=fmt, records=records)
    except LastFMRecentTracksAccessError as e:
        progress_reporter.close()
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    progress_reporter.finish(
        f"Completed {mode} export: {len(scrobbles)} tracks from {len(reports)} days; "
        f"integrity: {status}"
    )
    typer.echo(f"Wrote scrobbles to {out}")
    if has_violations and should_write_report:
        typer.echo(f"Warning: integrity violations recorded in {report_path}", err=True)
    elif has_violations:
        typer.echo(
            "Warning: integrity violations detected; no report was written "
            "(--integrity-report never).",
            err=True,
        )


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
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _utc_date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def _quarter_label(day: date) -> str:
    return f"{day.year} Q{((day.month - 1) // 3) + 1}"


def _is_completed_full_quarter(*, day: date, from_unix: int, to_unix: int) -> bool:
    quarter_month = ((day.month - 1) // 3) * 3 + 1
    if day != date(day.year, quarter_month, 1):
        return False
    quarter_start = int(
        datetime(day.year, quarter_month, 1, tzinfo=timezone.utc).timestamp()
    )
    next_quarter = (
        datetime(day.year + 1, 1, 1, tzinfo=timezone.utc)
        if quarter_month == 10
        else datetime(day.year, quarter_month + 3, 1, tzinfo=timezone.utc)
    )
    quarter_end = int(next_quarter.timestamp()) - 1
    return from_unix <= quarter_start and to_unix >= quarter_end
