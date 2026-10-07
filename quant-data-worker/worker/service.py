"""Runtime composition for the quant-data application and adapters."""

import logging
import os
from typing import Any

from worker.adapters import MarketDataStore, OpenBBFetcher
from worker.ports import QuantDataControlPlane
from worker.application import MarketDataApplicationService
from worker.observability import log_event

logger = logging.getLogger(__name__)


def _emit_event(event: str, **fields: Any) -> None:
    log_event(logger, event, **fields)


class OpenBBDataWorker(MarketDataApplicationService):
    """Worker composed from explicit application ports and adapters."""

    def __init__(
        self,
        store: MarketDataStore,
        fetcher: OpenBBFetcher | None = None,
        control_plane: QuantDataControlPlane | None = None,
    ) -> None:
        super().__init__(
            store,
            fetcher or OpenBBFetcher(),
            event_sink=_emit_event,
            control_plane=control_plane,
        )

    def readiness(self) -> bool:
        paths_ready = all(
            path.is_dir() and os.access(path, os.R_OK | os.W_OK)
            for path in (self.store.staging_dir, self.store.data_dir)
        )
        return paths_ready and self.control_plane.readiness()

    def close(self) -> None:
        self.control_plane.close()
