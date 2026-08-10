import json
from pathlib import Path

from typer.testing import CliRunner

from lastfm_export.cli.app import app
from lastfm_export.cli.commands_enrich import _record_to_scrobble, _spotify_from_record
from lastfm_export.models import SpotifyTrackEnrichment

runner = CliRunner()


def test_record_rehydration_only_uses_explicit_raw_payloads():
    lastfm_raw = {"name": "T"}
    record = {
        "artist_name": "A",
        "track_name": "T",
        "album_name": None,
        "timestamp_unix": 1,
        "mbid": None,
        "raw": lastfm_raw,
    }

    record_without_raw = {k: v for k, v in record.items() if k != "raw"}

    assert _record_to_scrobble(record).raw == lastfm_raw
    assert _record_to_scrobble(record_without_raw).raw is None


def test_spotify_rehydration_only_uses_explicit_raw_payload():
    spotify_raw = {"id": "sid", "name": "T"}
    value = {
        "spotify_track_id": "sid",
        "spotify_artist_id": None,
        "spotify_album_id": None,
        "spotify_track_url": None,
        "popularity": 10,
        "raw": spotify_raw,
    }

    value_without_raw = {k: v for k, v in value.items() if k != "raw"}

    assert _spotify_from_record(value).raw == spotify_raw
    assert _spotify_from_record(value_without_raw).raw is None


def test_cli_enrich_spotify_reads_ndjson_and_writes_ndjson(monkeypatch, tmp_path: Path):
    in_path = tmp_path / "scrobbles.ndjson"
    out = tmp_path / "enriched.ndjson"

    in_path.write_text(
        json.dumps(
            {
                "artist_name": "A",
                "track_name": "T",
                "album_name": None,
                "timestamp_unix": 1,
                "mbid": None,
            }
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
                raw={"id": "sid"},
            )

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")

    monkeypatch.setattr(
        "lastfm_export.cli.commands_enrich.SpotifyClient", _FakeSpotifyClient
    )

    result = runner.invoke(
        app,
        [
            "enrich",
            "spotify",
            "--in",
            str(in_path),
            "--out",
            str(out),
            "--in-format",
            "ndjson",
            "--out-format",
            "ndjson",
        ],
    )
    assert result.exit_code == 0
    assert out.exists()
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["spotify"]["spotify_track_id"] == "sid"
    assert "raw" not in record["spotify"]


def test_cli_enrich_progress_on_prints_start_and_keeps_final_stats(
    monkeypatch, tmp_path: Path
):
    in_path = tmp_path / "scrobbles.ndjson"
    out = tmp_path / "enriched.ndjson"
    in_path.write_text(
        json.dumps(
            {
                "artist_name": "A",
                "track_name": "T",
                "album_name": None,
                "timestamp_unix": 1,
                "mbid": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class _FakeSpotifyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_track_enrichment(self, *, track_name: str, artist_name: str):
            return None

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_enrich.SpotifyClient", _FakeSpotifyClient
    )

    result = runner.invoke(
        app,
        [
            "enrich",
            "spotify",
            "--in",
            str(in_path),
            "--out",
            str(out),
            "--progress",
            "on",
        ],
    )

    assert result.exit_code == 0
    assert "Starting Spotify enrichment:" in result.output
    assert "Working: 1 records processed; 1 Spotify lookups" in result.output
    assert "Spotify enrich stats: records=1" in result.output


def test_cli_enrich_spotify_can_include_raw(monkeypatch, tmp_path: Path):
    in_path = tmp_path / "scrobbles.ndjson"
    out = tmp_path / "enriched.ndjson"
    spotify_raw = {"id": "sid", "name": "T"}

    in_path.write_text(
        json.dumps(
            {
                "artist_name": "A",
                "track_name": "T",
                "album_name": None,
                "timestamp_unix": 1,
                "mbid": None,
            }
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
                raw=spotify_raw,
            )

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")
    monkeypatch.setattr(
        "lastfm_export.cli.commands_enrich.SpotifyClient", _FakeSpotifyClient
    )

    result = runner.invoke(
        app,
        [
            "enrich",
            "spotify",
            "--in",
            str(in_path),
            "--out",
            str(out),
            "--in-format",
            "ndjson",
            "--out-format",
            "ndjson",
            "--include-raw",
        ],
    )

    assert result.exit_code == 0
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["spotify"]["raw"] == spotify_raw
