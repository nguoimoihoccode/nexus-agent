import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { ExperimentCatalogPage } from "../features/dossier/ExperimentCatalogPage.js";

const catalogSearch = z.object({
  status: z.enum(["all", "active", "completed", "failed"]).catch("all"),
});

export const Route = createFileRoute("/evidence/")({
  validateSearch: catalogSearch,
  component: ExperimentCatalogRoute,
});

function ExperimentCatalogRoute() {
  const { status } = Route.useSearch();
  return <ExperimentCatalogPage filter={status} />;
}
