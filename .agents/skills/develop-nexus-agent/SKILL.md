---
name: develop-nexus-agent
description: Implement, debug, or review changes in the Nexus Agent Python/LangGraph backend or React/Vite frontend. Use for application features, bug fixes, API streaming changes, graph visualization work, tests, builds, and cross-layer changes in this repository.
---

# Develop Nexus Agent

1. Read the closest `AGENTS.md`, then open `docs/README.md` and
   `docs/architecture/overview.md`.
2. Trace the current source path before editing. For cross-layer work, verify the
   contract at both ends:
   - backend graph entrypoint and LangGraph stream shape;
   - `frontend/src/api/chat.js` parsing and `useChat` state transitions;
   - `backend/.deepagents/nexus.json` and frontend graph derivation when the harness
     topology changes.
3. Make the smallest coherent change. Preserve abort handling, stale-run guards,
   async resource lifecycles, least-privilege permissions, and immutable React state.
4. Add or update focused tests for changed behavior.
5. Verify from the affected package:
   - backend: `uv run python -m compileall source`;
   - frontend: `npm test` and `npm run build`.
6. Update `docs/architecture/overview.md` if entrypoints, ownership, runtime flow, or
   configuration locations changed.
7. Report changed files, checks and results, plus any integration check not run.

Do not read `.env`, generated runtime state, or credentials. Do not start services,
install packages, commit, or push unless the task requires it.
