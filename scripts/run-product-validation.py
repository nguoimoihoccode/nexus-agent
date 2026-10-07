#!/usr/bin/env python3
"""Run deterministic product workflow scenarios and emit validation evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "evaluation" / "product-validation-v1.json"
ALLOWED_PROJECTS = {
    "backend",
    "quant-data-worker",
    "quant-worker",
    "ai-trader-worker",
    "frontend",
}


def _command(scenario: dict[str, Any]) -> tuple[list[str], Path]:
    project = str(scenario.get("project") or "")
    if project not in ALLOWED_PROJECTS:
        raise ValueError(f"Unsupported evaluation project: {project}")
    workdir = ROOT / project
    runner = scenario.get("runner")
    if runner == "python_unittest":
        target = str(scenario.get("target") or "")
        if not target.startswith("tests.") or not all(
            character.isalnum() or character in "._" for character in target
        ):
            raise ValueError(f"Invalid unittest target for {scenario.get('id')}")
        return [str(workdir / ".venv/bin/python"), "-m", "unittest", target], workdir
    if runner == "node_test":
        relative = Path(str(scenario.get("file") or ""))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".js":
            raise ValueError(f"Invalid Node test path for {scenario.get('id')}")
        pattern = str(scenario.get("pattern") or "")
        if not pattern or len(pattern) > 160:
            raise ValueError(f"Invalid Node test pattern for {scenario.get('id')}")
        return [
            "node",
            "--test",
            f"--test-name-pattern={pattern}",
            str(relative),
        ], workdir
    if runner == "vitest":
        relative = Path(str(scenario.get("file") or ""))
        if (
            project != "frontend"
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix not in {".ts", ".tsx"}
        ):
            raise ValueError(f"Invalid Vitest path for {scenario.get('id')}")
        pattern = str(scenario.get("pattern") or "")
        if not pattern or len(pattern) > 160:
            raise ValueError(f"Invalid Vitest pattern for {scenario.get('id')}")
        return [
            "npm",
            "exec",
            "--",
            "vitest",
            "run",
            str(relative),
            "--testNamePattern",
            pattern,
        ], workdir
    raise ValueError(f"Unsupported evaluation runner: {runner}")


def _run(scenario: dict[str, Any]) -> dict[str, Any]:
    command, workdir = _command(scenario)
    started = time.monotonic()
    env = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "NEXUS_ENV": "test",
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", "offline-eval"),
    }
    result = subprocess.run(
        command,
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    return {
        "id": scenario["id"],
        "status": "passed" if result.returncode == 0 else "failed",
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "tags": scenario.get("tags", []),
        "failure_excerpt": output[-2000:] if result.returncode else None,
    }


def _rates(results: list[dict[str, Any]]) -> dict[str, float | int]:
    total = len(results)
    passed = sum(item["status"] == "passed" for item in results)

    def failure_rate(tag: str) -> float:
        selected = [item for item in results if tag in item["tags"]]
        if not selected:
            return 0.0
        return round(
            sum(item["status"] != "passed" for item in selected) / len(selected),
            6,
        )

    return {
        "scenario_count": total,
        "passed_count": passed,
        "workflow_completion_rate": round(passed / total, 6) if total else 0.0,
        "duplicate_side_effect_rate": failure_rate("idempotency"),
        "contract_failure_rate": failure_rate("contract"),
        "unsafe_tool_attempt_rate": failure_rate("unsafe_tool"),
        "resume_reconnect_failure_rate": failure_rate("reconnect"),
        "tenant_isolation_failure_rate": failure_rate("tenant_isolation"),
        "financial_correctness_failure_rate": failure_rate("financial_correctness"),
        "paid_api_calls": 0,
        "model_cost_usd": 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios")
    if manifest.get("schema_version") != "1" or not isinstance(scenarios, list):
        raise ValueError("Unsupported product validation manifest.")
    if len({item.get("id") for item in scenarios}) != len(scenarios):
        raise ValueError("Evaluation scenario IDs must be unique.")
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        futures = {executor.submit(_run, item): item["id"] for item in scenarios}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "id": futures[future],
                        "status": "failed",
                        "duration_ms": 0,
                        "tags": ["acceptance_failure"],
                        "failure_excerpt": f"{type(exc).__name__}: {str(exc)[:500]}",
                    }
                )
    results.sort(key=lambda item: item["id"])
    metrics = _rates(results)
    report = {
        "schema_version": "1",
        "suite": manifest["suite"],
        "manifest": str(args.manifest.relative_to(ROOT)),
        "generated_at": datetime.now(UTC).isoformat(),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "offline": True,
        "metrics": metrics,
        "acceptance_failures": [
            item["id"]
            for item in results
            if "acceptance_failure" in item["tags"] and item["status"] != "passed"
        ],
        "results": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if not report["acceptance_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
