"""Filesystem persistence and Qlib provider-layout adapter."""

import hashlib
import json
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from worker.domain import (
    CANONICAL_SCHEMA_VERSION,
    DatasetBuildIdentity,
    FACTOR_FIELDS,
    STAGING_SCHEMA,
    RevisionIdentity,
    WorkerError,
    canonicalize,
    canonical_json_bytes,
    content_hash,
    factor_snapshot_limitations,
    market_data_limitations,
    request_fingerprint,
    revision_id,
    utc_now,
)
from worker.models import BuildQlibDatasetRequest, FactorSnapshotRequest, FetchOhlcvRequest
from worker.security import actor_directory


class MarketDataStore:
    """Filesystem storage for staged market data and Qlib provider datasets."""

    def __init__(self, staging_dir: Path, data_dir: Path) -> None:
        self.staging_dir = staging_dir
        self.data_dir = data_dir

    def save_staging(
        self,
        request: FetchOhlcvRequest,
        frame: pd.DataFrame,
        warnings: list[str],
    ) -> RevisionIdentity:
        records = self._canonical_frame_records(frame, STAGING_SCHEMA)
        request_payload = {
            "symbols": sorted(request.symbols),
            "start_date": request.start_date,
            "end_date": request.end_date,
            "provider": request.provider,
            "frequency": request.frequency,
            "adjustment": request.adjustment,
        }
        limitations = market_data_limitations(
            provider=request.provider,
            requested_adjustment=request.adjustment,
            effective_adjustment=self.effective_adjustment(request.adjustment),
        )
        fingerprint = request_fingerprint("openbb_ohlcv", request_payload)
        digest = content_hash(
            "openbb_ohlcv",
            {"schema": STAGING_SCHEMA, "records": records},
        )
        staging_id = revision_id("stg", digest)
        retrieved_at = utc_now()
        target = self.staging_path(staging_id)
        metadata = {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "staging_revision_id": staging_id,
            "dataset_staging_id": staging_id,
            "request_fingerprint": fingerprint,
            "content_hash": digest,
            "retrieved_at": retrieved_at,
            "source": {
                "vendor": "openbb",
                "provider": request.provider,
                "vendor_version": limitations["provider_evidence"]["vendor_version"],
                "provider_adapter_version": limitations["provider_evidence"][
                    "provider_adapter_version"
                ],
                "frequency": request.frequency,
                "requested_adjustment": request.adjustment,
                "effective_adjustment": self.effective_adjustment(request.adjustment),
            },
            "request_parameters": canonicalize(request_payload),
            "symbols_requested": request.symbols,
            "symbols_loaded": sorted(frame["symbol"].unique().tolist()),
            "row_count": len(records),
            "date_range": {
                "start": min(record["date"] for record in records),
                "end": max(record["date"] for record in records),
            },
            "schema": STAGING_SCHEMA,
            "warnings": warnings,
            "limitations": limitations,
            "created_by": "quant-data-worker",
        }
        self._publish_immutable_revision(target, records, metadata, digest)
        self._record_request_link(
            "openbb_ohlcv",
            fingerprint,
            digest,
            staging_id,
            retrieved_at,
        )
        return RevisionIdentity(
            schema_version=CANONICAL_SCHEMA_VERSION,
            revision_id=staging_id,
            request_fingerprint=fingerprint,
            content_hash=digest,
            retrieved_at=retrieved_at,
        )

    def save_factor_snapshot(
        self,
        request: FactorSnapshotRequest,
        frame: pd.DataFrame,
        warnings: list[str],
    ) -> RevisionIdentity:
        records = self._canonical_frame_records(frame, FACTOR_FIELDS)
        fingerprint = request_fingerprint(
            "factor_snapshot",
            {
                "symbols": sorted(request.symbols),
                "as_of_date": request.as_of_date,
                "provider": request.provider,
            },
        )
        digest = content_hash(
            "factor_snapshot",
            {"schema": FACTOR_FIELDS, "records": records},
        )
        snapshot_id = revision_id("fac", digest)
        retrieved_at = utc_now()
        limitations = factor_snapshot_limitations(provider=request.provider)
        target = self.factor_snapshot_path(snapshot_id)
        metadata = {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "factor_snapshot_revision_id": snapshot_id,
            "factor_snapshot_id": snapshot_id,
            "request_fingerprint": fingerprint,
            "content_hash": digest,
            "retrieved_at": retrieved_at,
            "point_in_time_status": "unsupported",
            "source": {
                "vendor": "openbb",
                "provider": request.provider,
                "vendor_version": limitations["provider_evidence"]["vendor_version"],
                "provider_adapter_version": limitations["provider_evidence"][
                    "provider_adapter_version"
                ],
            },
            "request_parameters": canonicalize(
                {
                    "symbols": sorted(request.symbols),
                    "as_of_date": request.as_of_date,
                    "provider": request.provider,
                }
            ),
            "as_of_date": request.as_of_date.isoformat(),
            "symbols_requested": request.symbols,
            "symbols_loaded": sorted(frame["symbol"].unique().tolist()),
            "row_count": len(records),
            "schema": FACTOR_FIELDS,
            "warnings": warnings,
            "limitations": limitations,
            "created_by": "quant-data-worker",
        }
        self._publish_immutable_revision(target, records, metadata, digest)
        self._record_request_link(
            "factor_snapshot",
            fingerprint,
            digest,
            snapshot_id,
            retrieved_at,
        )
        return RevisionIdentity(
            schema_version=CANONICAL_SCHEMA_VERSION,
            revision_id=snapshot_id,
            request_fingerprint=fingerprint,
            content_hash=digest,
            retrieved_at=retrieved_at,
        )

    def load_staging(self, dataset_staging_id: str) -> pd.DataFrame:
        target = self.staging_path(dataset_staging_id)
        if not target.is_dir():
            raise WorkerError(
                "unknown_staging_dataset",
                f"Unknown staging dataset: {dataset_staging_id}",
                stage="validation",
            )
        try:
            return pd.read_json(target / "data.jsonl", lines=True)
        except (OSError, ValueError) as exc:
            raise WorkerError(
                "invalid_staging_dataset",
                "Staging dataset is unreadable.",
                stage="validation",
            ) from exc

    def read_staging_metadata(self, dataset_staging_id: str) -> dict[str, Any]:
        path = self.staging_path(dataset_staging_id) / "metadata.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerError(
                "invalid_staging_metadata",
                "Staging revision metadata is unavailable.",
                stage="validation",
            ) from exc
        if not isinstance(payload, dict):
            raise WorkerError(
                "invalid_staging_metadata",
                "Staging revision metadata is invalid.",
                stage="validation",
            )
        return payload

    def read_factor_snapshot_metadata(self, factor_snapshot_id: str) -> dict[str, Any]:
        path = self.factor_snapshot_path(factor_snapshot_id) / "metadata.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerError(
                "invalid_factor_snapshot_metadata",
                "Factor snapshot metadata is unavailable.",
                stage="validation",
            ) from exc
        if not isinstance(payload, dict):
            raise WorkerError(
                "invalid_factor_snapshot_metadata",
                "Factor snapshot metadata is invalid.",
                stage="validation",
            )
        return payload

    def build_dataset(
        self,
        request: BuildQlibDatasetRequest,
        frame: pd.DataFrame,
    ) -> DatasetBuildIdentity:
        dates = sorted(pd.to_datetime(frame["date"]).dt.date.unique().tolist())
        symbols = sorted(frame["symbol"].unique().tolist())
        actor_root = actor_directory(self.data_dir)
        actor_root.mkdir(parents=True, exist_ok=True)
        temporary = actor_root / f".tmp_dataset_build_{uuid4().hex}"
        try:
            self.write_qlib_layout(
                temporary,
                frame,
                dates,
                symbols,
                request.universe_name,
                request.include_fields,
            )
            staging_metadata = self.read_staging_metadata(request.dataset_staging_id)
            limitations = staging_metadata.get("limitations") or {
                "point_in_time_status": "unknown",
                "survivorship_status": "unknown",
                "adjustment_status": {
                    "requested_policy": "unknown",
                    "effective_policy": "unknown",
                    "verification_status": "unknown",
                },
                "calendar_status": "unknown",
                "missing_data_policy": "no_fill_per_symbol",
                "provider_revision_status": "unknown",
                "production_eligibility": "blocked",
            }
            manifest_payload = {
                "schema_version": CANONICAL_SCHEMA_VERSION,
                "source_staging_revision_id": request.dataset_staging_id,
                "universe_name": request.universe_name,
                "include_fields": request.include_fields,
                "limitations": limitations,
                "files": self._manifest_files(temporary),
            }
            manifest_hash = content_hash("qlib_dataset_manifest", manifest_payload)
            dataset_revision_id = revision_id("dsr", manifest_hash)
            dataset_alias = str(request.dataset_alias)
            target = self.dataset_path(dataset_revision_id)
            self._validate_dataset_alias(dataset_alias, dataset_revision_id)
            manifest = {
                **manifest_payload,
                "dataset_revision_id": dataset_revision_id,
                "dataset_alias": dataset_alias,
                "manifest_hash": manifest_hash,
                "status": "ready",
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if target.exists():
                existing_errors, existing_hash, verified = self.inspect_dataset_manifest(
                    dataset_revision_id
                )
                if existing_errors or not verified or existing_hash != manifest_hash:
                    raise WorkerError(
                        "invalid_immutable_dataset",
                        "Existing dataset revision failed manifest verification.",
                        stage="dataset_build",
                    )
            else:
                try:
                    os.replace(temporary, target)
                except OSError:
                    if not target.exists():
                        raise
                    existing_errors, existing_hash, verified = (
                        self.inspect_dataset_manifest(dataset_revision_id)
                    )
                    if existing_errors or not verified or existing_hash != manifest_hash:
                        raise WorkerError(
                            "dataset_revision_conflict",
                            "Concurrent dataset revision publication conflicted.",
                            stage="dataset_build",
                        )
            self._record_dataset_alias(dataset_alias, dataset_revision_id, manifest_hash)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return DatasetBuildIdentity(
            schema_version=CANONICAL_SCHEMA_VERSION,
            dataset_revision_id=dataset_revision_id,
            dataset_alias=dataset_alias,
            source_staging_revision_id=request.dataset_staging_id,
            manifest_hash=manifest_hash,
            dates=dates,
            symbols=symbols,
        )

    def inspect_qlib_layout(
        self,
        dataset_id: str | Path,
    ) -> tuple[list[str], list[date], list[str]]:
        dataset_dir = (
            dataset_id if isinstance(dataset_id, Path) else self.dataset_path(dataset_id)
        )
        if not dataset_dir.is_dir():
            raise WorkerError(
                "unknown_dataset",
                f"Unknown dataset: {dataset_dir.name}",
                stage="validation",
            )
        errors: list[str] = []
        for name in ("calendars", "instruments", "features"):
            if not (dataset_dir / name).is_dir():
                errors.append(f"Missing Qlib dataset directory: {name}")
        calendar = self.read_calendar(dataset_dir / "calendars" / "day.txt", errors)
        universes = sorted(
            path.stem for path in (dataset_dir / "instruments").glob("*.txt")
        )
        if (dataset_dir / "instruments").is_dir() and not universes:
            errors.append("Missing Qlib universe file under instruments/")
        for instrument in self.instrument_symbols(dataset_dir):
            feature_dir = dataset_dir / "features" / instrument.lower()
            if not feature_dir.is_dir():
                errors.append(f"Missing feature directory: features/{instrument.lower()}")
                continue
            for field in ("close", "factor"):
                if not (feature_dir / f"{field}.day.bin").is_file():
                    errors.append(
                        f"Missing feature file: features/{instrument.lower()}/{field}.day.bin"
                    )
        return errors, calendar, universes

    def inspect_dataset_manifest(
        self,
        dataset_revision_id: str,
    ) -> tuple[list[str], str | None, bool]:
        dataset_dir = self.dataset_path(dataset_revision_id)
        manifest_path = dataset_dir / "manifest.json"
        if not manifest_path.is_file():
            return ["Missing immutable dataset manifest: manifest.json"], None, False
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
        actual_files = self._manifest_files(dataset_dir)
        errors: list[str] = []
        if manifest["schema_version"] != CANONICAL_SCHEMA_VERSION:
            errors.append("Dataset manifest schema version is unsupported")
        if manifest["status"] != "ready":
            errors.append("Dataset manifest status is not ready")
        if manifest["manifest_hash"] != actual_hash:
            errors.append("Dataset manifest hash does not match its canonical payload")
        if manifest["dataset_revision_id"] != dataset_revision_id:
            errors.append("Dataset manifest revision ID does not match the directory")
        if revision_id("dsr", actual_hash) != dataset_revision_id:
            errors.append("Dataset revision ID does not match the manifest hash")
        if manifest["files"] != actual_files:
            errors.append("Dataset files do not match the immutable manifest")
        return errors, str(manifest.get("manifest_hash") or actual_hash), not errors

    def read_dataset_manifest(self, dataset_revision_id: str) -> dict[str, object]:
        path = self.dataset_path(dataset_revision_id) / "manifest.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerError(
                "invalid_dataset_manifest",
                "Dataset manifest is unreadable.",
                stage="validation",
            ) from exc

    def dataset_path(self, dataset_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,80}", dataset_id):
            raise WorkerError("invalid_dataset_id", "Invalid dataset identifier.")
        return actor_directory(self.data_dir) / dataset_id

    def _validate_dataset_alias(self, alias: str, dataset_revision_id: str) -> None:
        path = self._dataset_alias_path(alias)
        if not path.exists():
            return
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerError(
                "invalid_dataset_alias",
                "Existing dataset alias record is unreadable.",
                stage="dataset_build",
            ) from exc
        if existing.get("dataset_revision_id") != dataset_revision_id:
            raise WorkerError(
                "dataset_alias_conflict",
                "Dataset alias already points to a different immutable revision.",
                stage="dataset_build",
            )

    def _record_dataset_alias(
        self,
        alias: str,
        dataset_revision_id: str,
        manifest_hash: str,
    ) -> None:
        path = self._dataset_alias_path(alias)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "dataset_alias": alias,
            "dataset_revision_id": dataset_revision_id,
            "manifest_hash": manifest_hash,
        }
        try:
            with path.open("x", encoding="utf-8") as file:
                json.dump(payload, file, indent=2, sort_keys=True)
        except FileExistsError:
            self._validate_dataset_alias(alias, dataset_revision_id)

    def _dataset_alias_path(self, alias: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,80}", alias):
            raise WorkerError("invalid_dataset_alias", "Invalid dataset alias.")
        return actor_directory(self.data_dir) / "_aliases" / f"{alias}.json"

    @staticmethod
    def _manifest_files(root: Path) -> list[dict[str, object]]:
        files: list[dict[str, object]] = []
        for path in sorted(
            candidate
            for candidate in root.rglob("*")
            if candidate.is_file() and candidate.name != "manifest.json"
        ):
            data = path.read_bytes()
            suffix = path.suffix.lower()
            media_type = (
                "text/plain; charset=utf-8"
                if suffix == ".txt"
                else "application/octet-stream"
            )
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "media_type": media_type,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        return files

    def staging_path(self, dataset_staging_id: str) -> Path:
        if not re.fullmatch(r"stg_[A-Za-z0-9._-]{1,120}", dataset_staging_id):
            raise WorkerError(
                "invalid_staging_id",
                "Invalid staging dataset identifier.",
                stage="validation",
            )
        return actor_directory(self.staging_dir) / dataset_staging_id

    def factor_snapshot_path(self, factor_snapshot_id: str) -> Path:
        if not re.fullmatch(r"fac_[A-Za-z0-9._-]{1,120}", factor_snapshot_id):
            raise WorkerError(
                "invalid_factor_snapshot_id",
                "Invalid factor snapshot identifier.",
                stage="factor_snapshot",
            )
        return actor_directory(self.staging_dir) / "factors" / factor_snapshot_id

    @staticmethod
    def effective_adjustment(adjustment: str) -> str:
        return "splits_and_dividends" if adjustment == "auto" else adjustment

    @staticmethod
    def _canonical_frame_records(
        frame: pd.DataFrame,
        fields: list[str],
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for source in frame[fields].to_dict(orient="records"):
            record: dict[str, object] = {}
            for field in fields:
                value = source[field]
                if hasattr(value, "item"):
                    value = value.item()
                if isinstance(value, pd.Timestamp):
                    value = value.to_pydatetime()
                if isinstance(value, datetime):
                    value = value.date() if field in {"date", "period_ending"} else value
                if pd.isna(value):
                    value = None
                record[field] = value
            records.append(record)
        return sorted(
            records,
            key=lambda item: canonical_json_bytes(item),
        )

    @staticmethod
    def _jsonl_bytes(records: list[dict[str, object]]) -> bytes:
        return b"".join(canonical_json_bytes(record) + b"\n" for record in records)

    def _publish_immutable_revision(
        self,
        target: Path,
        records: list[dict[str, object]],
        metadata: dict[str, object],
        expected_hash: str,
    ) -> None:
        if target.exists():
            self._verify_existing_revision(target, expected_hash)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".tmp_{target.name}_{uuid4().hex}"
        try:
            temporary.mkdir()
            (temporary / "data.jsonl").write_bytes(self._jsonl_bytes(records))
            (temporary / "metadata.json").write_text(
                json.dumps(canonicalize(metadata), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError:
            if target.exists():
                self._verify_existing_revision(target, expected_hash)
                return
            raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    @staticmethod
    def _verify_existing_revision(target: Path, expected_hash: str) -> None:
        try:
            metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerError(
                "invalid_immutable_revision",
                "Existing immutable revision metadata is unreadable.",
                stage="persistence",
            ) from exc
        if metadata.get("content_hash") != expected_hash:
            raise WorkerError(
                "revision_hash_collision",
                "Existing immutable revision has a different content hash.",
                stage="persistence",
            )

    def _record_request_link(
        self,
        namespace: str,
        fingerprint: str,
        digest: str,
        revision: str,
        retrieved_at: str,
    ) -> None:
        request_dir = (
            actor_directory(self.staging_dir)
            / "_requests"
            / namespace
            / fingerprint
        )
        request_dir.mkdir(parents=True, exist_ok=True)
        link_path = request_dir / f"{digest.removeprefix('sha256:')}.json"
        payload = {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "request_fingerprint": fingerprint,
            "content_hash": digest,
            "revision_id": revision,
            "retrieved_at": retrieved_at,
        }
        try:
            with link_path.open("x", encoding="utf-8") as file:
                json.dump(payload, file, indent=2, sort_keys=True)
        except FileExistsError:
            return

    @staticmethod
    def write_qlib_layout(
        root: Path,
        frame: pd.DataFrame,
        dates: list[date],
        symbols: list[str],
        universe_name: str,
        fields: list[str],
    ) -> None:
        (root / "calendars").mkdir(parents=True)
        (root / "instruments").mkdir()
        (root / "features").mkdir()
        (root / "calendars" / "day.txt").write_text(
            "\n".join(str(value) for value in dates) + "\n",
            encoding="utf-8",
        )
        date_index = {value: index for index, value in enumerate(dates)}
        instrument_lines: list[str] = []
        for symbol in symbols:
            symbol_frame = frame[frame["symbol"] == symbol].copy()
            symbol_dates = pd.to_datetime(symbol_frame["date"]).dt.date
            first = min(symbol_dates)
            last = max(symbol_dates)
            instrument = _qlib_instrument(symbol)
            instrument_lines.append(f"{instrument}\t{first}\t{last}")
            symbol_frame = symbol_frame.assign(date=symbol_dates).set_index("date")
            start_index = date_index[first]
            calendar_slice = dates[start_index : date_index[last] + 1]
            feature_dir = root / "features" / instrument.lower()
            feature_dir.mkdir()
            for field in fields:
                values = symbol_frame.reindex(calendar_slice)[field].astype("float32").to_numpy()
                np.hstack([start_index, values]).astype("<f").tofile(
                    feature_dir / f"{field}.day.bin"
                )
        (root / "instruments" / f"{universe_name.lower()}.txt").write_text(
            "\n".join(instrument_lines) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def read_calendar(path: Path, errors: list[str]) -> list[date]:
        if not path.is_file():
            errors.append("Missing Qlib daily calendar: calendars/day.txt")
            return []
        try:
            calendar = [
                date.fromisoformat(line.strip())
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, ValueError):
            errors.append("Qlib daily calendar is unreadable or contains invalid dates")
            return []
        if not calendar:
            errors.append("Qlib daily calendar is empty")
        elif any(left >= right for left, right in zip(calendar, calendar[1:])):
            errors.append("Qlib daily calendar must be strictly increasing")
            return []
        return calendar

    @staticmethod
    def instrument_symbols(dataset_dir: Path) -> list[str]:
        symbols: list[str] = []
        instruments_dir = dataset_dir / "instruments"
        if not instruments_dir.is_dir():
            return symbols
        for path in instruments_dir.glob("*.txt"):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 3 and parts[0]:
                    symbols.append(parts[0])
        return symbols


def _qlib_instrument(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", symbol).upper()
