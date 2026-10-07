"""OpenBB SDK adapter."""

import importlib

from worker.domain import WorkerError
from worker.models import FetchOhlcvRequest


class OpenBBFetcher:
    """Small adapter around the OpenBB SDK."""

    def __init__(self, obb: object | None = None, import_error: WorkerError | None = None) -> None:
        self._obb = obb
        self._import_error = import_error

    @classmethod
    def preload(cls) -> "OpenBBFetcher":
        """Import OpenBB on the main thread before FastAPI runs sync endpoints."""
        try:
            return cls(obb=cls._load_obb())
        except WorkerError as exc:
            return cls(import_error=exc)

    def fetch_ohlcv(self, request: FetchOhlcvRequest) -> object:
        obb = self._require_obb()
        kwargs = {
            "symbol": ",".join(request.symbols),
            "start_date": request.start_date,
            "end_date": request.end_date,
            "provider": request.provider,
        }
        adjustment = self._openbb_adjustment(request.adjustment)
        if adjustment is not None:
            kwargs["adjustment"] = adjustment
        try:
            return obb.equity.price.historical(**kwargs)
        except Exception as exc:
            raise WorkerError(
                "openbb_fetch_failed",
                f"OpenBB fetch failed: {str(exc)[:300]}",
            ) from exc

    def fetch_factor_snapshot(self, symbol: str, provider: str) -> object:
        obb = self._require_obb()
        try:
            return obb.equity.fundamental.metrics(symbol=symbol, provider=provider)
        except Exception as exc:
            raise WorkerError(
                "openbb_factor_fetch_failed",
                f"OpenBB factor fetch failed for {symbol}: {str(exc)[:300]}",
                stage="factor_snapshot",
            ) from exc

    def _require_obb(self) -> object:
        if self._import_error is not None:
            raise self._import_error
        if self._obb is None:
            self._obb = self._load_obb()
        return self._obb

    @staticmethod
    def _load_obb() -> object:
        try:
            return importlib.import_module("openbb").obb
        except ImportError as exc:
            raise WorkerError(
                "openbb_unavailable",
                "OpenBB is not installed in the quant data worker image.",
            ) from exc
        except Exception as exc:
            raise WorkerError(
                "openbb_unavailable",
                f"OpenBB failed to initialize: {str(exc)[:300]}",
            ) from exc

    @staticmethod
    def _openbb_adjustment(adjustment: str) -> str | None:
        return {
            "auto": "splits_and_dividends",
            "adjusted": "splits_and_dividends",
            "unadjusted": "unadjusted",
            "provider_default": None,
        }[adjustment]
