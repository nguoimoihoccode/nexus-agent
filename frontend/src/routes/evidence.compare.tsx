import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { isExperimentId } from "../api/governedQuant.js";
import { DossierPage } from "../features/dossier/DossierPage.js";

const comparisonSearch = z.object({
  experiments: z.preprocess(
    (value) => typeof value === "string" ? value.split(",") : value,
    z.array(z.string()).catch([]),
  ),
});

export const Route = createFileRoute("/evidence/compare")({
  validateSearch: comparisonSearch,
  component: ComparisonRoute,
});

function ComparisonRoute() {
  const search = Route.useSearch();
  const experimentIds = [...new Set(search.experiments.filter(isExperimentId))].slice(0, 5);
  return <DossierPage initialMode="comparison" initialComparisonIds={experimentIds} />;
}
