---
name: maintain-agent-harness
description: Maintain the Nexus Deep Agents harness and its visual representation. Use when adding, removing, renaming, reviewing, or rewiring supervisors, subagents, project skills, permissions, model/tool integrations, nexus.json metadata, or LangGraph graph registrations.
---

# Maintain Agent Harness

1. Read `backend/AGENTS.md` and `docs/architecture/overview.md`.
2. Identify every surface affected by the change:
   - definition: `.deepagents/agents/<name>/AGENTS.md`;
   - skill: `.deepagents/skills/<name>/SKILL.md`;
   - assignment and UI metadata: `.deepagents/nexus.json`;
   - registration: `source/agents/sub_agents/__init__.py`;
   - graph entrypoint: `langgraph.json`;
   - UI topology/protocol assumptions: `frontend/src/data/` and
     `frontend/src/api/chat.js`.
3. Keep frontmatter names, config keys, filesystem paths, and frontend node IDs
   consistent. Prefer explicit validation over silent fallback.
4. Preserve least privilege:
   - researcher and reviewer remain read-only;
   - coder cannot edit `/.deepagents`;
   - supervisor writes only approved memory paths.
5. Update focused tests for config, tool-call, skill-path, or stream parsing changes.
6. Run backend compile verification plus frontend tests/build when shared harness
   contracts change.
7. Update architecture docs and report any live LangGraph check not run.

Never expose `.env` values or copy private memory/profile content into documentation.
Do not assume that changing only `.deepagents/nexus.json` creates a runnable subagent.
