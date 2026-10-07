import type { LucideIcon } from "lucide-react";

import type { GraphNode } from "../../../data/types.js";

export type PresentedGraphNode = Omit<GraphNode, "icon"> & { icon: LucideIcon };
