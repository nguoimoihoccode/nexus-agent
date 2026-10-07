"""Filesystem persistence adapter for datasets, records, and artifacts."""

import hashlib
import json
import os
import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from worker.adapters.artifact_store import (
    ArtifactObjectStore,
    FilesystemArtifactObjectStore,
)
from worker.domain import (
    CANONICAL_SCHEMA_VERSION,
    DatasetInspection,
    WorkerError,
    content_hash,
    revision_id,
)
from worker.models import (
    ArtifactManifest,
    DatasetInfo,
    DatasetValidation,
    ExperimentRecord,
    JobStatus,
    WorkerError as WorkerErrorModel,
    utc_now,
)
from worker.security import actor_context, actor_directory
from worker.security import current_actor_key


class FilesystemExperimentRepository:
    def __init__(
        self,
        data_dir: Path,
        artifacts_dir: Path,
        *,
        artifact_retention_seconds: int = 90 * 24 * 60 * 60,
        artifact_store: ArtifactObjectStore | None = None,
    ) -> None:
        if artifact_retention_seconds < 1:
            raise ValueError("artifact_retention_seconds must be positive")
        self.data_dir = data_dir
        self.artifacts_dir = artifacts_dir
        self.artifact_retention_seconds = artifact_retention_seconds
        self.artifact_store = artifact_store or FilesystemArtifactObjectStore()
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def readiness(self) -> bool:
        local_ready = all(
            path.is_dir() and os.access(path, os.R_OK | os.W_OK)
            for path in (self.data_dir, self.artifacts_dir)
        )
        return local_ready and self.artifact_store.readiness()

    def recover_interrupted(self) -> int:
        recovered = 0
        roots = [self.artifacts_dir]
        roots.extend(path for path in self.artifacts_dir.glob("v1-*") if path.is_dir())
        for root in roots:
            actor = root.name if root != self.artifacts_dir else "development"
            with actor_context(actor):
                for record_path in (root / "experiments").glob("*/record.json"):
                    try:
                        record = ExperimentRecord.model_validate_json(
                            record_path.read_text(encoding="utf-8")
                        )
                    except (ValueError, OSError, json.JSONDecodeError):
                        continue
                    if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                        self.update_record(
                            record.experiment_id,
                            status=JobStatus.FAILED,
                            error=WorkerErrorModel(
                                code="worker_interrupted",
                                message=(
                                    "The worker restarted before the experiment completed."
                                ),
                            ),
                        )
                        recovered += 1
        return recovered

    def close(self) -> None:
        return None

    def list_datasets(self) -> list[DatasetInfo]:
        datasets: list[DatasetInfo] = []
        data_dir = actor_directory(self.data_dir)
        if not data_dir.is_dir():
            return datasets
        for candidate in sorted(data_dir.iterdir()):
            if candidate.name in {"calendars", "features", "instruments"}:
                continue
            if not candidate.is_dir() or not self.is_dataset_id(candidate.name):
                continue
            limitations = self.dataset_limitations(candidate.name)
            for universe in self.universes(candidate):
                datasets.append(
                    self.dataset_info(
                        candidate.name,
                        universe,
                        self.inspect_dataset(candidate.name, universe),
                    ).model_copy(
                        update={
                            "limitations": limitations,
                            "production_eligibility": limitations.get(
                                "production_eligibility", "blocked"
                            ),
                        }
                    )
                )
        return datasets

    def validate_dataset(self, dataset_id: str, universe: str) -> DatasetValidation:
        self.require_dataset_id(dataset_id)
        self.require_universe(universe)
        inspection = self.inspect_dataset(dataset_id, universe)
        limitations = self.dataset_limitations(dataset_id)
        return DatasetValidation(
            dataset_id=dataset_id,
            dataset_revision_id=(dataset_id if dataset_id.startswith("dsr_v1_") else None),
            universe=universe,
            valid=inspection.ready,
            errors=list(inspection.errors),
            start_date=inspection.start_date,
            end_date=inspection.end_date,
            max_test_end=inspection.max_test_end,
            trading_days=inspection.trading_days,
            manifest_hash=inspection.manifest_hash,
            manifest_verified=inspection.manifest_verified,
            limitations=limitations,
            production_eligibility=limitations.get(
                "production_eligibility", "blocked"
            ),
        )

    def dataset_limitations(self, dataset_id: str) -> dict[str, Any]:
        path = self.dataset_dir(dataset_id) / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"production_eligibility": "blocked"}
        limitations = manifest.get("limitations")
        return (
            dict(limitations)
            if isinstance(limitations, dict)
            else {"production_eligibility": "blocked"}
        )

    def inspect_dataset(self, dataset_id: str, universe: str) -> DatasetInspection:
        errors: list[str] = []
        dataset_dir = self.dataset_dir(dataset_id)
        if not dataset_dir.is_dir():
            raise WorkerError("unknown_dataset", f"Unknown dataset: {dataset_id}")
        for name in ("calendars", "instruments", "features"):
            if not (dataset_dir / name).is_dir():
                errors.append(f"Missing Qlib dataset directory: {name}")

        calendar = self.read_calendar(dataset_dir, errors)
        instrument_path = dataset_dir / "instruments" / f"{universe.lower()}.txt"
        if (dataset_dir / "instruments").is_dir() and not instrument_path.is_file():
            errors.append(f"Missing universe: instruments/{universe.lower()}.txt")
        for instrument in self.instrument_symbols(instrument_path):
            feature_dir = dataset_dir / "features" / instrument.lower()
            if not feature_dir.is_dir():
                errors.append(f"Missing feature directory: features/{instrument.lower()}")
                continue
            for field in ("close", "factor"):
                if not (feature_dir / f"{field}.day.bin").is_file():
                    errors.append(
                        f"Missing feature file: features/{instrument.lower()}/{field}.day.bin"
                    )
        manifest_errors, manifest_hash, manifest_verified = self.inspect_manifest(
            dataset_dir,
            dataset_id,
        )
        errors.extend(manifest_errors)
        return DatasetInspection(
            errors=tuple(errors),
            calendar=calendar,
            manifest_hash=manifest_hash,
            manifest_verified=manifest_verified,
        )

    def inspect_manifest(
        self,
        dataset_dir: Path,
        dataset_id: str,
    ) -> tuple[list[str], str | None, bool]:
        manifest_path = dataset_dir / "manifest.json"
        if not manifest_path.is_file():
            if dataset_id.startswith("dsr_v1_"):
                return ["Missing immutable dataset manifest: manifest.json"], None, False
            return [], None, False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ["Dataset manifest is unreadable"], None, False
        required = {
            "schema_version",
            "source_staging_revision_id",
            "universe_name",
            "include_fields",
            "files",
            "dataset_revision_id",
            "manifest_hash",
            "status",
        }
        missing = sorted(required - set(manifest))
        if missing:
            return [f"Dataset manifest is missing fields: {', '.join(missing)}"], None, False
        payload = {
            "schema_version": manifest["schema_version"],
            "source_staging_revision_id": manifest["source_staging_revision_id"],
            "universe_name": manifest["universe_name"],
            "include_fields": manifest["include_fields"],
            "files": manifest["files"],
        }
        if "limitations" in manifest:
            payload["limitations"] = manifest["limitations"]
        actual_hash = content_hash("qlib_dataset_manifest", payload)
        errors: list[str] = []
        if manifest["schema_version"] != CANONICAL_SCHEMA_VERSION:
            errors.append("Dataset manifest schema version is unsupported")
        if manifest["status"] != "ready":
            errors.append("Dataset manifest status is not ready")
        if manifest["manifest_hash"] != actual_hash:
            errors.append("Dataset manifest hash does not match its canonical payload")
        if manifest["dataset_revision_id"] != dataset_id:
            errors.append("Dataset manifest revision ID does not match the directory")
        if revision_id("dsr", actual_hash) != dataset_id:
            errors.append("Dataset revision ID does not match the manifest hash")
        if manifest["files"] != self._manifest_files(dataset_dir):
            errors.append("Dataset files do not match the immutable manifest")
        return errors, str(manifest.get("manifest_hash") or actual_hash), not errors

    @staticmethod
    def _manifest_files(root: Path) -> list[dict[str, object]]:
        files: list[dict[str, object]] = []
        for path in sorted(
            candidate
            for candidate in root.rglob("*")
            if candidate.is_file() and candidate.name != "manifest.json"
        ):
            data = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "media_type": (
                        "text/plain; charset=utf-8"
                        if path.suffix.lower() == ".txt"
                        else "application/octet-stream"
                    ),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        return files

    def read_calendar(self, dataset_dir: Path, errors: list[str]) -> tuple[date, ...]:
        calendar_path = dataset_dir / "calendars" / "day.txt"
        if not calendar_path.is_file():
            errors.append("Missing Qlib daily calendar: calendars/day.txt")
            return ()
        try:
            parsed = tuple(
                date.fromisoformat(line.strip())
                for line in calendar_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except (OSError, ValueError):
            errors.append("Qlib daily calendar is unreadable or contains invalid dates")
            return ()
        if not parsed:
            errors.append("Qlib daily calendar is empty")
            return ()
        if any(left >= right for left, right in zip(parsed, parsed[1:])):
            errors.append("Qlib daily calendar must be strictly increasing")
            return ()
        return parsed

    def load_record(self, experiment_id: str) -> ExperimentRecord | None:
        path = self.record_path(experiment_id)
        if not path.is_file():
            return None
        return ExperimentRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save_record(self, record: ExperimentRecord) -> None:
        path = self.record_path(record.experiment_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def request_cancellation(
        self,
        experiment_id: str,
        *,
        reason_category: str,
    ) -> ExperimentRecord:
        record = self.load_record(experiment_id)
        if record is None:
            raise WorkerError("unknown_experiment", "Unknown experiment.")
        if record.status in {
            JobStatus.CANCELLED,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.EXPIRED,
        }:
            return record
        next_status = (
            JobStatus.CANCELLED
            if record.status == JobStatus.QUEUED
            else JobStatus.CANCELLING
        )
        return self.update_record(
            experiment_id,
            status=next_status,
            cancellation={
                "reason_category": reason_category,
                "requested_at": utc_now().isoformat(),
                "requested_by_actor": current_actor_key(),
            },
        )

    def update_record(self, experiment_id: str, **changes: Any) -> ExperimentRecord:
        record = self.load_record(experiment_id)
        if record is None:
            raise KeyError(experiment_id)
        if (
            changes.get("status") == JobStatus.COMPLETED
            and isinstance(changes.get("result"), dict)
        ):
            limitations = self.dataset_limitations(record.request.dataset_id)
            changes["result"] = {
                **changes["result"],
                "limitations": limitations,
                "production_eligibility": limitations.get(
                    "production_eligibility", "blocked"
                ),
            }
            changes["result"], _manifests = self.prepare_completed_artifacts(
                experiment_id,
                changes["result"],
            )
        updated_at = utc_now()
        if changes.get("status") == JobStatus.CANCELLED:
            changes["cancellation"] = {
                **(changes.get("cancellation") or record.cancellation or {}),
                "completed_at": updated_at.isoformat(),
                "resulting_state": JobStatus.CANCELLED.value,
            }
        updated = record.model_copy(update={**changes, "updated_at": updated_at})
        self.save_record(updated)
        return updated

    def discard_partial_artifacts(self, experiment_id: str) -> None:
        shutil.rmtree(self.experiment_dir(experiment_id) / "artifacts", ignore_errors=True)

    def prepare_completed_artifacts(
        self,
        experiment_id: str,
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Commit allowlisted files into immutable content-addressed objects."""
        staging = self.experiment_dir(experiment_id) / "artifacts"
        definitions = (
            (
                "predictions",
                "predictions.parquet",
                "application/vnd.apache.parquet",
                True,
            ),
            ("report", "report.parquet", "application/vnd.apache.parquet", True),
            (
                "positions",
                "positions.pkl",
                "application/x-python-pickle",
                False,
            ),
        )
        manifests: list[dict[str, Any]] = []
        actor_root = actor_directory(self.artifacts_dir)
        for artifact_type, filename, media_type, download_eligible in definitions:
            source = staging / filename
            if not source.is_file() or source.is_symlink():
                continue
            digest = f"sha256:{hashlib.sha256(source.read_bytes()).hexdigest()}"
            artifact_id = revision_id("art", digest)
            storage_key, size_bytes = self.artifact_store.publish(
                source,
                actor_key=current_actor_key(),
                actor_root=actor_root,
                artifact_id=artifact_id,
                filename=filename,
                media_type=media_type,
                content_hash=digest,
            )
            created_at = utc_now()
            manifest = ArtifactManifest(
                artifact_id=artifact_id,
                producer_id=experiment_id,
                artifact_type=artifact_type,
                media_type=media_type,
                size_bytes=size_bytes,
                content_hash=digest,
                download_eligible=download_eligible,
                created_at=created_at,
                expires_at=created_at
                + timedelta(seconds=self.artifact_retention_seconds),
            ).model_dump(mode="json")
            manifest["storage_key"] = storage_key
            manifests.append(manifest)
        public = [_public_artifact(item) for item in manifests]
        committed = {**result, "artifacts": public}
        manifest_path = self.experiment_dir(experiment_id) / "artifact-manifest.json"
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": "1", "artifacts": manifests},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, manifest_path)
        shutil.rmtree(staging, ignore_errors=True)
        return committed, manifests

    def artifact_manifest(self, artifact_id: str) -> dict[str, Any]:
        self._validate_artifact_id(artifact_id)
        root = actor_directory(self.artifacts_dir)
        for path in (root / "experiments").glob("*/artifact-manifest.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for manifest in payload.get("artifacts", []):
                if manifest.get("artifact_id") == artifact_id:
                    return _public_artifact(manifest)
        raise WorkerError("unknown_artifact", "Unknown artifact.", stage="artifact_read")

    def artifact_path(self, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        self._validate_artifact_id(artifact_id)
        root = actor_directory(self.artifacts_dir).resolve()
        for path in (root / "experiments").glob("*/artifact-manifest.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for manifest in payload.get("artifacts", []):
                if manifest.get("artifact_id") != artifact_id:
                    continue
                if not manifest.get("download_eligible"):
                    raise WorkerError(
                        "artifact_not_downloadable",
                        "Artifact is internal-only.",
                        stage="artifact_read",
                    )
                storage_key = str(manifest.get("storage_key") or "")
                filename = storage_key.rsplit("/", 1)[-1]
                candidate = self.artifact_store.materialize(
                    storage_key,
                    actor_key=current_actor_key(),
                    actor_root=root,
                    artifact_id=artifact_id,
                    filename=filename,
                    content_hash=str(manifest.get("content_hash") or ""),
                )
                if not candidate.is_file():
                    raise WorkerError(
                        "artifact_missing",
                        "Artifact bytes are unavailable.",
                        stage="artifact_read",
                    )
                actual = f"sha256:{hashlib.sha256(candidate.read_bytes()).hexdigest()}"
                if actual != manifest.get("content_hash"):
                    raise WorkerError(
                        "artifact_corrupted",
                        "Artifact content hash verification failed.",
                        stage="artifact_read",
                    )
                return candidate, _public_artifact(manifest)
        raise WorkerError("unknown_artifact", "Unknown artifact.", stage="artifact_read")

    def delete_artifact_bytes(self, storage_key: str) -> None:
        """Delete only an actor-scoped object selected by a committed tombstone."""
        root = actor_directory(self.artifacts_dir)
        self.artifact_store.delete(
            storage_key,
            actor_key=current_actor_key(),
            actor_root=root,
        )

    def experiment_dir(self, experiment_id: str) -> Path:
        if not re.fullmatch(r"(?:exp_|exp_v1_)[0-9a-f]{32}", experiment_id):
            raise ValueError("Invalid experiment ID")
        return actor_directory(self.artifacts_dir) / "experiments" / experiment_id

    def artifact_dir(self, experiment_id: str) -> Path:
        path = self.experiment_dir(experiment_id) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def record_path(self, experiment_id: str) -> Path:
        return self.experiment_dir(experiment_id) / "record.json"

    @staticmethod
    def _validate_artifact_id(artifact_id: str) -> None:
        if not re.fullmatch(r"art_v1_[0-9a-f]{24}", artifact_id):
            raise WorkerError("invalid_artifact_id", "Invalid artifact ID.")

    def require_dataset_id(self, dataset_id: str) -> None:
        if not self.is_dataset_id(dataset_id):
            raise WorkerError("unknown_dataset", f"Unknown dataset: {dataset_id}")

    @staticmethod
    def require_universe(universe: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", universe):
            raise WorkerError("unknown_dataset", f"Unknown universe: {universe}")

    @staticmethod
    def is_dataset_id(dataset_id: str) -> bool:
        return bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,80}", dataset_id))

    def dataset_dir(self, dataset_id: str) -> Path:
        self.require_dataset_id(dataset_id)
        return actor_directory(self.data_dir) / dataset_id

    @staticmethod
    def dataset_info(
        dataset_id: str,
        universe: str,
        inspection: DatasetInspection,
    ) -> DatasetInfo:
        return DatasetInfo(
            dataset_id=dataset_id,
            dataset_revision_id=(dataset_id if dataset_id.startswith("dsr_v1_") else None),
            universe=universe,
            ready=inspection.ready,
            start_date=inspection.start_date,
            end_date=inspection.end_date,
            max_test_end=inspection.max_test_end,
            trading_days=inspection.trading_days,
            manifest_hash=inspection.manifest_hash,
            manifest_verified=inspection.manifest_verified,
        )

    @staticmethod
    def universes(dataset_dir: Path) -> list[str]:
        instruments_dir = dataset_dir / "instruments"
        if not instruments_dir.is_dir():
            return []
        return sorted(path.stem for path in instruments_dir.glob("*.txt"))

    @staticmethod
    def instrument_symbols(instrument_path: Path) -> list[str]:
        if not instrument_path.is_file():
            return []
        try:
            lines = instrument_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        symbols: list[str] = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0]:
                symbols.append(parts[0])
        return symbols


def _public_artifact(manifest: dict[str, Any]) -> dict[str, Any]:
    return ArtifactManifest.model_validate(manifest).model_dump(mode="json")
