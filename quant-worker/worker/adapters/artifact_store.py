"""Artifact-byte storage adapters behind one immutable object-store port."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

from worker.domain import WorkerError


class ArtifactObjectStore(Protocol):
    def readiness(self) -> bool: ...

    def publish(
        self,
        source: Path,
        *,
        actor_key: str,
        actor_root: Path,
        artifact_id: str,
        filename: str,
        media_type: str,
        content_hash: str,
    ) -> tuple[str, int]: ...

    def materialize(
        self,
        storage_key: str,
        *,
        actor_key: str,
        actor_root: Path,
        artifact_id: str,
        filename: str,
        content_hash: str,
    ) -> Path: ...

    def delete(self, storage_key: str, *, actor_key: str, actor_root: Path) -> None: ...


class FilesystemArtifactObjectStore:
    """Immutable actor-scoped objects on a local or mounted filesystem."""

    def readiness(self) -> bool:
        return True

    def publish(
        self,
        source: Path,
        *,
        actor_key: str,
        actor_root: Path,
        artifact_id: str,
        filename: str,
        media_type: str,
        content_hash: str,
    ) -> tuple[str, int]:
        del actor_key, media_type
        object_dir = actor_root / "objects" / artifact_id
        object_dir.mkdir(parents=True, exist_ok=True)
        destination = object_dir / filename
        if destination.exists():
            if _digest(destination) != content_hash:
                raise WorkerError(
                    "artifact_hash_conflict",
                    "Artifact storage contains conflicting immutable bytes.",
                    stage="artifact_publish",
                )
            source.unlink()
        else:
            os.replace(source, destination)
        return destination.relative_to(actor_root).as_posix(), destination.stat().st_size

    def materialize(
        self,
        storage_key: str,
        *,
        actor_key: str,
        actor_root: Path,
        artifact_id: str,
        filename: str,
        content_hash: str,
    ) -> Path:
        del actor_key, artifact_id, filename, content_hash
        return _scoped_path(actor_root, storage_key)

    def delete(self, storage_key: str, *, actor_key: str, actor_root: Path) -> None:
        del actor_key
        candidate = _scoped_path(actor_root, storage_key)
        if candidate.is_symlink():
            candidate.unlink(missing_ok=True)
        elif candidate.is_file():
            candidate.unlink()
        try:
            candidate.parent.rmdir()
        except OSError:
            pass


def _scoped_path(actor_root: Path, storage_key: str) -> Path:
    root = actor_root.resolve()
    candidate = (root / storage_key).resolve()
    if root not in candidate.parents:
        raise WorkerError(
            "artifact_path_invalid",
            "Artifact storage key escaped the actor boundary.",
            stage="artifact_read",
        )
    return candidate


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
