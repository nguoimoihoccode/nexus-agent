import assert from "node:assert/strict";
import { test } from "vitest";

import {
  MAX_LIVE_STEPS,
  chatReducer,
  createInitialChatState,
  recordProgress,
} from "./chat/model/chatState.js";
import { projectActivity } from "./chat/model/liveGraph.js";

test("chat state machine moves a prompt through streaming and completion", () => {
  const initial = createInitialChatState();
  const started = chatReducer(initial, { type: "run_started", prompt: "Phân tích CSI300" });
  const streamed = chatReducer(started, { type: "token_received", token: "Kết quả", replace: true });
  const completed = chatReducer(streamed, { type: "run_completed", hasAnswer: true, at: 3 });
  const finished = chatReducer(completed, { type: "run_finished" });

  assert.equal(initial.messages.length, 0);
  assert.equal(started.runOutcome, "running");
  assert.equal(streamed.messages.at(-1).content, "Kết quả");
  assert.equal(completed.runOutcome, "complete");
  assert.equal(finished.chatting, false);
  assert.equal(finished.chatProgress.at(-1).label, "Hoàn tất");
});

test("progress closes the matching logical tool call", () => {
  const called = recordProgress([], "Đang gọi web_search", {
    kind: "tool_call", toolName: "web_search", toolCallId: "call-1",
  }, 2);
  const completed = recordProgress(called, "Công cụ web_search đã hoàn tất", {
    kind: "tool_result", toolName: "web_search", toolCallId: "call-1",
  }, 5);

  assert.equal(completed.length, 1);
  assert.equal(completed[0].status, "complete");
  assert.equal(completed[0].duration, 3);
});

test("approval and clear are explicit state transitions", () => {
  const started = chatReducer(createInitialChatState(), { type: "run_started", prompt: "Publish" });
  const waiting = chatReducer(started, {
    type: "approval_requested",
    approval: {
      action_requests: [{ name: "publish", args: {}, supported: false }],
      status: "unsupported",
      blocked_reason: "unsupported",
    },
    agentName: "ai-trader-agent",
    at: 1,
  });
  const cleared = chatReducer(waiting, { type: "cleared" });

  assert.equal(waiting.runOutcome, "awaiting_approval");
  assert.equal(waiting.pendingApproval.action_requests[0].name, "publish");
  assert.equal(waiting.chatProgress[0].status, "complete");
  assert.equal(waiting.chatProgress.at(-1).status, "waiting");
  assert.equal(waiting.chatProgress.at(-1).agentName, "ai-trader-agent");
  assert.deepEqual(cleared, createInitialChatState());
});

test("approval completion clears waiting progress and preserves its owning agent", () => {
  const started = chatReducer(createInitialChatState(), {
    type: "run_started",
    prompt: "Lưu tên vào memory",
  });
  const waiting = chatReducer(started, {
    type: "approval_requested",
    approval: {
      action_requests: [{ name: "save_user_memory", args: {}, supported: true }],
      status: "verified",
      blocked_reason: null,
    },
    agentName: "supervisor",
    at: 2,
  });
  const resumed = chatReducer(waiting, {
    type: "approval_started",
    decision: "approve",
    at: 3,
  });
  const completed = chatReducer(resumed, {
    type: "run_completed",
    hasAnswer: false,
    emptyMessage: "Hành động đã được xử lý.",
    at: 4,
    addCompletion: false,
  });

  assert.equal(resumed.pendingApproval, null);
  assert.equal(resumed.chatProgress.at(-1).status, "running");
  assert.equal(resumed.chatProgress.at(-1).agentName, "supervisor");
  assert.equal(completed.chatProgress.some((item) => item.status === "waiting"), false);
  assert.equal(completed.chatProgress.at(-1).label, "Hành động đã được xử lý.");
  assert.equal(completed.chatProgress.at(-1).status, "complete");
  assert.equal(completed.chatProgress.at(-1).agentName, "supervisor");
});

test("live graph projection is pure and maps worker activity to its resource", () => {
  const projection = projectActivity("Đang gọi công cụ run_experiment", {
    kind: "tool_call",
    agentName: "quant-researcher",
    toolName: "run_experiment",
  }, {
    agents: { "quant-researcher": { skills: ["quant-research"] } },
  });

  assert.equal(projection.activeSkillNode, "quant-research-skill");
  assert.deepEqual(projection.steps.at(-1).slice(0, 2), [
    "qlib-worker",
    "quant-researcher-qlib-worker",
  ]);
});

test("subagent model stream projects one stable graph transition", () => {
  const projection = projectActivity("Researcher đang viết báo cáo", {
    kind: "model_stream",
    agentName: "researcher",
    phase: "content",
    modelCallId: "model-call-1",
  }, {
    agents: { researcher: { skills: ["research"] } },
  });

  assert.deepEqual(projection.steps.map((step) => step.slice(0, 2)), [
    ["model", "researcher-model"],
  ]);
});

test("duplicate progress is a reducer no-op", () => {
  const initial = createInitialChatState();
  const action = {
    type: "progress_recorded",
    label: "Researcher đang viết báo cáo",
    activity: { kind: "model_stream", agentName: "researcher" },
    at: 1,
  };
  const first = chatReducer(initial, action);
  const duplicate = chatReducer(first, action);

  assert.strictEqual(duplicate, first);
});

test("live graph history is bounded while preserving initial request steps", () => {
  const started = chatReducer(createInitialChatState(), {
    type: "run_started",
    prompt: "Stress graph",
  });
  const initialSteps = started.liveSteps.map((step) => [...step]);
  let state = started;
  for (let index = 0; index < MAX_LIVE_STEPS * 2; index += 1) {
    state = chatReducer(state, {
      type: "live_step_added",
      step: [`node-${index % 2}`, `edge-${index % 2}`, `Step ${index}`],
    });
  }

  assert.equal(state.liveSteps.length, MAX_LIVE_STEPS);
  assert.deepEqual(state.liveSteps.slice(0, initialSteps.length), initialSteps);
  assert.equal(state.liveSteps.at(-1)[2], `Step ${MAX_LIVE_STEPS * 2 - 1}`);
});
