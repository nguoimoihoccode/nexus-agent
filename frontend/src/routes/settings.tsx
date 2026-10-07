import { createFileRoute } from "@tanstack/react-router";

import { useWorkspace } from "../App.js";
import { SettingsPage } from "../features/console/SettingsPage.js";

export const Route = createFileRoute("/settings")({
  component: SettingsRoute,
});

function SettingsRoute() {
  const workspace = useWorkspace();
  return (
    <SettingsPage
      enabledAgents={workspace.enabledAgents}
      skillCount={workspace.skillCount}
      chat={workspace.chat}
      backendHealth={workspace.backendHealth}
      topology={{ isPending: workspace.topology.isPending, isError: workspace.topology.isError, error: workspace.topology.error, refresh: () => { void workspace.topology.refetch(); } }}
    />
  );
}
