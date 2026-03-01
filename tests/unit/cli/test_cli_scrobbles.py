from pathlib import Path

from typer.testing import CliRunner

from lastfm_export.cli.app import app
from lastfm_export.models import Scrobble

runner = CliRunner()


def test_cli_scrobbles_export_writes_ndjson(monkeypatch, tmp_path: Path):
    out = tmp_path / "scrobbles.ndjson"

    class _FakeLastFMClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    def fake_export_scrobbles(**kwargs):
        yield Scrobble(artist_name="A", track_name="T", album_name=None, timestamp_unix=1)

    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USERNAME", "u")

    monkeypatch.setattr("lastfm_export.cli.commands_scrobbles.LastFMClient", _FakeLastFMClient)
    monkeypatch.setattr("lastfm_export.cli.commands_scrobbles.export_scrobbles", fake_export_scrobbles)

    result = runner.invoke(app, ["scrobbles", "export", "--out", str(out), "--format", "ndjson", "--resume", "off"])
    assert result.exit_code == 0
    assert out.exists()
    txt = out.read_text(encoding="utf-8").strip()
    assert '"track_name": "T"' in txt