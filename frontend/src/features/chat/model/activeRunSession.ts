export const ACTIVE_RUN_STORAGE_KEY = "nexus-active-run-v1";

export type ActiveRun = { threadId: string; runId: string; lastEventId: string | null };

export function readActiveRun(storage: Pick<Storage, "getItem"> = window.sessionStorage): ActiveRun | null {
  try {
    const value = JSON.parse(storage.getItem(ACTIVE_RUN_STORAGE_KEY) || "null");
    if (
      typeof value?.threadId !== "string"
      || !value.threadId
      || typeof value?.runId !== "string"
      || !value.runId
      || (value.lastEventId !== null && typeof value.lastEventId !== "string")
    ) return null;
    return value as ActiveRun;
  } catch {
    return null;
  }
}

export function writeActiveRun(
  threadId: string | null,
  runId: string | null,
  lastEventId: string | null = null,
  storage: Pick<Storage, "setItem"> = window.sessionStorage,
): ActiveRun | null {
  if (!threadId || !runId) return null;
  const value = { threadId, runId, lastEventId: lastEventId || null };
  storage.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify(value));
  return value;
}

export function clearActiveRun(
  runId: string | null = null,
  storage: Pick<Storage, "getItem" | "removeItem"> = window.sessionStorage,
): void {
  if (runId) {
    const current = readActiveRun(storage);
    if (current?.runId !== runId) return;
  }
  storage.removeItem(ACTIVE_RUN_STORAGE_KEY);
}
