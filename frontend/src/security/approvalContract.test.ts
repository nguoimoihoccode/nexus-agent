import assert from "node:assert/strict";
import { test } from "vitest";

import {
  approvalSummary,
  canApprove,
  canReject,
  parseApprovalRequest,
} from "./approvalContract.js";

test("normalizes a supported approval and exposes a bounded business summary", () => {
  const approval = parseApprovalRequest({
    action_requests: [{
      name: "run_governed_qlib_experiment",
      args: { dataset_revision_id: "dsr_v1_exact", model: "lightgbm" },
    }],
  });

  assert.equal(approval.status, "verified");
  assert.equal(canApprove(approval), true);
  assert.equal(canReject(approval), true);
  assert.deepEqual(approvalSummary(approval.action_requests[0]), [
    { label: "Dataset", value: "dsr_v1_exact" },
    { label: "Model", value: "lightgbm" },
  ]);
});

test("unknown actions can be rejected but never approved", () => {
  const approval = parseApprovalRequest({
    actionRequests: [{ action: "future_effect", arguments: { target: "external" } }],
  });

  assert.equal(approval.status, "unsupported");
  assert.equal(canApprove(approval), false);
  assert.equal(canReject(approval), true);
  assert.match(approval.blocked_reason || "", /future_effect/);
});

test("empty, deeply nested, oversized and excessive approvals fail closed", () => {
  const nested = { value: { value: { value: { value: { value: { value: { value: true } } } } } } };
  const values = [
    {},
    { action_requests: [] },
    { action_requests: [{ name: "save_user_memory", args: nested }] },
    { action_requests: [{ name: "save_user_memory", args: { content: "x".repeat(17 * 1024) } }] },
    { action_requests: Array.from({ length: 9 }, () => ({ name: "delete_user_memory", args: {} })) },
  ];

  values.forEach((value) => {
    const approval = parseApprovalRequest(value);
    assert.equal(approval.status, "malformed");
    assert.equal(canApprove(approval), false);
    assert.equal(canReject(approval), false);
  });
});
