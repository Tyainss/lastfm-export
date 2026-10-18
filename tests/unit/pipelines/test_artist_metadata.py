import json

import pytest

from lastfm_export.errors import CollectionError, ConfigError, HttpRequestError
from lastfm_export.io.artist_metadata import read_collection
from lastfm_export.models import ArtistInput
from lastfm_export.pipelines.artist_metadata import collect_artist_metadata

MBID = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"


def error(*, fatal=False, code=None):
    return HttpRequestError(
        "GET",
        "https://example.com",
        200,
        "Service error",
        payload={"error": code},
        provider_code=code,
        retryable=not fatal,
        fatal=fatal,
    )


def rows(path):
    return [
        json.loads(line) for line in (path / "results.ndjson").read_text().splitlines()
    ]


def collect(path, client, artists=None, **kwargs):
    return collect_artist_metadata(
        out=path, artists=artists or [ArtistInput("A")], lastfm=client, **kwargs
    )


def test_exact_duplicates_share_requests_but_preserve_input_mapping(
    tmp_path, metadata_client
):
    client = metadata_client()
    artists = [
        ArtistInput("A", MBID),
        ArtistInput("A", MBID),
        ArtistInput("Alias", MBID),
        ArtistInput("a", MBID),
    ]
    summary = collect(tmp_path / "run", client, artists)
    manifest, latest = read_collection(tmp_path / "run")
    assert summary.total == summary.attempted == summary.success == 6
    assert len(client.calls) == 6
    assert len(manifest["plan"]["inputs"]) == 4
    assert (
        manifest["plan"]["input_requests"][0] == manifest["plan"]["input_requests"][1]
    )
    assert len(latest) == 6


def test_resume_skips_durable_result_and_reuse_is_read_only(tmp_path, metadata_client):
    path = tmp_path / "run"
    client = metadata_client(["empty", KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        collect(path, client)
    assert not (path / ".writer.lock").exists()
    assert len(rows(path)) == 1
    resumed = metadata_client()
    summary = collect_artist_metadata(out=path, lastfm=resumed, mode="resume")
    assert summary.attempted == 1
    assert summary.skipped == 1
    assert summary.empty == 1
    assert summary.completed
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.iterdir()}
    reuse = collect_artist_metadata(out=path, mode="reuse")
    assert reuse.attempted == 0
    assert reuse.skipped == 2
    assert before == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.iterdir()
    }
    with pytest.raises(ConfigError, match="read-only"):
        collect_artist_metadata(out=path, mode="resume", lastfm=resumed)


@pytest.mark.parametrize(
    "change",
    [
        {"similar_limit": 7},
        {"autocorrect": True},
        {"artists": [ArtistInput("B")]},
        {"operations": ["lastfm_tags"]},
    ],
)
def test_resume_rejects_changed_inputs_and_settings(tmp_path, change, metadata_client):
    path = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        collect(path, metadata_client([KeyboardInterrupt()]))
    with pytest.raises(ConfigError, match="differ"):
        collect_artist_metadata(
            out=path, mode="resume", lastfm=metadata_client(), **change
        )


def test_resume_does_not_implicitly_retry_failures(tmp_path, metadata_client):
    path = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        collect(path, metadata_client([error(), KeyboardInterrupt()]))
    client = metadata_client()
    summary = collect_artist_metadata(out=path, mode="resume", lastfm=client)
    assert summary.failed == 1
    assert summary.attempted == 1
    assert [row["status"] for row in rows(path)] == ["failed", "success"]


def test_explicit_failure_retry_preserves_earlier_attempts(tmp_path, metadata_client):
    path = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        collect(path, metadata_client([error(), KeyboardInterrupt()]))
    summary = collect_artist_metadata(
        out=path, mode="resume", retry_failed=True, lastfm=metadata_client()
    )
    assert summary.failed == 0
    results = rows(path)
    assert [row["attempt"] for row in results] == [1, 2, 1]
    assert results[0]["request_id"] == results[1]["request_id"]
    assert results[0]["status"] == "failed"
    assert results[1]["status"] == "success"
    assert results[0]["raw_response"] == {"error": None}


def test_retry_finalized_failures_creates_a_separate_snapshot(
    tmp_path, metadata_client
):
    path = tmp_path / "source"
    collect(path, metadata_client(["success", error()]))
    before = (path / "results.ndjson").read_bytes()
    retry = tmp_path / "retry"
    client = metadata_client()
    summary = collect_artist_metadata(out=retry, retry_from=path, lastfm=client)
    source_manifest, _ = read_collection(path)
    manifest, _ = read_collection(retry)
    assert summary.total == summary.success == 1
    assert len(client.calls) == 1
    assert client.calls[0][0] == "similar"
    assert manifest["source_collection_id"] == source_manifest["collection_id"]
    assert manifest["collection_id"] != source_manifest["collection_id"]
    assert (path / "results.ndjson").read_bytes() == before
    assert collect_artist_metadata(out=retry, mode="reuse").total == 1


