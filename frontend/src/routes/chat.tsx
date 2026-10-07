import { createFileRoute } from "@tanstack/react-router";

import { useWorkspace } from "../App.js";
import { ChatPage } from "../features/console/pages.js";

export const Route = createFileRoute("/chat")({
  component: ChatRoute,
});

function ChatRoute() {
  const workspace = useWorkspace();
  return (
    <ChatPage
      chat={workspace.chat}
      current={workspace.current}
      graph={workspace.graph}
      nodes={workspace.nodes}
      selected={workspace.selected}
      activeNode={workspace.activeNode}
      visitedNodes={workspace.visitedNodes}
      expandedAgents={workspace.expandedAgents}
      onToggleAgentDetails={workspace.toggleAgentDetails}
      onCloseNodeDetail={() => workspace.setSelected(null)}
      onSelectNode={workspace.selectNode}
    />
  );
}
