# Frontend Agent Instructions

## Scope

These instructions apply to `frontend/`. The root `AGENTS.md` also applies.

## Architecture and conventions

- Start with `../docs/architecture/overview.md`.
- Preserve the existing React function-component and custom-hook style.
- Use immutable React state updates and keep side effects in hooks or explicit event
  handlers.
- Keep LangGraph HTTP/SSE protocol code in `src/api/`; keep graph derivation in
  `src/data/`; keep reusable UI behavior in `src/hooks/`.
- Treat streamed events and fetched JSON as untrusted input. Preserve abort handling
  and stale-run guards when changing chat behavior.
- Load topology only from the authenticated `/v1/topology` product API. Keep the
  browser fail-closed when that endpoint is unavailable.

## Verification

- From `frontend/`, run `npm test` for protocol/parser changes.
- Run `npm run build` for any frontend change.
- Manually verify live chat only when the backend and required services are
  available; report this separately from automated checks.
