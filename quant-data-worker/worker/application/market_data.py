"""OpenBB ingestion and Qlib dataset application workflows."""

import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from worker.domain import (
    DEFAULT_WARNINGS,
    FACTOR_FIELDS,
    FACTOR_WARNINGS,
    LABEL_LOOKAHEAD_DAYS,
    OHLC_FIELDS,
    STAGING_SCHEMA,
    WorkerError,
)
from worker.models import (
    BuildQlibDatasetRequest,
    FactorRecord,
    FactorSnapshotLimitations,
    FactorSnapshotRequest,
    FactorSnapshotResponse,
    FetchOhlcvRequest,
    FetchOhlcvResponse,
    MarketDataLimitations,
    MarketDataSource,
    MarketDataValidation,
    QlibDatasetBuildResponse,
    QlibDatasetValidation,
)
from worker.ports import (
    MarketDataFetcher,
    MarketDataRepository,
    NullQuantDataControlPlane,
    QuantDataControlPlane,
    QuantDataControlPlaneError,
)

EventSink = Callable[..., None]


class MarketDataApplicationService:
    """Coordinate provider fetches, normalization, validation, and persistence."""

    def __init__(
        self,
        store: MarketDataRepository,
        fetcher: MarketDataFetcher,
        *,
        event_sink: EventSink,
        control_plane: QuantDataControlPlane | None = None,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self._event_sink = event_sink
        self.control_plane = control_plane or NullQuantDataControlPlane()

    def fetch_openbb_ohlcv(self, request: FetchOhlcvRequest) -> FetchOhlcvResponse:
        try:
            guard = self.control_plane.ingestion_guard(
                "provider.openbb_ohlcv",
                request,
                FetchOhlcvResponse,
            )
            with guard as existing:
                if existing is not None:
                    return existing
                return self._fetch_openbb_ohlcv_once(request)
        except QuantDataControlPlaneError as exc:
            raise self._idempotency_error(exc) from exc

    def _fetch_openbb_ohlcv_once(
        self,
        request: FetchOhlcvRequest,
    ) -> FetchOhlcvResponse:
        raw = self.fetcher.fetch_ohlcv(request)
        frame = self._normalize_ohlcv(raw, request)
        if frame.empty:
            raise WorkerError("no_market_data", "OpenBB returned no usable OHLCV rows.")
        loaded = sorted(frame["symbol"].unique().tolist())
        warnings = self._warnings_for_symbols(request.symbols, loaded)
        revision = self.store.save_staging(request, frame, warnings)
        response = FetchOhlcvResponse(
            staging_revision_id=revision.revision_id,
            dataset_staging_id=revision.revision_id,
            request_fingerprint=revision.request_fingerprint,
            content_hash=revision.content_hash,
            retrieved_at=revision.retrieved_at,
            source=MarketDataSource(
                provider=request.provider,
                frequency=request.frequency,
                adjustment=self.store.effective_adjustment(request.adjustment),
            ),
            symbols_requested=len(request.symbols),
            symbols_loaded=len(loaded),
            rows=len(frame),
            schema=STAGING_SCHEMA,
            date_range={"start": frame["date"].min(), "end": frame["date"].max()},
            warnings=warnings,
            limitations=MarketDataLimitations.model_validate(
                self.store.read_staging_metadata(revision.revision_id)["limitations"]
            ),
        )
        self._persist_control_plane(self.control_plane.record_staging, request, response)
        self._event_sink(
            "market_data.fetched",
            staging_revision_id=revision.revision_id,
            request_fingerprint=revision.request_fingerprint,
            content_hash=revision.content_hash,
            provider=request.provider,
            symbols_requested=len(request.symbols),
            symbols_loaded=len(loaded),
            rows=len(frame),
        )
        return response

    def fetch_factor_snapshot(self, request: FactorSnapshotRequest) -> FactorSnapshotResponse:
        try:
            guard = self.control_plane.ingestion_guard(
                "provider.factor_snapshot",
                request,
                FactorSnapshotResponse,
            )
            with guard as existing:
                if existing is not None:
                    return existing
                return self._fetch_factor_snapshot_once(request)
        except QuantDataControlPlaneError as exc:
            raise self._idempotency_error(exc) from exc

    @staticmethod
    def _idempotency_error(exc: QuantDataControlPlaneError) -> WorkerError:
        conflict = str(exc).startswith("idempotency_conflict:")
        return WorkerError(
            "idempotency_conflict" if conflict else "idempotency_unavailable",
            (
                "Provider-ingestion retry conflicts with different arguments."
                if conflict
                else "Durable provider-ingestion control is unavailable."
            ),
            stage="idempotency",
            retryable=not conflict,
        )

    def _fetch_factor_snapshot_once(
        self,
        request: FactorSnapshotRequest,
    ) -> FactorSnapshotResponse:
        records: list[dict[str, object]] = []
        warnings = FACTOR_WARNINGS.copy()
        for symbol in request.symbols:
            try:
                raw = self.fetcher.fetch_factor_snapshot(symbol, request.provider)
                record = self._normalize_factor_snapshot(raw, symbol)
            except WorkerError as exc:
                warnings.append(exc.message)
                continue
            if record is None:
                warnings.append(f"No factor data returned for {symbol}.")
                continue
            records.append(record)

        if not records:
            raise WorkerError(
                "no_factor_data",
                "OpenBB returned no usable factor data for the requested symbols.",
                stage="factor_snapshot",
            )

        frame = pd.DataFrame(records, columns=FACTOR_FIELDS)
        revision = self.store.save_factor_snapshot(request, frame, warnings)
        factors = [
            FactorRecord.model_validate(row)
            for row in frame.where(pd.notnull(frame), None).to_dict(orient="records")
        ]
        response = FactorSnapshotResponse(
            factor_snapshot_revision_id=revision.revision_id,
            factor_snapshot_id=revision.revision_id,
            request_fingerprint=revision.request_fingerprint,
            content_hash=revision.content_hash,
            retrieved_at=revision.retrieved_at,
            provider=request.provider,
            as_of_date=request.as_of_date,
            symbols_requested=len(request.symbols),
            symbols_loaded=len(factors),
            factors=factors,
            warnings=warnings,
            limitations=FactorSnapshotLimitations.model_validate(
                self.store.read_factor_snapshot_metadata(revision.revision_id)[
                    "limitations"
                ]
            ),
        )
        self._persist_control_plane(
            self.control_plane.record_factor_snapshot,
            request,
            response,
        )
        return response

    def validate_market_data(self, dataset_staging_id: str) -> MarketDataValidation:
        validation = self._validate_market_frame(
            self.store.load_staging(dataset_staging_id),
            dataset_staging_id,
        )
        self._persist_control_plane(
            self.control_plane.record_validation,
            dataset_staging_id,
            "staging_revision",
            validation.model_dump(mode="json"),
        )
        return validation

    def build_qlib_dataset(
        self,
        request: BuildQlibDatasetRequest,
    ) -> QlibDatasetBuildResponse:
        frame = self.store.load_staging(request.dataset_staging_id)
        validation = self._validate_market_frame(frame, request.dataset_staging_id)
        self._persist_control_plane(
            self.control_plane.record_validation,
            request.dataset_staging_id,
            "staging_revision",
            validation.model_dump(mode="json"),
        )
        if not validation.valid:
            raise WorkerError(
                "invalid_market_data",
                "Staged market data failed validation.",
                stage="validation",
            )
        build = self.store.build_dataset(request, frame)
        manifest = self.store.read_dataset_manifest(build.dataset_revision_id)
        dates = build.dates
        symbols = build.symbols
        response = QlibDatasetBuildResponse(
            dataset_revision_id=build.dataset_revision_id,
            dataset_id=build.dataset_revision_id,
            dataset_alias=build.dataset_alias,
            provider_uri=str(Path("qlib-data") / build.dataset_revision_id),
            source_staging_revision_id=build.source_staging_revision_id,
            source_staging_id=build.source_staging_revision_id,
            manifest_hash=build.manifest_hash,
            calendar={
                "start_date": min(dates),
                "end_date": max(dates),
                "trading_days": len(dates),
            },
            universe={"name": request.universe_name, "symbols": len(symbols)},
            qlib_layout={"calendars": True, "features": True, "instruments": True},
            warnings=DEFAULT_WARNINGS.copy(),
            limitations=MarketDataLimitations.model_validate(manifest["limitations"]),
        )
        self._persist_control_plane(
            self.control_plane.record_dataset,
            request,
            response,
            manifest,
        )
        self._event_sink(
            "dataset.built",
            dataset_revision_id=build.dataset_revision_id,
            dataset_alias=build.dataset_alias,
            source_staging_revision_id=build.source_staging_revision_id,
            manifest_hash=build.manifest_hash,
            universe=request.universe_name,
            symbols=len(symbols),
            trading_days=len(dates),
        )
        return response

    @staticmethod
    def _persist_control_plane(callback, *args: Any) -> None:
        try:
            callback(*args)
        except QuantDataControlPlaneError as exc:
            raise WorkerError(
                "domain_persistence_failed",
                "Durable quant-data metadata could not be committed.",
                stage="persistence",
                retryable=True,
            ) from exc

    def validate_qlib_dataset(self, dataset_id: str) -> QlibDatasetValidation:
        errors, calendar, universes = self.store.inspect_qlib_layout(dataset_id)
        manifest_errors, manifest_hash, manifest_verified = (
            self.store.inspect_dataset_manifest(dataset_id)
        )
        errors.extend(manifest_errors)
        manifest = self.store.read_dataset_manifest(dataset_id)
        validation = QlibDatasetValidation(
            valid=not errors,
            dataset_revision_id=dataset_id,
            dataset_id=dataset_id,
            provider_uri=str(Path("qlib-data") / dataset_id),
            start_date=calendar[0] if calendar else None,
            end_date=calendar[-1] if calendar else None,
            max_test_end=(
                calendar[-(LABEL_LOOKAHEAD_DAYS + 1)]
                if len(calendar) > LABEL_LOOKAHEAD_DAYS
                else None
            ),
            trading_days=len(calendar) if calendar else None,
            universes=universes,
            manifest_hash=manifest_hash,
            manifest_verified=manifest_verified,
            warnings=DEFAULT_WARNINGS.copy(),
            errors=errors,
            limitations=MarketDataLimitations.model_validate(
                manifest.get("limitations")
                or {
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
            ),
        )
        self._persist_control_plane(
            self.control_plane.record_validation,
            dataset_id,
            "dataset_revision",
            validation.model_dump(mode="json"),
        )
        return validation

    def _normalize_ohlcv(
        self,
        raw: object,
        request: FetchOhlcvRequest,
    ) -> pd.DataFrame:
        frame = self._as_frame(raw)
        frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
        frame = frame.rename(
            columns={"datetime": "date", "timestamp": "date", "ticker": "symbol"}
        )
        if "date" not in frame.columns:
            first_index = frame.index.names[0] if frame.index.names else None
            frame = frame.reset_index()
            if first_index and first_index in frame.columns:
                frame = frame.rename(columns={first_index: "date"})
            elif "index" in frame.columns:
                frame = frame.rename(columns={"index": "date"})
        if "symbol" not in frame.columns and len(request.symbols) == 1:
            frame["symbol"] = request.symbols[0]
        missing = {"date", "symbol", *OHLC_FIELDS, "volume"} - set(frame.columns)
        if missing:
            raise WorkerError(
                "invalid_openbb_response",
                f"OpenBB response is missing columns: {', '.join(sorted(missing))}",
            )
        normalized = frame[["date", "symbol", *OHLC_FIELDS, "volume"]].copy()
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.date
        normalized["symbol"] = normalized["symbol"].astype(str).str.upper().str.strip()
        for field in [*OHLC_FIELDS, "volume"]:
            normalized[field] = pd.to_numeric(normalized[field], errors="coerce")
        normalized["factor"] = 1.0
        normalized = normalized.dropna(subset=["date", "symbol", *OHLC_FIELDS, "volume"])
        normalized = normalized[normalized["symbol"].isin(request.symbols)]
        normalized = normalized.sort_values(["symbol", "date"]).reset_index(drop=True)
        return normalized[STAGING_SCHEMA]

    def _normalize_factor_snapshot(
        self,
        raw: object,
        symbol: str,
    ) -> dict[str, object] | None:
        frame = self._as_frame(raw)
        if frame.empty:
            return None
        frame = self._normalize_factor_columns(frame)
        if "symbol" in frame.columns:
            symbols = frame["symbol"].astype(str).str.upper().str.strip()
            matching = frame[symbols == symbol]
            row = matching.iloc[0] if not matching.empty else frame.iloc[0]
        else:
            row = frame.iloc[0]

        record: dict[str, object] = {"symbol": symbol}
        aliases = self._factor_aliases()
        for field in FACTOR_FIELDS:
            if field == "symbol":
                continue
            value = self._first_present(row, aliases[field])
            if field == "period_ending":
                record[field] = self._date_or_none(value)
            elif field == "currency":
                record[field] = self._string_or_none(value)
            else:
                record[field] = self._number_or_none(value)
        return record

    @staticmethod
    def _normalize_factor_columns(frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        normalized.columns = [
            re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
            for column in normalized.columns
        ]
        return normalized

    @staticmethod
    def _factor_aliases() -> dict[str, list[str]]:
        return {
            "market_cap": ["market_cap", "market_capitalization", "mkt_cap"],
            "pe_ratio": [
                "pe_ratio",
                "trailing_pe",
                "price_earnings_ratio",
                "price_to_earnings",
                "pe",
            ],
            "price_to_book": ["price_to_book", "pb_ratio", "price_book_value"],
            "price_to_sales": ["price_to_sales", "ps_ratio", "price_sales_ratio"],
            "debt_to_equity": ["debt_to_equity", "debt_equity_ratio"],
            "return_on_equity": ["return_on_equity", "roe"],
            "revenue_growth": [
                "revenue_growth",
                "revenue_growth_yoy",
                "revenue_per_share_growth",
            ],
            "gross_margin": ["gross_margin"],
            "operating_margin": ["operating_margin"],
            "profit_margin": ["profit_margin", "net_margin"],
            "currency": ["currency", "financial_currency"],
            "period_ending": ["period_ending", "period_end_date", "fiscal_date", "date"],
        }

    @staticmethod
    def _first_present(row: pd.Series, aliases: list[str]) -> object | None:
        for alias in aliases:
            if alias in row.index and pd.notna(row[alias]):
                return row[alias]
        return None

    @staticmethod
    def _number_or_none(value: object | None) -> float | None:
        if value is None:
            return None
        number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        return None if pd.isna(number) else float(number)

    @staticmethod
    def _string_or_none(value: object | None) -> str | None:
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _date_or_none(value: object | None) -> date | None:
        if value is None or pd.isna(value):
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()

    def _validate_market_frame(
        self,
        frame: pd.DataFrame,
        dataset_staging_id: str,
    ) -> MarketDataValidation:
        errors: list[str] = []
        required_columns = all(column in frame.columns for column in STAGING_SCHEMA)
        working = frame.copy()
        if "date" in working.columns:
            working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.date
        for field in [*OHLC_FIELDS, "volume", "factor"]:
            if field in working.columns:
                working[field] = pd.to_numeric(working[field], errors="coerce")
        duplicate_rows = (
            int(working.duplicated(["date", "symbol"]).sum()) if required_columns else 0
        )
        missing_required = (
            int(working[STAGING_SCHEMA].isna().sum().sum())
            if required_columns
            else len(working)
        )
        negative_volume = int((working.get("volume", pd.Series(dtype=float)) < 0).sum())
        ohlc_errors = self._ohlc_consistency_errors(working) if required_columns else 0
        if not required_columns:
            missing = sorted(set(STAGING_SCHEMA) - set(frame.columns))
            errors.append(f"Missing required columns: {', '.join(missing)}")
        if duplicate_rows:
            errors.append("Duplicate (date, symbol) rows exist")
        if missing_required:
            errors.append("Required values are missing or non-numeric")
        if negative_volume:
            errors.append("Volume contains negative values")
        if ohlc_errors:
            errors.append("OHLC consistency checks failed")
        symbols = (
            sorted(working["symbol"].dropna().unique().tolist())
            if "symbol" in working
            else []
        )
        date_range = None
        if "date" in working and not working["date"].dropna().empty:
            date_range = {"start": working["date"].min(), "end": working["date"].max()}
        return MarketDataValidation(
            valid=not errors,
            dataset_staging_id=dataset_staging_id,
            rows=len(working),
            symbols=symbols,
            date_range=date_range,
            quality_checks={
                "required_columns": required_columns,
                "date_sorted": self._is_sorted(working) if required_columns else False,
                "duplicate_rows": duplicate_rows,
                "missing_required_values": missing_required,
                "negative_volume_rows": negative_volume,
                "ohlc_consistency_errors": ohlc_errors,
            },
            missing_by_symbol=(
                self._missing_by_symbol(working) if required_columns else {}
            ),
            warnings=DEFAULT_WARNINGS.copy(),
            errors=errors,
        )

    @staticmethod
    def _as_frame(raw: object) -> pd.DataFrame:
        if isinstance(raw, pd.DataFrame):
            return raw.copy()
        for attr in ("to_df", "to_dataframe"):
            method = getattr(raw, attr, None)
            if callable(method):
                result = method()
                if isinstance(result, pd.DataFrame):
                    return result.copy()
        try:
            return pd.DataFrame(raw)
        except ValueError as exc:
            raise WorkerError(
                "invalid_openbb_response",
                "OpenBB response is not tabular.",
            ) from exc

    @staticmethod
    def _ohlc_consistency_errors(frame: pd.DataFrame) -> int:
        checks = (
            (frame["high"] < frame["low"])
            | (frame["high"] < frame["open"])
            | (frame["high"] < frame["close"])
            | (frame["low"] > frame["open"])
            | (frame["low"] > frame["close"])
        )
        return int(checks.sum())

    @staticmethod
    def _missing_by_symbol(frame: pd.DataFrame) -> dict[str, int]:
        counts: dict[str, int] = {}
        for symbol, group in frame.groupby("symbol"):
            days = pd.to_datetime(group["date"]).dt.date
            if days.empty:
                counts[str(symbol)] = 0
                continue
            expected = pd.bdate_range(min(days), max(days)).date
            counts[str(symbol)] = max(0, len(set(expected) - set(days)))
        return counts

    @staticmethod
    def _is_sorted(frame: pd.DataFrame) -> bool:
        ordered = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
        return frame.reset_index(drop=True)[["symbol", "date"]].equals(
            ordered[["symbol", "date"]]
        )

    @staticmethod
    def _warnings_for_symbols(requested: list[str], loaded: list[str]) -> list[str]:
        warnings = DEFAULT_WARNINGS.copy()
        missing = sorted(set(requested) - set(loaded))
        if missing:
            warnings.append(f"OpenBB returned no rows for symbols: {', '.join(missing)}")
        return warnings
