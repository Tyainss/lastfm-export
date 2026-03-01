import json
from pathlib import Path

from typer.testing import CliRunner

from lastfm_export.cli.app import app
from lastfm_export.models import SpotifyTrackEnrichment

runner = CliRunner()


def test_cli_enrich_spotify_reads_ndjson_and_writes_ndjson(monkeypatch, tmp_path: Path):
    in_path = tmp_path / "scrobbles.ndjson"
    out = tmp_path / "enriched.ndjson"

    in_path.write_text(
        json.dumps(
            {"artist_name": "A", "track_name": "T", "album_name": None, "timestamp_unix": 1, "mbid": None}
        )
        + "\n",
        encoding="utf-8",
    )

    class _FakeSpotifyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_track_enrichment(self, *, track_name: str, artist_name: str):
            return SpotifyTrackEnrichment(
                spotify_track_id="sid",
                spotify_artist_id=None,
                spotify_album_id=None,
                spotify_track_url=None,
                popularity=None,
            )

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")

    monkeypatch.setattr("lastfm_export.cli.commands_enrich.SpotifyClient", _FakeSpotifyClient)

    result = runner.invoke(
        app,
        ["enrich", "spotify", "--in", str(in_path), "--out", str(out), "--in-format", "ndjson", "--out-format", "ndjson"],
    )
    assert result.exit_code == 0
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert '"spotify_track_id": "sid"' in txt