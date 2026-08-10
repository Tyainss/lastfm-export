import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from typer.testing import CliRunner

from lastfm_export.cli.app import app
from lastfm_export.cli.commands_scrobbles import _is_completed_full_quarter
from lastfm_export.integrity import WindowReport
from lastfm_export.models import Scrobble


runner = CliRunner()


class _FakeLastFMClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_user_registration_unix(self):
        return 2


def _records():
    return [
        Scrobble(
            artist_name="A",
            track_name="T",
            album_name=None,
            timestamp_unix=1,
            raw={"name": "T"},
        )
    ]


def _report(*violations: str) -> WindowReport:
    return WindowReport(
        0,
        10,
        api_total=1,
        materialized_count=1,
        page_count=1,
        violations=list(violations),
    )


def _invoke(monkeypatch, out: Path, records, reports, *extra: str):
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USERNAME", "u")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.LastFMClient", _FakeLastFMClient
    )
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.collect_verified_scrobbles",
        lambda **kwargs: (records, reports),
    )
    return runner.invoke(
        app,
        [
            "scrobbles",
            "export",
            "--out",
            str(out),
            "--from-unix",
            "0",
            "--to-unix",
            "10",
            "--resume",
            "off",
            *extra,
        ],
    )


def _invoke_fast(monkeypatch, out: Path, records, *extra: str):
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USERNAME", "u")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.LastFMClient", _FakeLastFMClient
    )
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.export_scrobbles",
        lambda **kwargs: iter(records),
    )
    return runner.invoke(
        app,
        [
            "scrobbles",
            "export",
            "--out",
            str(out),
            "--from-unix",
            "0",
            "--to-unix",
            "10",
            "--resume",
            "off",
            "--acquisition-mode",
            "fast",
            *extra,
        ],
    )


