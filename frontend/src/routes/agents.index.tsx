import { createFileRoute, useNavigate } from "@tanstack/react-router";

import { useWorkspace } from "../App.js";
import { AgentsPage } from "../features/console/pages.js";

export const Route = createFileRoute("/agents/")({
  component: AgentsRoute,
});

function AgentsRoute() {
  const navigate = useNavigate();
  const workspace = useWorkspace();
  return (
    <AgentsPage
      rows={workspace.agentRows}
      topology={{ isPending: workspace.topology.isPending, isError: workspace.topology.isError, error: workspace.topology.error, refresh: () => { void workspace.topology.refetch(); } }}
      onInspect={(agentId) => {
        workspace.selectNode(agentId);
        void navigate({ to: "/agents/$agentId", params: { agentId } });
      }}
    />
  );
}
