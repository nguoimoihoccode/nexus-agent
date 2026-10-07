import { createRootRoute } from "@tanstack/react-router";

import App from "../App.js";
import { RouteFailure, RouteNotFound } from "../components/AsyncState.js";

export const Route = createRootRoute({
  component: App,
  errorComponent: ({ error, reset }) => (
    <RouteFailure error={error} onRetry={reset} />
  ),
  notFoundComponent: RouteNotFound,
});
