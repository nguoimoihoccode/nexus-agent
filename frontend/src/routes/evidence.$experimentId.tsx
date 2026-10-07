import { createFileRoute, notFound } from "@tanstack/react-router";

import { isExperimentId } from "../api/governedQuant.js";
import { DossierPage } from "../features/dossier/DossierPage.js";

export const Route = createFileRoute("/evidence/$experimentId")({
  beforeLoad: ({ params }) => {
    if (!isExperimentId(params.experimentId)) throw notFound();
  },
  component: ExperimentRoute,
});

function ExperimentRoute() {
  const { experimentId } = Route.useParams();
  return <DossierPage initialExperimentId={experimentId} />;
}
