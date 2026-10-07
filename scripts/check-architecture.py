#!/usr/bin/env python3
"""Fail fast when source dependencies cross the documented architecture boundaries."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKERS = ("quant-worker", "quant-data-worker", "ai-trader-worker")
LAYER_RULES = {
    "domain": (
        "fastapi",
        "httpx",
        "numpy",
        "pandas",
        "pydantic",
        "sqlite3",
        "worker.adapters",
        "worker.application",
        "worker.models",
        "worker.ports",
        "worker.service",
    ),
    "ports": (
        "fastapi",
        "httpx",
        "sqlite3",
        "worker.adapters",
        "worker.application",
        "worker.service",
    ),
    "application": (
        "fastapi",
        "httpx",
        "sqlite3",
        "worker.adapters",
        "worker.service",
    ),
}
MAX_LINES = {
    "backend/source/agents/agent.py": 100,
    "quant-worker/worker/service.py": 220,
    "quant-data-worker/worker/service.py": 220,
    "ai-trader-worker/worker/service.py": 220,
    "frontend/src/App.tsx": 550,
    "frontend/src/hooks/useChat.ts": 380,
    "frontend/src/api/chat.ts": 220,
    "frontend/src/api/chatProtocol.ts": 400,
    "frontend/src/features/chat/model/chatState.ts": 300,
    "frontend/src/features/console/pages.tsx": 450,
    "frontend/src/features/dossier/DossierPage.tsx": 480,
}
JS_IMPORT = re.compile(r"(?:import|export)\s+(?:[\s\S]*?\s+from\s+)?[\"']([^\"']+)[\"']")


def imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append((node.lineno, node.module))
    return modules


def matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def check_python_layers(errors: list[str]) -> None:
    for worker in WORKERS:
        worker_root = ROOT / worker / "worker"
        for layer, forbidden in LAYER_RULES.items():
            layer_root = worker_root / layer
            if not layer_root.is_dir():
                continue
            for path in sorted(layer_root.glob("*.py")):
                for line, module in imported_modules(path):
                    blocked = next((prefix for prefix in forbidden if matches(module, prefix)), None)
                    if blocked:
                        relative = path.relative_to(ROOT)
                        errors.append(
                            f"{relative}:{line}: {layer} must not import {module} "
                            f"(blocked dependency: {blocked})"
                        )

    contracts = ROOT / "backend/source/contracts"
    forbidden_contracts = (
        "source.agents",
        "source.infrastructure",
        "source.integrations",
        "source.security",
        "source.tools",
    )
    for path in sorted(contracts.glob("*.py")):
        for line, module in imported_modules(path):
            blocked = next(
                (prefix for prefix in forbidden_contracts if matches(module, prefix)),
                None,
            )
            if blocked:
                relative = path.relative_to(ROOT)
                errors.append(f"{relative}:{line}: contracts must not depend on {module}")


def check_frontend_boundaries(errors: list[str]) -> None:
    rules = {
        ROOT / "frontend/src/data": ("react", "lucide-react"),
        ROOT / "frontend/src/features/chat/model": ("react", "../../api", "../../../api"),
    }
    for directory, forbidden in rules.items():
        for path in sorted(
            path for path in directory.rglob("*") if path.suffix in {".ts", ".tsx"}
        ):
            source = path.read_text(encoding="utf-8")
            for module in JS_IMPORT.findall(source):
                blocked = next(
                    (prefix for prefix in forbidden if module == prefix or module.startswith(f"{prefix}/")),
                    None,
                )
                if blocked:
                    relative = path.relative_to(ROOT)
                    errors.append(f"{relative}: model/data source must not import {module}")

    protocol = ROOT / "frontend/src/api/chatProtocol.ts"
    if "fetch(" in protocol.read_text(encoding="utf-8"):
        errors.append("frontend/src/api/chatProtocol.ts: protocol parser must not perform HTTP fetches")


def check_hotspots(errors: list[str]) -> None:
    for relative, maximum in MAX_LINES.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"{relative}: required architecture entrypoint is missing")
            continue
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > maximum:
            errors.append(f"{relative}: {count} lines exceeds the architecture budget of {maximum}")


def check_runtime_registry(errors: list[str]) -> None:
    backend_root = ROOT / "backend"
    sys.path.insert(0, str(backend_root))
    try:
        from source.agents.config.registry import (  # noqa: PLC0415
            validate_repository_registry,
        )

        validate_repository_registry()
    except Exception as exc:
        errors.append(f"runtime registry validation failed: {exc}")
    finally:
        sys.path.remove(str(backend_root))


def main() -> int:
    errors: list[str] = []
    check_python_layers(errors)
    check_frontend_boundaries(errors)
    check_hotspots(errors)
    check_runtime_registry(errors)
    if errors:
        print("Architecture boundary check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Architecture boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
