import json

import pytest

from lastfm_export.errors import CollectionError
from lastfm_export.io.artist_metadata import CollectionStore, read_collection
from lastfm_export.models import ArtistInput
from lastfm_export.pipelines.artist_metadata import collect_artist_metadata


def interrupted(path, metadata_client):
    with pytest.raises(KeyboardInterrupt):
        collect_artist_metadata(
            out=path,
            artists=[ArtistInput("A")],
            lastfm=metadata_client(["success", KeyboardInterrupt()]),
        )


def test_incomplete_tail_requires_explicit_recovery_and_preserves_bytes(
    tmp_path, metadata_client
):
    path = tmp_path / "run"
    interrupted(path, metadata_client)
    with (path / "results.ndjson").open("ab") as stream:
        stream.write(b'{"incomplete": "\xe2\x82')
    with pytest.raises(CollectionError, match="Incomplete result tail"):
        collect_artist_metadata(out=path, mode="resume", lastfm=metadata_client())
    client = metadata_client()
    summary = collect_artist_metadata(
        out=path, mode="resume", lastfm=client, recover_tail=True
    )
    assert summary.skipped == summary.attempted == 1
    assert (
        list(path.glob("results.tail-*.bin"))[0].read_bytes()
        == b'{"incomplete": "\xe2\x82'
    )
    assert len(read_collection(path)[1]) == 2


def test_corruption_in_middle_is_never_skipped_or_truncated(tmp_path, metadata_client):
    path = tmp_path / "run"
    interrupted(path, metadata_client)
    log = path / "results.ndjson"
    log.write_bytes(b"broken\n" + log.read_bytes())
    before = log.read_bytes()
    with pytest.raises(CollectionError, match="line 1"):
        collect_artist_metadata(
            out=path, mode="resume", lastfm=metadata_client(), recover_tail=True
        )
    assert log.read_bytes() == before
    assert not list(path.glob("results.tail-*"))


def test_concurrent_writer_guard_is_not_removed_by_rejected_writer(
    tmp_path, metadata_client
):
    path = tmp_path / "run"
    interrupted(path, metadata_client)
    manifest, _ = read_collection(path)
    with CollectionStore(path, mode="resume", plan=manifest["plan"]):
        with pytest.raises(CollectionError, match="writer guard"):
            collect_artist_metadata(out=path, mode="resume", lastfm=metadata_client())
        assert (path / ".writer.lock").exists()
    assert not (path / ".writer.lock").exists()


def test_completed_snapshot_digest_detects_payload_changes(tmp_path, metadata_client):
    path = tmp_path / "run"
    collect_artist_metadata(
        out=path, artists=[ArtistInput("A")], lastfm=metadata_client()
    )
    log = path / "results.ndjson"
    data = log.read_text().replace('"source":"tags"', '"source":"edited"')
    log.write_text(data)
    with pytest.raises(CollectionError, match="digest differ"):
        read_collection(path)


def test_failed_durable_append_does_not_advance_manifest_or_fetch_more(
    tmp_path, monkeypatch, metadata_client
):
    path = tmp_path / "run"
    client = metadata_client()

    def fail_append(self, result):
        raise OSError("disk full")

    monkeypatch.setattr(CollectionStore, "append", fail_append)
    with pytest.raises(OSError, match="disk full"):
        collect_artist_metadata(out=path, artists=[ArtistInput("A")], lastfm=client)
    assert len(client.calls) == 1
    manifest, latest = read_collection(path)
    assert manifest["state"] == "collecting" and not latest
    assert not (path / ".writer.lock").exists()


def test_schema_or_plan_mismatch_is_rejected(tmp_path, metadata_client):
    path = tmp_path / "run"
    interrupted(path, metadata_client)
    manifest_path = path / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["plan"]["settings"]["similar_limit"] = 99
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(CollectionError, match="digest differs"):
        read_collection(path)


def test_wrong_request_or_attempt_is_rejected_before_resume(tmp_path, metadata_client):
    path = tmp_path / "run"
    interrupted(path, metadata_client)
    log = path / "results.ndjson"
    record = json.loads(log.read_text())
    record["attempt"] = 2
    log.write_text(json.dumps(record) + "\n")
    with pytest.raises(CollectionError, match="attempt sequence"):
        read_collection(path)


def test_failure_to_publish_final_manifest_resumes_without_refetch(
    tmp_path, monkeypatch, metadata_client
):
    from lastfm_export.io import artist_metadata

    path = tmp_path / "run"
    original = artist_metadata._write_manifest

    def fail_finalize(destination, manifest):
        if manifest["state"] == "completed":
            raise OSError("manifest publication failed")
        original(destination, manifest)

    monkeypatch.setattr(artist_metadata, "_write_manifest", fail_finalize)
    with pytest.raises(OSError, match="publication failed"):
        collect_artist_metadata(
            out=path, artists=[ArtistInput("A")], lastfm=metadata_client()
        )
    assert len(read_collection(path)[1]) == 2
    monkeypatch.setattr(artist_metadata, "_write_manifest", original)
    client = metadata_client()
    summary = collect_artist_metadata(out=path, mode="resume", lastfm=client)
    assert not client.calls
    assert summary.completed and summary.skipped == 2


def test_interruption_before_result_persistence_repeats_only_unrecorded_request(
    tmp_path, monkeypatch, metadata_client
):
    path = tmp_path / "run"
    original = CollectionStore.append

    def interrupt_append(self, result):
        raise KeyboardInterrupt

    monkeypatch.setattr(CollectionStore, "append", interrupt_append)
    first = metadata_client()
    with pytest.raises(KeyboardInterrupt):
        collect_artist_metadata(out=path, artists=[ArtistInput("A")], lastfm=first)
    assert len(first.calls) == 1
    assert not read_collection(path)[1]
    monkeypatch.setattr(CollectionStore, "append", original)
    second = metadata_client()
    summary = collect_artist_metadata(out=path, mode="resume", lastfm=second)
    assert len(second.calls) == 2
    assert summary.completed
