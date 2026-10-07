import { createFileRoute, notFound, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

import { useWorkspace } from "../App.js";
import { AgentsPage } from "../features/console/pages.js";

export const Route = createFileRoute("/agents/$agentId")({
  component: AgentDetailRoute,
});

function AgentDetailRoute() {
  const { agentId } = Route.useParams();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const exists = workspace.agentRows.some((agent) => agent.id === agentId);

  useEffect(() => {
    if (exists) workspace.selectNode(agentId);
  }, [agentId, exists, workspace.selectNode]);

  if (!exists) throw notFound();
  return (
    <AgentsPage
      rows={workspace.agentRows}
      focusedAgentId={agentId}
      topology={{ isPending: workspace.topology.isPending, isError: workspace.topology.isError, error: workspace.topology.error, refresh: () => { void workspace.topology.refetch(); } }}
      onInspect={(nextAgentId) => {
        workspace.selectNode(nextAgentId);
        void navigate({ to: "/agents/$agentId", params: { agentId: nextAgentId } });
      }}
    />
  );
}
