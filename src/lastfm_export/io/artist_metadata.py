import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from lastfm_export.errors import CollectionError
from lastfm_export.models import ArtistMetadataResult

SCHEMA_VERSION = 1
OUTCOMES = {"success", "empty", "not_found", "failed"}


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def request_id(request: dict[str, Any]) -> str:
    return hashlib.sha256(json_bytes(request)).hexdigest()


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json_bytes(manifest) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((path / "manifest.json").read_bytes())
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != SCHEMA_VERSION
        ):
            raise ValueError("unsupported manifest schema")
        if manifest["state"] not in {"collecting", "completed"}:
            raise ValueError("invalid collection state")
        plan = manifest["plan"]
        if manifest["plan_digest"] != request_id(plan):
            raise ValueError("request plan digest differs")
        if (
            not isinstance(manifest["collection_id"], str)
            or not manifest["collection_id"]
        ):
            raise ValueError("missing collection identity")
        requests = plan["requests"]
        if not requests or len({item["request_id"] for item in requests}) != len(
            requests
        ):
            raise ValueError("empty or duplicate request plan")
        for item in requests:
            if item["request_id"] != request_id(item["request"]):
                raise ValueError("request identity differs")
        if len(plan["inputs"]) != len(plan["input_requests"]):
            raise ValueError("invalid input mapping")
        known = {item["request_id"]: item["request"] for item in requests}
        mapped = set()
        for artist, ids in zip(plan["inputs"], plan["input_requests"]):
            for identity in ids:
                if known[identity]["input_artist"] != artist:
                    raise ValueError("input mapping differs from request")
                mapped.add(identity)
        if mapped != set(known):
            raise ValueError("unmapped request")
        return manifest
    except (ValueError, KeyError, TypeError) as exc:
        raise CollectionError(f"Invalid metadata manifest: {exc}") from None


def _load_results(
    path: Path, manifest: dict[str, Any], *, recover_tail: bool = False
) -> dict[str, ArtistMetadataResult]:
    planned = {
        item["request_id"]: item["request"] for item in manifest["plan"]["requests"]
    }
    latest: dict[str, ArtistMetadataResult] = {}
    results_path = path / "results.ndjson"
    digest = hashlib.sha256()
    offset = 0
    if not results_path.exists():
        if manifest["state"] == "completed":
            raise CollectionError("Completed collection is missing its result log")
        return latest
    with results_path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.endswith(b"\n"):
                if not recover_tail or manifest["state"] == "completed":
                    raise CollectionError(
                        "Incomplete result tail; resume with recover_tail=True / --recover-tail to preserve and remove it"
                    )
                tail_path = path / f"results.tail-{uuid4().hex}.bin"
                with tail_path.open("xb") as tail:
                    tail.write(line)
                    tail.flush()
                    os.fsync(tail.fileno())
                _sync_directory(path)
                with results_path.open("r+b") as log:
                    log.truncate(offset)
                    log.flush()
                    os.fsync(log.fileno())
                break
            try:
                result = ArtistMetadataResult(**json.loads(line))
                request = planned[result.request_id]
                if (
                    result.schema_version != SCHEMA_VERSION
                    or result.collection_id != manifest["collection_id"]
                ):
                    raise ValueError("result belongs to a different collection/schema")
                if result.status not in OUTCOMES:
                    raise ValueError("invalid outcome")
                if any(
                    getattr(result, field) != request[field]
                    for field in (
                        "input_artist",
                        "provider",
                        "endpoint",
                        "base_url",
                        "request_params",
                    )
                ):
                    raise ValueError("result differs from planned request")
                previous = latest.get(result.request_id)
                if result.attempt != (previous.attempt + 1 if previous else 1):
                    raise ValueError("invalid attempt sequence")
                if previous and previous.status != "failed":
                    raise ValueError("completed request has another attempt")
                if (result.status == "failed") != isinstance(result.error, dict):
                    raise ValueError("inconsistent failure outcome")
                if (
                    not isinstance(result.wire_attempts, int)
                    or result.wire_attempts < 1
                ):
                    raise ValueError("invalid wire attempt count")
            except (ValueError, TypeError, KeyError) as exc:
                raise CollectionError(
                    f"Corrupt result log at line {line_number}: {exc}"
                ) from None
            latest[result.request_id] = result
            digest.update(line)
            offset += len(line)
    if manifest["state"] == "completed":
        counts = dict(Counter(result.status for result in latest.values()))
        if (
            len(latest) != len(planned)
            or manifest.get("counts") != counts
            or manifest.get("results_sha256") != digest.hexdigest()
        ):
            raise CollectionError("Completed collection counts or result digest differ")
    return latest


