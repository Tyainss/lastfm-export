import json

import pytest
import responses
from typer.testing import CliRunner

from lastfm_export.cli.app import app
from lastfm_export.errors import HttpRequestError
from lastfm_export.io.artist_metadata import read_collection

runner = CliRunner()
MBID = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"


def install_fake(monkeypatch, factory):
    def client(**kwargs):
        return factory()

    monkeypatch.setattr("lastfm_export.cli.commands_artists.LastFMClient", client)
    monkeypatch.setenv("LASTFM_API_KEY", "test-key")


def input_file(tmp_path, fmt):
    path = tmp_path / f"artists.{fmt}"
    if fmt == "json":
        path.write_text(json.dumps([{"artist_name": "A"}]))
    elif fmt == "ndjson":
        path.write_text('{"artist_name": "A"}\n')
    else:
        path.write_text("artist_name,mbid\nA,\n")
    return path


@pytest.mark.parametrize("fmt", ["json", "ndjson", "csv"])
def test_cli_input_formats_and_credential_free_reuse(
    tmp_path, monkeypatch, metadata_client, fmt
):
    install_fake(monkeypatch, metadata_client)
    path, out = input_file(tmp_path, fmt), tmp_path / "run"
    result = runner.invoke(
        app,
        [
            "enrich",
            "artists",
            "--in",
            str(path),
            "--out",
            str(out),
            "--progress",
            "off",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(read_collection(out)[1]) == 2
    monkeypatch.delenv("LASTFM_API_KEY")
    reused = runner.invoke(app, ["enrich", "artists", "--out", str(out), "--reuse"])
    assert reused.exit_code == 0, reused.output
    assert "skipped=2" in reused.output


@pytest.mark.parametrize(
    "flags",
    [
        ["--operation", "unknown"],
        ["--similar-limit", "0"],
        ["--progress", "bad"],
        ["--resume", "--reuse"],
        ["--retry-failed"],
    ],
)
def test_cli_invalid_configuration_has_exit_two(
    tmp_path, monkeypatch, metadata_client, flags
):
    install_fake(monkeypatch, metadata_client)
    path = input_file(tmp_path, "json")
    out = tmp_path / "run"
    result = runner.invoke(
        app, ["enrich", "artists", "--in", str(path), "--out", str(out), *flags]
    )
    assert result.exit_code == 2, result.output
    assert not out.exists()


def test_cli_rejects_scrobble_mbid_as_artist_input(tmp_path):
    path = tmp_path / "scrobbles.json"
    path.write_text(json.dumps([{"artist_name": "A", "track_name": "B", "mbid": MBID}]))
    result = runner.invoke(
        app, ["enrich", "artists", "--in", str(path), "--out", str(tmp_path / "run")]
    )
    assert result.exit_code == 2
    assert "scrobble records" in result.output


def test_cli_failure_exit_and_new_retry_snapshot(
    tmp_path, monkeypatch, metadata_client
):
    failed_client = metadata_client(
        [
            HttpRequestError(
                "GET", "https://example.com", message="unavailable", retryable=True
            ),
            "empty",
        ]
    )
    install_fake(monkeypatch, lambda: failed_client)
    source, retry = tmp_path / "source", tmp_path / "retry"
    result = runner.invoke(
        app,
        [
            "enrich",
            "artists",
            "--in",
            str(input_file(tmp_path, "json")),
            "--out",
            str(source),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "failed=1" in result.output and "empty=1" in result.output
    install_fake(monkeypatch, metadata_client)
    retried = runner.invoke(
        app, ["enrich", "artists", "--retry-from", str(source), "--out", str(retry)]
    )
    assert retried.exit_code == 0, retried.output
    assert len(read_collection(retry)[1]) == 1


@responses.activate
def test_musicbrainz_only_needs_no_lastfm_or_spotify_credentials(tmp_path, monkeypatch):
    for key in (
        "LASTFM_API_KEY",
        "LASTFM_USERNAME",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MUSICBRAINZ_USER_AGENT", "tests/1.0 (https://example.com)")
    monkeypatch.setattr("lastfm_export.clients.musicbrainz._pacer.wait", lambda: None)
    responses.get("https://musicbrainz.org/ws/2/artist", json={"artists": []})
    result = runner.invoke(
        app,
        [
            "enrich",
            "artists",
            "--in",
            str(input_file(tmp_path, "json")),
            "--out",
            str(tmp_path / "run"),
            "--operation",
            "musicbrainz_search",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "empty=1" in result.output


@responses.activate
def test_full_cli_workflow_preserves_independent_provider_payloads(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LASTFM_API_KEY", "private-api-key")
    monkeypatch.delenv("LASTFM_USERNAME", raising=False)
    monkeypatch.setenv("MUSICBRAINZ_USER_AGENT", "tests/1.0 (https://example.com)")
    monkeypatch.setattr("lastfm_export.clients.musicbrainz._pacer.wait", lambda: None)
    responses.get(
        "https://ws.audioscrobbler.com/2.0/",
        json={"toptags": {"tag": [{"name": "jazz", "count": 80}]}},
    )
    responses.get(
        "https://ws.audioscrobbler.com/2.0/", json={"similarartists": {"artist": []}}
    )
    responses.get(
        f"https://musicbrainz.org/ws/2/artist/{MBID}",
        json={"id": MBID, "name": "A", "genres": [], "life-span": {"begin": "1990"}},
    )
    path = tmp_path / "artists.json"
    path.write_text(json.dumps([{"artist_name": "A", "mbid": MBID}]))
    out = tmp_path / "run"
    result = runner.invoke(
        app,
        [
            "enrich",
            "artists",
            "--in",
            str(path),
            "--out",
            str(out),
            "--operation",
            "lastfm_tags",
            "--operation",
            "lastfm_similar",
            "--operation",
            "musicbrainz_lookup",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest, latest = read_collection(out)
    assert [value.status for value in latest.values()] == [
        "success",
        "empty",
        "success",
    ]
    assert len(manifest["plan"]["requests"]) == 3
    assert "private-api-key" not in (out / "results.ndjson").read_text()
    assert "private-api-key" not in (out / "manifest.json").read_text()


def test_cli_interrupt_then_resume_and_progress_off(
    tmp_path, monkeypatch, metadata_client
):
    interrupted = metadata_client(["success", KeyboardInterrupt()])
    install_fake(monkeypatch, lambda: interrupted)
    out = tmp_path / "run"
    first = runner.invoke(
        app,
        [
            "enrich",
            "artists",
            "--in",
            str(input_file(tmp_path, "json")),
            "--out",
            str(out),
            "--progress",
            "off",
        ],
    )
    assert first.exit_code == 130
    install_fake(monkeypatch, metadata_client)
    resumed = runner.invoke(
        app, ["enrich", "artists", "--out", str(out), "--resume", "--progress", "off"]
    )
    assert resumed.exit_code == 0, resumed.output
    assert "skipped=1" in resumed.output
    assert "Artist metadata collection:" not in resumed.output


@responses.activate
def test_retry_of_only_musicbrainz_failures_needs_no_lastfm_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "private-api-key")
    monkeypatch.setenv("MUSICBRAINZ_USER_AGENT", "tests/1.0 (https://example.com)")
    monkeypatch.setattr("lastfm_export.clients.musicbrainz._pacer.wait", lambda: None)
    responses.get("https://ws.audioscrobbler.com/2.0/", json={"toptags": {"tag": []}})
    responses.get(
        "https://musicbrainz.org/ws/2/artist", json={"unexpected": "response"}
    )
    source, retry = tmp_path / "source", tmp_path / "retry"
    first = runner.invoke(
        app,
        [
            "enrich",
            "artists",
            "--in",
            str(input_file(tmp_path, "json")),
            "--out",
            str(source),
            "--operation",
            "lastfm_tags",
            "--operation",
            "musicbrainz_search",
        ],
    )
    assert first.exit_code == 1, first.output
    monkeypatch.delenv("LASTFM_API_KEY")
    responses.get("https://musicbrainz.org/ws/2/artist", json={"artists": []})
    second = runner.invoke(
        app, ["enrich", "artists", "--out", str(retry), "--retry-from", str(source)]
    )
    assert second.exit_code == 0, second.output
    assert len(read_collection(retry)[1]) == 1
