import assert from "node:assert/strict";
import { onTestFinished, test } from "vitest";

import {
  createAuthorizationLease,
  getAuthorizationLease,
  getUserMemory,
  revokeAuthorizationLease,
} from "./chatResources.js";
import { ChatApiError } from "./chatHttp.js";


test("user memory uses the product API instead of LangGraph Store", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({
      content: "durable preference",
      revision: 2,
      updated_at: "2026-07-15T00:00:00Z",
    }), { status: 200 });
  };

  const memory = await getUserMemory();

  assert.equal(memory.revision, 2);
  assert.equal(requests[0].url, "/api/v1/memory");
  assert.equal(requests[0].options.cache, "no-store");
  assert.equal(requests.length, 1);
  assert.equal(requests.some(({ url }) => url.includes("/store" + "/items")), false);
});

test("authorization lease APIs preserve actor-thread mode inputs", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  const requests = [];
  const lease = {
    lease_id: "azl_v1_" + "a".repeat(32),
    thread_id: "thread-permission",
    mode: "full_access",
    allow_sensitive: false,
    allowed_tools: ["prepare_qlib_dataset", "save_user_memory"],
    created_at: "2026-07-17T00:00:00Z",
    expires_at: "2026-07-17T01:00:00Z",
  };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (options.method === "DELETE") return new Response(null, { status: 204 });
    return new Response(JSON.stringify(lease), { status: 200 });
  };

  const current = await getAuthorizationLease("thread-permission");
  const created = await createAuthorizationLease({
    threadId: "thread-permission",
    mode: "full_access",
    ttlSeconds: 3600,
    allowSensitive: false,
  });
  await revokeAuthorizationLease(lease.lease_id);

  assert.equal(current.mode, "full_access");
  assert.deepEqual(created.allowed_tools, lease.allowed_tools);
  assert.equal(
    requests[0].url,
    "/api/v1/authorization-leases/current?thread_id=thread-permission",
  );
  assert.equal(requests[1].url, "/api/v1/authorization-leases");
  assert.equal(requests[1].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    thread_id: "thread-permission",
    mode: "full_access",
    ttl_seconds: 3600,
    allow_sensitive: false,
  });
  assert.equal(
    requests[2].url,
    `/api/v1/authorization-leases/${lease.lease_id}`,
  );
  assert.equal(requests[2].options.method, "DELETE");
});

test("chat resources reject oversized JSON responses before parsing", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response("{}", {
    status: 200,
    headers: { "content-length": String(8 * 1024 * 1024 + 1) },
  });

  await assert.rejects(
    getUserMemory(),
    (error) => error instanceof ChatApiError && error.status === 502,
  );
});
