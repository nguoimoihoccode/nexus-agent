import { useQuery } from "@tanstack/react-query";

import { checkBackendHealth } from "../api/chat.js";

const REFRESH_INTERVAL_MS = 30_000;
export type BackendHealthStatus = "checking" | "online" | "offline";

export function useBackendHealth(): { status: BackendHealthStatus; refresh: () => void } {
  const health = useQuery({
    queryKey: ["backend-health"],
    queryFn: ({ signal }) => checkBackendHealth(signal),
    refetchInterval: REFRESH_INTERVAL_MS,
    retry: false,
  });
  const status: BackendHealthStatus = health.isFetching
    ? "checking"
    : health.isSuccess
      ? "online"
      : "offline";
  return { status, refresh: () => { void health.refetch(); } };
}
