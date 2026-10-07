import { type RefObject, useEffect, useState } from "react";

import {
  createAuthorizationLease,
  getAuthorizationLease,
  revokeAuthorizationLease,
} from "../api/chat.js";
import type {
  AuthorizationLease,
  PermissionMode,
} from "../types/contracts.js";

function safeLease(threadId = ""): AuthorizationLease {
  return {
    lease_id: null,
    thread_id: threadId,
    mode: "safe",
    allow_sensitive: false,
    allowed_tools: [],
    created_at: null,
    expires_at: null,
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Lỗi không xác định.";
}

export function useAuthorizationLease({
  threadRef,
  ensureThread,
  isBlocked,
}: {
  threadRef: RefObject<string | null>;
  ensureThread: () => Promise<string>;
  isBlocked: () => boolean;
}) {
  const [permissionLease, setPermissionLease] = useState<AuthorizationLease>(() => (
    safeLease(threadRef.current ?? "")
  ));
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [permissionError, setPermissionError] = useState("");

  useEffect(() => {
    const threadId = threadRef.current;
    if (!threadId) return undefined;
    let disposed = false;
    getAuthorizationLease(threadId)
      .then((lease) => {
        if (!disposed) setPermissionLease(lease);
      })
      .catch((error) => {
        if (!disposed) {
          setPermissionLease(safeLease(threadId));
          setPermissionError(`Không thể tải permission mode: ${errorMessage(error)}`);
        }
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (!permissionLease.expires_at) return undefined;
    const remaining = new Date(permissionLease.expires_at).getTime() - Date.now();
    if (remaining <= 0) {
      setPermissionLease(safeLease(permissionLease.thread_id));
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      setPermissionLease(safeLease(permissionLease.thread_id));
    }, Math.min(remaining, 2_147_000_000));
    return () => window.clearTimeout(timeout);
  }, [permissionLease]);

  const resetPermission = (threadId = "", revoke = false) => {
    const leaseId = permissionLease.lease_id;
    setPermissionLease(safeLease(threadId));
    setPermissionError("");
    if (revoke && leaseId) revokeAuthorizationLease(leaseId).catch(() => {});
  };

  const setPermissionMode = async (
    mode: PermissionMode,
    options: { ttlSeconds: number; allowSensitive: boolean },
  ) => {
    if (permissionBusy || isBlocked()) return;
    setPermissionBusy(true);
    setPermissionError("");
    try {
      if (mode === "safe") {
        if (permissionLease.lease_id) {
          await revokeAuthorizationLease(permissionLease.lease_id);
        }
        setPermissionLease(safeLease(threadRef.current ?? ""));
        return;
      }
      const threadId = await ensureThread();
      const lease = await createAuthorizationLease({
        threadId,
        mode,
        ttlSeconds: options.ttlSeconds,
        allowSensitive: mode === "full_access" && options.allowSensitive,
      });
      setPermissionLease(lease);
    } catch (error) {
      setPermissionError(`Không thể đổi permission mode: ${errorMessage(error)}`);
    } finally {
      setPermissionBusy(false);
    }
  };

  return {
    permissionLease,
    permissionBusy,
    permissionError,
    resetPermission,
    setPermissionMode,
  };
}
