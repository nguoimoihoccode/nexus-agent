"""Runtime helpers shared by the AI-Trader API and refresh daemon."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def nexus_data_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return Path(values.get("NEXUS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


def runtime_db_path(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return Path(values.get("DB_PATH", nexus_data_dir(values) / "ai-trader" / "clawtrader.db"))