def test_retry_snapshot_can_itself_resume(tmp_path, metadata_client):
    source, retry = tmp_path / "source", tmp_path / "retry"
    collect(source, metadata_client([error(), "success"]))
    with pytest.raises(KeyboardInterrupt):
        collect_artist_metadata(
            out=retry, retry_from=source, lastfm=metadata_client([KeyboardInterrupt()])
        )
    summary = collect_artist_metadata(
        out=retry, mode="resume", lastfm=metadata_client()
    )
    assert summary.total == summary.success == 1


def test_new_directory_required_and_refresh_is_independent(tmp_path, metadata_client):
    path = tmp_path / "run"
    collect(path, metadata_client())
    with pytest.raises(CollectionError, match="Destination exists"):
        collect(path, metadata_client())
    fresh = collect(tmp_path / "refresh", metadata_client())
    original = collect_artist_metadata(out=path, mode="reuse")
    assert fresh.collection_id != original.collection_id


def test_global_credentials_failure_is_durable_and_stops_remaining_work(
    tmp_path, metadata_client
):
    path = tmp_path / "run"
    client = metadata_client([error(fatal=True, code=10)])
    with pytest.raises(CollectionError, match="global credentials"):
        collect(path, client)
    manifest, latest = read_collection(path)
    assert len(client.calls) == len(latest) == 1
    assert manifest["state"] == "collecting"
    assert rows(path)[0]["provider_code"] == 10
    summary = collect_artist_metadata(
        out=path, mode="resume", retry_failed=True, lastfm=metadata_client()
    )
    assert summary.completed and summary.success == 2


def test_unexpected_programming_error_is_not_persisted_as_missing(
    tmp_path, metadata_client
):
    path = tmp_path / "run"
    with pytest.raises(RuntimeError, match="bug"):
        collect(path, metadata_client([RuntimeError("bug")]))
    assert not (path / "results.ndjson").exists()
    assert not (path / ".writer.lock").exists()


def test_not_found_and_invalid_parameters_are_distinct(tmp_path, metadata_client):
    path = tmp_path / "run"
    summary = collect(path, metadata_client([error(code=7), error(code=6)]))
    assert summary.not_found == 1 and summary.failed == 1
    assert rows(path)[0]["error"] is None
    assert rows(path)[1]["error"] is not None


def test_lookup_requires_mbid_before_creating_directory(tmp_path, metadata_client):
    path = tmp_path / "run"
    with pytest.raises(ConfigError, match="accepted artist MBID"):
        collect_artist_metadata(
            out=path, artists=[ArtistInput("A")], operations=["musicbrainz_lookup"]
        )
    assert not path.exists()


def test_collection_timestamps_are_utc_and_ids_change_with_response_parameters(
    tmp_path, metadata_client
):
    first, second = tmp_path / "first", tmp_path / "second"
    collect(first, metadata_client())
    collect(second, metadata_client(), similar_limit=9)
    left, right = rows(first), rows(second)
    assert left[0]["request_id"] == right[0]["request_id"]
    assert left[1]["request_id"] != right[1]["request_id"]
    assert all(
        row["started_at"].endswith("+00:00")
        and row["collected_at"] >= row["started_at"]
        for row in left
    )


@pytest.mark.parametrize(
    "name, mbid", [("", None), ("  ", None), (None, None), ("A", "bad-id"), ("A", 7)]
)
def test_artist_input_validation(name, mbid, metadata_client):
    with pytest.raises(ValueError):
        ArtistInput(name, mbid)


def test_missing_client_does_not_create_snapshot(tmp_path, metadata_client):
    with pytest.raises(ConfigError, match="lastfm client"):
        collect_artist_metadata(out=tmp_path / "run", artists=[ArtistInput("A")])
    assert not (tmp_path / "run").exists()


def test_explicit_lastfm_missing_artist_message_is_not_invalid_parameter_failure(
    tmp_path, metadata_client
):
    absent = error(code=6)
    absent.payload = {
        "error": 6,
        "message": "The artist you supplied could not be found",
    }
    summary = collect(tmp_path / "run", metadata_client([absent, "empty"]))
    assert summary.not_found == 1
    assert summary.failed == 0
