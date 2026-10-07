# Repository Agent Instructions

## Context first

- Read [docs/README.md](docs/README.md) before broad repository work.
- Use the closest `AGENTS.md` for the files being changed:
  - `backend/AGENTS.md` for Python, LangGraph, and `.deepagents` work.
  - `frontend/AGENTS.md` for React, Vite, and browser integration work.
- Inspect only the relevant codemap and source files; documentation is a navigation
  aid, while source code remains authoritative.
- Keep `docs/architecture/overview.md` synchronized when entrypoints, module
  ownership, or runtime flows change.

## Working principles

- Prefer the smallest coherent change; do not refactor unrelated code.
- Validate external input at boundaries and handle errors explicitly.
- Never read, print, commit, or expose `.env` values, tokens, credentials, or local
  runtime state.
- Preserve user changes in a dirty worktree.
- Use immutable updates where practical, especially for React state and shared data.
- Add or update focused tests for behavior changes. Do not invent a coverage target
  that the repository does not enforce.
- Run the narrowest relevant checks first, then the broader quality gate described in
  [docs/guides/development.md](docs/guides/development.md).
- Review `git diff` before handing work off. After a requested change is complete and
  its relevant checks pass, commit the intended files and push directly to `main`
  unless the user explicitly asks not to publish it.

## Documentation

- Put durable architecture and workflow knowledge under `docs/`.
- Update existing pages instead of creating overlapping documents.
- Use relative Markdown links and verify that renamed paths remain navigable from
  `docs/README.md`.
- Do not duplicate secrets, personal memory, or the full contents of runtime prompts
  in documentation.

## Simple push workflow for agents

- Check `git status --short` and review the diff before staging.
- Stage only the intended files with explicit paths.
- Commit with a short message that describes the change.
- Push with plain Git over the HTTPS remote, for example `git push origin <branch>`.
- Do not use `gh` for normal push-only requests.
- If authentication fails, reuse VS Code's `GIT_ASKPASS` and `VSCODE_GIT_*`
  environment from an active integrated terminal, but never print token values.
