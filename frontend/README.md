# Nexus Agent Console

TypeScript/React console for authenticated chat, live workflow inspection, experiment
evidence, agent topology, and user settings. React 19 and Vite provide the runtime;
TanStack Router owns URL state, TanStack Query owns server state, Zod validates product
API boundaries, and Radix supplies accessible interaction primitives.

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite server proxies `/api` to the backend configured
by `VITE_API_PROXY_TARGET` (default `http://127.0.0.1:2024`). Runtime topology is read
from the authenticated `/v1/topology` API; the browser does not edit harness files.

## Routes

- `/chat` keeps the live chat/session shell mounted while navigating.
- `/evidence/:experimentId` is a reload-safe dossier URL.
- `/evidence/compare?experiments=...` stores comparison selection in the URL.
- `/agents/:agentId` opens a directly addressable agent card.
- `/settings` exposes backend, runtime, thread, memory, and OIDC controls.

## Quality gates

```bash
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Route generation is part of type-check, test, and build scripts. Do not edit
`src/routeTree.gen.ts` manually.