def test_cli_verified_export_writes_rows_and_ok_report(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(monkeypatch, out, _records(), [_report()])
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["track_name"] == "T"
    report = json.loads(Path(f"{out}.integrity.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["to_unix"] == 10


def test_cli_verified_export_can_include_raw(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(monkeypatch, out, _records(), [_report()], "--include-raw")
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["raw"] == {"name": "T"}


def test_cli_progress_on_prints_verified_start_and_completion(
    monkeypatch, tmp_path: Path
):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(monkeypatch, out, _records(), [_report()], "--progress", "on")

    assert result.exit_code == 0
    assert "Starting verified export:" in result.output
    assert (
        "Completed verified export: 1 tracks from 1 days; integrity: ok"
        in result.output
    )


def test_cli_progress_auto_is_quiet_in_test_runner(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(monkeypatch, out, _records(), [_report()])

    assert result.exit_code == 0
    assert "Starting verified export:" not in result.output


def test_cli_strict_failure_preserves_existing_destination(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    out.write_text('{"track_name":"old"}\n', encoding="utf-8")
    result = _invoke(monkeypatch, out, _records(), [_report("exact overlap")])
    assert result.exit_code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["track_name"] == "old"
    report = json.loads(Path(f"{out}.integrity.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"


def test_cli_warn_publishes_unverified_output(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(
        monkeypatch,
        out,
        _records(),
        [_report("exact overlap")],
        "--integrity-policy",
        "warn",
    )
    assert result.exit_code == 0
    assert "Warning: integrity violations" in result.output
    report = json.loads(Path(f"{out}.integrity.json").read_text(encoding="utf-8"))
    assert report["status"] == "warnings"


def test_cli_rejects_unknown_integrity_policy(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(
        monkeypatch, out, _records(), [_report()], "--integrity-policy", "unsafe"
    )
    assert result.exit_code != 0
    assert "integrity-policy" in str(result.exception)


def test_cli_verified_rejects_page_limit(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(monkeypatch, out, _records(), [_report()], "--page-limit", "1")
    assert result.exit_code != 0
    assert "page-limit" in str(result.exception)


def test_cli_fast_export_is_unverified_and_honors_page_limit(
    monkeypatch, tmp_path: Path
):
    out = tmp_path / "scrobbles.ndjson"
    captured = {}
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USERNAME", "u")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.LastFMClient", _FakeLastFMClient
    )
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.export_scrobbles",
        lambda **kwargs: captured.update(kwargs) or iter(_records()),
    )
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.collect_verified_scrobbles",
        lambda **kwargs: pytest.fail("fast mode must not use verified acquisition"),
    )

    result = runner.invoke(
        app,
        [
            "scrobbles",
            "export",
            "--out",
            str(out),
            "--from-unix",
            "0",
            "--to-unix",
            "10",
            "--resume",
            "off",
            "--acquisition-mode",
            "fast",
            "--page-limit",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert "WARNING: fast acquisition" in result.output
    assert captured["from_unix"] == 0
    assert captured["to_unix"] == 10
    assert captured["page_limit"] == 3
    report = json.loads(Path(f"{out}.integrity.json").read_text(encoding="utf-8"))
    assert report["status"] == "unverified"
    assert report["acquisition_mode"] == "fast"
    assert "repeat or skip" in report["reason"]


def test_cli_fast_progress_on_prints_page_summary(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke_fast(monkeypatch, out, _records(), "--progress", "on")

    assert result.exit_code == 0
    assert "Starting fast export:" in result.output
    assert (
        "Completed fast export: 1 tracks from 0 days; integrity: unverified"
        in result.output
    )


def test_cli_fast_rejects_integrity_policy(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke_fast(monkeypatch, out, _records(), "--integrity-policy", "warn")
    assert result.exit_code != 0
    assert "integrity-policy" in str(result.exception)


def test_cli_rejects_unknown_progress_mode(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    result = _invoke(monkeypatch, out, _records(), [_report()], "--progress", "noisy")
    assert result.exit_code != 0
    assert "progress" in str(result.exception)


def test_completed_quarter_requires_the_full_utc_quarter_in_range():
    quarter_start = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    quarter_end = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()) - 1

    assert _is_completed_full_quarter(
        day=datetime(2026, 4, 1).date(),
        from_unix=quarter_start,
        to_unix=quarter_end,
    )
    assert not _is_completed_full_quarter(
        day=datetime(2026, 4, 2).date(),
        from_unix=quarter_start,
        to_unix=quarter_end,
    )
    assert not _is_completed_full_quarter(
        day=datetime(2026, 4, 1).date(),
        from_unix=quarter_start + 1,
        to_unix=quarter_end,
    )


def test_cli_freezes_now_and_uses_registration_fallback(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    captured = {}
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USERNAME", "u")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.LastFMClient", _FakeLastFMClient
    )
    monkeypatch.setattr("lastfm_export.cli.commands_scrobbles.time.time", lambda: 99)
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.collect_verified_scrobbles",
        lambda **kwargs: captured.update(kwargs) or (_records(), [_report()]),
    )
    result = runner.invoke(
        app, ["scrobbles", "export", "--out", str(out), "--resume", "off"]
    )
    assert result.exit_code == 0
    assert captured["from_unix"] == 2
    assert captured["to_unix"] == 99


def test_cli_resume_merges_through_staging(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    out.write_text(
        '{"artist_name":"Old","track_name":"Old","timestamp_unix":0}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USERNAME", "u")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.LastFMClient", _FakeLastFMClient
    )
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.collect_verified_scrobbles",
        lambda **kwargs: (_records(), [_report()]),
    )
    result = runner.invoke(
        app,
        [
            "scrobbles",
            "export",
            "--out",
            str(out),
            "--from-unix",
            "0",
            "--to-unix",
            "10",
        ],
    )
    assert result.exit_code == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["track_name"] for row in rows] == ["T", "Old"]


def test_cli_fast_resume_merges_through_staging(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    out.write_text(
        '{"artist_name":"Old","track_name":"Old","timestamp_unix":0}\n',
        encoding="utf-8",
    )
    captured = {}
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USERNAME", "u")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.LastFMClient", _FakeLastFMClient
    )
    monkeypatch.setattr(
        "lastfm_export.cli.commands_scrobbles.export_scrobbles",
        lambda **kwargs: captured.update(kwargs) or iter(_records()),
    )

    result = runner.invoke(
        app,
        [
            "scrobbles",
            "export",
            "--out",
            str(out),
            "--from-unix",
            "0",
            "--to-unix",
            "10",
            "--acquisition-mode",
            "fast",
        ],
    )

    assert result.exit_code == 0
    assert captured["watermark"] == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["track_name"] for row in rows] == ["T", "Old"]


def test_cli_ndjson_append_semantics_remain_transactional(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"
    out.write_text('{"track_name":"Old","timestamp_unix":0}\n', encoding="utf-8")
    result = _invoke(monkeypatch, out, _records(), [_report()])
    assert result.exit_code == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["track_name"] for row in rows] == ["Old", "T"]


@pytest.mark.parametrize("suffix", ["ndjson", "json", "csv"])
def test_cli_fast_export_uses_transactional_writer(
    monkeypatch, tmp_path: Path, suffix: str
):
    out = tmp_path / f"scrobbles.{suffix}"
    result = _invoke_fast(monkeypatch, out, _records())
    assert result.exit_code == 0
    assert out.exists()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("suffix", ["json", "csv"])
def test_cli_transactional_writer_supports_json_and_csv(
    monkeypatch, tmp_path: Path, suffix: str
):
    out = tmp_path / f"scrobbles.{suffix}"
    result = _invoke(monkeypatch, out, _records(), [_report()])
    assert result.exit_code == 0
    assert out.exists()
    assert not list(tmp_path.glob(".*.tmp"))