def read_collection(
    path: Path,
) -> tuple[dict[str, Any], dict[str, ArtistMetadataResult]]:
    """Validate and inspect a collection without changing it."""
    path = Path(path)
    manifest = load_manifest(path)
    return manifest, _load_results(path, manifest)


class CollectionStore:
    """Single-writer collection with a durable result log as resume truth."""

    def __init__(
        self,
        path: Path,
        *,
        mode: str,
        plan: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        recover_tail: bool = False,
    ) -> None:
        self.path = Path(path)
        self.mode = mode
        self.plan = plan
        self.metadata = metadata
        self.recover_tail = recover_tail
        self.manifest: dict[str, Any] = {}
        self.latest: dict[str, ArtistMetadataResult] = {}
        self._locked = False

    def __enter__(self) -> "CollectionStore":
        if self.mode == "new":
            if self.plan is None or self.metadata is None:
                raise CollectionError("New collection requires a plan and metadata")
            try:
                self.path.mkdir(parents=True, exist_ok=False)
                _sync_directory(self.path.parent)
            except FileExistsError:
                raise CollectionError(
                    "Destination exists; choose resume, reuse or a new directory"
                ) from None
        try:
            try:
                with (self.path / ".writer.lock").open("x", encoding="utf-8") as guard:
                    self._locked = True
                    guard.write(f"pid={os.getpid()}\n")
                    guard.flush()
                    os.fsync(guard.fileno())
            except FileExistsError:
                raise CollectionError(
                    "Collection has a writer guard. If its process has stopped, remove .writer.lock before resuming"
                ) from None
            if self.mode == "new":
                self.manifest = {
                    **self.metadata,
                    "schema_version": SCHEMA_VERSION,
                    "state": "collecting",
                    "plan": self.plan,
                    "plan_digest": request_id(self.plan),
                }
                _write_manifest(self.path / "manifest.json", self.manifest)
            else:
                self.manifest = load_manifest(self.path)
                if self.manifest["state"] == "completed":
                    raise CollectionError(
                        "Completed collections are read-only; use reuse or a new retry/refresh directory"
                    )
                if self.plan != self.manifest["plan"]:
                    raise CollectionError(
                        "Inputs or settings differ from the collection plan"
                    )
            self.latest = _load_results(
                self.path, self.manifest, recover_tail=self.recover_tail
            )
            return self
        except BaseException:
            self._release()
            raise

    def append(self, result: ArtistMetadataResult) -> None:
        with (self.path / "results.ndjson").open("ab") as stream:
            stream.write(json_bytes(result.to_record()) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(self.path)
        self.latest[result.request_id] = result

    def finalize(self, completed_at: str) -> None:
        if len(self.latest) != len(self.manifest["plan"]["requests"]):
            raise CollectionError("Cannot finalize with unattempted requests")
        digest = hashlib.sha256()
        with (self.path / "results.ndjson").open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        self.manifest.update(
            state="completed",
            completed_at=completed_at,
            counts=dict(Counter(result.status for result in self.latest.values())),
            results_sha256=digest.hexdigest(),
        )
        _write_manifest(self.path / "manifest.json", self.manifest)

    def _release(self) -> None:
        if self._locked:
            (self.path / ".writer.lock").unlink()
            self._locked = False

    def __exit__(self, *args) -> None:
        self._release()
