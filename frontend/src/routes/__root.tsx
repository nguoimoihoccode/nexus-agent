import { createRootRoute } from "@tanstack/react-router";

import App from "../App.js";
import { RouteFailure, RouteNotFound } from "../components/AsyncState.js";

// errorComponent receives whatever the route threw. @tanstack/react-router
// types that as `unknown` since 1.170.41, so narrow it here rather than
// widening RouteFailure's `error: Error` contract.
function asError(thrown: unknown): Error {
  return thrown instanceof Error ? thrown : new Error("Lỗi không xác định.");
}

export const Route = createRootRoute({
  component: App,
  errorComponent: ({ error, reset }) => (
    <RouteFailure error={asError(error)} onRetry={reset} />
  ),
  notFoundComponent: RouteNotFound,
});
