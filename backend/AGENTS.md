# Backend Agent Instructions

## Scope

These instructions apply to `backend/`. The root `AGENTS.md` also applies.

## Architecture and conventions

- Start with `../docs/architecture/overview.md`.
- Python is pinned to `>=3.11,<3.12`; use modern type annotations.
- Keep graph assembly in `source/agents/`, shared policy/configuration in
  `source/core/`, persistence in `source/infrastructure/`, and tool adapters in
  `source/tools/`.
- Treat `langgraph.json` as the registry of public graph entrypoints.
- Use async APIs for database, graph, and streaming paths. Keep blocking SDK setup
  away from the event loop when the surrounding code already uses `asyncio.to_thread`.
- Load configuration through `source/core/config.py`; never hardcode credentials.

## Deep Agents harness

- Treat the supervisor prompt constant in `source/agents/factory.py`, subagent
  prompt files, `.deepagents/skills/*/SKILL.md`, and `.deepagents/nexus.json` as
  one wiring surface.
- When adding or renaming an agent or skill, update all runtime and visualization
  consumers described in `../docs/architecture/overview.md`.
- Keep subagent permissions least-privileged. Researcher and quant researcher work
  is read-only.
- Do not place private user data in public documentation or test fixtures.

## Verification

- From `backend/`, run `uv run python -m compileall source`.
- For graph startup or integration behavior, prefer the repository's LangGraph dev
  command and document any required external services.
- Do not claim backend tests passed when no backend test suite exists.
