import json
import pytest
from pathlib import Path

from typer.testing import CliRunner

from lastfm_export.cli.app import app
from lastfm_export.cli.commands_enrich import _record_to_scrobble, _spotify_from_record
from lastfm_export.models import SpotifyTrackEnrichment
from lastfm_export.io.readers import (
    read_csv_records,
    read_json_records,
    read_ndjson_records,
)
from lastfm_export.io.sinks import csv_sink, json_sink, ndjson_sink

runner = CliRunner()


@pytest.mark.parametrize("fmt", ["ndjson", "json", "csv"])
def test_inplace_spotify_enrichment_preserves_all_input_records(
    monkeypatch, tmp_path, fmt
):
    path = tmp_path / f"input.{fmt}"
    sinks = {"ndjson": ndjson_sink, "json": json_sink, "csv": csv_sink}
    readers = {
        "ndjson": read_ndjson_records,
        "json": read_json_records,
        "csv": read_csv_records,
    }
    rows = [
        {"artist_name": "A", "track_name": f"T{i}", "timestamp_unix": i}
        for i in range(100)
    ]
    sinks[fmt](path)(rows)

    class Spotify:
        def __init__(self, **kwargs):
            pass

        def build_track_enrichment(self, **kwargs):
            return None

    monkeypatch.setattr("lastfm_export.cli.commands_enrich.SpotifyClient", Spotify)
    result = runner.invoke(
        app,
        [
            "enrich",
            "spotify",
            "--in",
            str(path),
            "--out",
            str(path),
            "--overwrite",
            "--client-id",
            "test",
            "--client-secret",
            "test",
            "--progress",
            "off",
        ],
    )
    assert result.exit_code == 0, result.output
    output = list(readers[fmt](path))
    assert [row["track_name"] for row in output] == [row["track_name"] for row in rows]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("fmt", ["ndjson", "json", "csv"])
def test_failed_enrichment_preserves_existing_destination(monkeypatch, tmp_path, fmt):
    source = tmp_path / "input.ndjson"
    ndjson_sink(source)(
        [
            {"artist_name": "A", "track_name": f"T{i}", "timestamp_unix": i}
            for i in range(2)
        ]
    )
    out = tmp_path / f"out.{fmt}"
    out.write_bytes(b"previous output")

    class Spotify:
        def __init__(self, **kwargs):
            self.calls = 0

        def build_track_enrichment(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("API failed")
            return None

    monkeypatch.setattr("lastfm_export.cli.commands_enrich.SpotifyClient", Spotify)
    result = runner.invoke(
        app,
        [
            "enrich",
            "spotify",
            "--in",
            str(source),
            "--out",
            str(out),
            "--overwrite",
            "--client-id",
            "test",
            "--client-secret",
            "test",
            "--progress",
            "off",
        ],
    )
    assert result.exit_code != 0
    assert out.read_bytes() == b"previous output"
    assert not list(tmp_path.glob("*.tmp"))


def test_inplace_ndjson_append_is_rejected_before_changing_input(tmp_path):
    path = tmp_path / "input.ndjson"
    path.write_text('{"artist_name":"A","track_name":"T","timestamp_unix":1}\n')
    before = path.read_bytes()
    result = runner.invoke(
        app,
        [
            "enrich",
            "spotify",
            "--in",
            str(path),
            "--out",
            str(path),
            "--client-id",
            "test",
            "--client-secret",
            "test",
            "--progress",
            "off",
        ],
    )
    assert result.exit_code != 0
    assert "In-place enrichment requires --overwrite" in str(result.exception)
    assert path.read_bytes() == before


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
