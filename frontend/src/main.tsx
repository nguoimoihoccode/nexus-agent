import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";

import { AuthGate } from "./auth/AuthGate.js";
import { routeTree } from "./routeTree.gen.js";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles.css";

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  scrollRestoration: true,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

function SessionApp() {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  }));
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Nexus root element is missing.");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <AuthGate>{(sessionKey) => <SessionApp key={sessionKey} />}</AuthGate>
  </React.StrictMode>,
);
