import assert from "node:assert/strict";
import { onTestFinished, test } from "vitest";

import {
  checkBackendHealth,
  createToolCallTracker,
  describeStreamEvent,
  isMissingThreadError,
  parseSseFrame,
  reconnectRun,
  skillFromToolCall,
  streamChat,
} from "./chat.js";
import { consumeChatStream } from "./chatProtocol.js";
import {
  MAX_ASSISTANT_MESSAGE_BYTES,
  MAX_SSE_FRAME_BYTES,
  MAX_SSE_STREAM_BYTES,
} from "../security/streamLimits.js";

test("reconnectRun resumes with Last-Event-ID and acknowledges replay", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response([
      "id: event-8",
      "event: messages-tuple",
      'data: [{"type":"ai","content":"Tiếp tục"},{}]',
      "",
      "",
    ].join("\n"), { status: 200 });
  };
  const tokens = [];
  const acknowledgements = [];

  const hasAnswer = await reconnectRun(
    "thread-1",
    "run-1",
    "event-7",
    {
      onToken: (token) => tokens.push(token),
      onEventId: (eventId) => acknowledgements.push(eventId),
    },
  );

  assert.equal(hasAnswer, true);
  assert.deepEqual(tokens, ["Tiếp tục"]);
  assert.deepEqual(acknowledgements, ["event-8"]);
  assert.match(request.url, /threads\/thread-1\/runs\/run-1\/stream/);
  assert.equal(new Headers(request.options.headers).get("Last-Event-ID"), "event-7");
});

test("checkBackendHealth reports the proxied LangGraph health endpoint", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  assert.equal(await checkBackendHealth(), true);
  assert.equal(request.url, "/api/ok");
  assert.equal(request.options.cache, "no-store");
});

test("checkBackendHealth rejects unavailable backends with status context", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "not ready" }), { status: 503 });

  await assert.rejects(
    () => checkBackendHealth(),
    (error) => error.status === 503 && error.message === "not ready",
  );
});

test("parseSseFrame parses a messages tuple frame", () => {
  const frame = [
    "event: messages|tools:abc",
    'data: [{"type":"AIMessageChunk"},{"lc_agent_name":"researcher"}]',
  ].join("\n");

  assert.deepEqual(parseSseFrame(frame), {
    eventType: "messages|tools:abc",
    eventId: null,
    payload: [{ type: "AIMessageChunk" }, { lc_agent_name: "researcher" }],
  });
});

test("parseSseFrame ignores done and empty frames", () => {
  assert.equal(parseSseFrame("event: end\ndata: [DONE]"), null);
  assert.equal(parseSseFrame(": keep-alive"), null);
});

test("parseSseFrame rejects malformed JSON payloads", () => {
  assert.throws(() => parseSseFrame("event: messages-tuple\ndata: {not-json}"), SyntaxError);
});

test("consumeChatStream aborts an oversized unterminated SSE frame", async () => {
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${"x".repeat(MAX_SSE_FRAME_BYTES)}`));
    },
    cancel() {
      cancelled = true;
    },
  });

  await assert.rejects(
    () => consumeChatStream(stream.getReader(), {
      onToken: () => {},
      onProgress: () => {},
    }),
    /giới hạn 1 MiB/,
  );
  assert.equal(cancelled, true);
});

test("consumeChatStream aborts a stream that exceeds its total byte budget", async () => {
  let cancelled = false;
  const frame = new TextEncoder().encode(
    `:${"x".repeat(MAX_SSE_FRAME_BYTES - 64)}\ndata: [DONE]\n\n`,
  );
  assert.ok(frame.byteLength < MAX_SSE_FRAME_BYTES);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let total = 0; total <= MAX_SSE_STREAM_BYTES; total += frame.byteLength) {
        controller.enqueue(frame);
      }
    },
    cancel() {
      cancelled = true;
    },
  });

  await assert.rejects(
    () => consumeChatStream(stream.getReader(), { onToken: () => {}, onProgress: () => {} }),
    new RegExp(`giới hạn ${MAX_SSE_STREAM_BYTES / (1024 * 1024)} MiB`),
  );
  assert.equal(cancelled, true);
});

test("consumeChatStream aborts an oversized assistant message across valid frames", async () => {
  let cancelled = false;
  const content = "x".repeat(Math.floor(MAX_ASSISTANT_MESSAGE_BYTES * 0.6));
  const frame = (id: string) => new TextEncoder().encode([
    `id: ${id}`,
    "event: messages-tuple",
    `data: ${JSON.stringify([{ type: "ai", content }, {}])}`,
    "",
    "",
  ].join("\n"));
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(frame("event-1"));
      controller.enqueue(frame("event-2"));
    },
    cancel() {
      cancelled = true;
    },
  });

  await assert.rejects(
    () => consumeChatStream(stream.getReader(), { onToken: () => {}, onProgress: () => {} }),
    /Câu trả lời.*1 MiB/,
  );
  assert.equal(cancelled, true);
});

test("describeStreamEvent separates mode from subgraph namespace", () => {
  assert.deepEqual(describeStreamEvent("updates|tools:abc|researcher:def"), {
    mode: "updates",
    namespace: ["tools:abc", "researcher:def"],
  });
});

test("createToolCallTracker waits for streamed task arguments and identifies the subagent", () => {
  const tracker = createToolCallTracker();
  const metadata = { langgraph_step: 2 };

  assert.deepEqual(tracker.ingest({
    tool_call_chunks: [{ index: 0, id: "call-task", name: "task", args: "{\"subagent_" }],
  }, metadata), []);

  assert.deepEqual(tracker.ingest({
    tool_call_chunks: [{ index: 0, id: "call-task", name: null, args: "type\":\"researcher\"," }],
  }, metadata), []);

  assert.deepEqual(tracker.ingest({
    tool_call_chunks: [{ index: 0, id: "call-task", name: null, args: "\"description\":\"test\"}" }],
  }, metadata), [{
    id: "call-task",
    name: "task",
    args: { subagent_type: "researcher", description: "test" },
    delegatedAgent: "researcher",
  }]);
});

test("createToolCallTracker identifies quant researcher delegation", () => {
  const tracker = createToolCallTracker();

  assert.deepEqual(tracker.ingest({
    tool_calls: [{
      id: "call-quant",
      name: "task",
      args: { subagent_type: "quant-researcher", description: "Analyze CSI300" },
    }],
  }, { langgraph_step: 2 }), [{
    id: "call-quant",
    name: "task",
    args: { subagent_type: "quant-researcher", description: "Analyze CSI300" },
    delegatedAgent: "quant-researcher",
  }]);
});

test("createToolCallTracker identifies quant data delegation", () => {
  const tracker = createToolCallTracker();

  assert.deepEqual(tracker.ingest({
    tool_calls: [{
      id: "call-data",
      name: "task",
      args: { subagent_type: "quant-data-agent", description: "Prepare dataset" },
    }],
  }, { langgraph_step: 2 }), [{
    id: "call-data",
    name: "task",
    args: { subagent_type: "quant-data-agent", description: "Prepare dataset" },
    delegatedAgent: "quant-data-agent",
  }]);
});

test("createToolCallTracker identifies AI-Trader delegation", () => {
  const tracker = createToolCallTracker();

  assert.deepEqual(tracker.ingest({
    tool_calls: [{
      id: "call-ai-trader",
      name: "task",
      args: { subagent_type: "ai-trader-agent", description: "Read signal feed" },
    }],
  }, { langgraph_step: 2 }), [{
    id: "call-ai-trader",
    name: "task",
    args: { subagent_type: "ai-trader-agent", description: "Read signal feed" },
    delegatedAgent: "ai-trader-agent",
  }]);
});

test("createToolCallTracker emits each logical tool call once", () => {
  const tracker = createToolCallTracker();
  const message = {
    tool_calls: [{ id: "call-search", name: "web_search", args: { query: "survivorship bias factor backtest" } }],
  };

  assert.equal(tracker.ingest(message, { langgraph_step: 3 }).length, 1);
  assert.equal(tracker.ingest(message, { langgraph_step: 3 }).length, 0);
});

test("createToolCallTracker keeps calls from different subgraph scopes separate", () => {
  const tracker = createToolCallTracker();
  const message = {
    tool_calls: [{ id: "call-read", name: "read_file", args: { file_path: "/tmp/file" } }],
  };

  assert.equal(tracker.ingest(message, {
    langgraph_step: 3,
    langgraph_checkpoint_ns: "model:supervisor",
  }).length, 1);
  assert.equal(tracker.ingest(message, {
    langgraph_step: 3,
    langgraph_checkpoint_ns: "tools:task|model:researcher",
  }).length, 1);
});

test("createToolCallTracker returns every ready call in one message", () => {
  const tracker = createToolCallTracker();
  const calls = tracker.ingest({
    tool_calls: [
      { id: "call-search", name: "web_search", args: { query: "factor backtest data leakage" } },
      {
        id: "call-skill",
        name: "read_file",
        args: { file_path: "/.deepagents/skills/research/SKILL.md" },
      },
    ],
  }, { langgraph_step: 4, langgraph_checkpoint_ns: "tools:task|model:researcher" });

  assert.deepEqual(calls.map((call) => call.name), ["web_search", "read_file"]);
});

test("skillFromToolCall detects an explicit SKILL.md read", () => {
  assert.equal(skillFromToolCall("read_file", {
    file_path: "/.deepagents/skills/research/SKILL.md",
  }), "research");
  assert.equal(skillFromToolCall("read_file", {
    path: ".deepagents/skills/docs-audit/SKILL.md",
  }), "docs-audit");
  assert.equal(skillFromToolCall("read_file", { path: "/workspace/README.md" }), null);
  assert.equal(skillFromToolCall("save_user_memory", {
    path: "/.deepagents/skills/research/SKILL.md",
  }), null);
});

test("createToolCallTracker waits for streamed read_file arguments", () => {
  const tracker = createToolCallTracker();
  const metadata = { langgraph_step: 4 };

  assert.deepEqual(tracker.ingest({
    tool_call_chunks: [{ index: 0, id: "call-skill", name: "read_file", args: "{\"file_path\":" }],
  }, metadata), []);

  assert.deepEqual(tracker.ingest({
    tool_call_chunks: [{ index: 0, id: "call-skill", name: null, args: "\"/.deepagents/skills/research/SKILL.md\"}" }],
  }, metadata), [{
    id: "call-skill",
    name: "read_file",
    args: { file_path: "/.deepagents/skills/research/SKILL.md" },
    delegatedAgent: null,
  }]);
});

test("streamChat replaces a stale in-memory thread and retries once", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  const requestedUrls = [];
  globalThis.fetch = async (url) => {
    requestedUrls.push(url);
    if (url.endsWith("/threads/stale/runs/stream")) {
      return new Response(JSON.stringify({ detail: "Thread or assistant not found" }), {
        status: 404,
      });
    }
    if (url.endsWith("/threads")) {
      return new Response(JSON.stringify({ thread_id: "fresh" }), { status: 200 });
    }
    return new Response([
      "event: messages-tuple",
      'data: [{"type":"ai","content":"Xin chào"},{}]',
      "",
      "",
    ].join("\n"), { status: 200 });
  };

  const tokens = [];
  let resetThreadId = null;
  const hasAnswer = await streamChat(
    "stale",
    "hello",
    (token) => tokens.push(token),
    () => {},
    undefined,
    (threadId) => {
      resetThreadId = threadId;
    },
  );

  assert.equal(hasAnswer, true);
  assert.deepEqual(tokens, ["Xin chào"]);
  assert.equal(resetThreadId, "fresh");
  assert.deepEqual(requestedUrls, [
    "/api/threads/stale/runs/stream",
    "/api/threads",
    "/api/threads/fresh/runs/stream",
  ]);
  assert.equal(isMissingThreadError({ status: 500, message: "Thread or assistant not found" }), false);
});

test("streamChat propagates backend error frames", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response([
    "event: error",
    'data: {"message":"backend denied request"}',
    "",
    "",
  ].join("\n"), { status: 200 });

  await assert.rejects(
    () => streamChat("thread", "hello", () => {}, () => {}),
    /backend denied request/,
  );
});

test("streamChat emits completed tool activity from graph updates", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response([
    "event: updates|tools:abc|researcher:def",
    'data: {"tools":{"messages":[{"type":"tool","name":"web_search","tool_call_id":"call-search"}]}}',
    "",
    "",
  ].join("\n"), { status: 200 });

  const activities = [];
  await streamChat("thread", "hello", () => {}, (label, activity) => {
    activities.push({ label, activity });
  });

  assert.equal(activities[0].label, "Công cụ web_search đã hoàn tất");
  assert.equal(activities[0].activity.kind, "tool_result");
  assert.equal(activities[0].activity.agentName, "researcher");
  assert.equal(activities[0].activity.toolCallId, "call-search");
});

test("streamChat coalesces ten thousand chunks into model lifecycle phases", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  const metadata = { lc_agent_name: "researcher", langgraph_step: 4 };
  const frame = (message) => [
    "event: messages|tools:researcher-run",
    `data: ${JSON.stringify([message, metadata])}`,
    "",
    "",
  ].join("\n");
  const frames = [frame({ type: "AIMessageChunk", id: "model-call-1", content: "" })];
  for (let index = 0; index < 4_999; index += 1) {
    frames.push(frame({
      type: "AIMessageChunk",
      id: "model-call-1",
      content: "",
      additional_kwargs: { reasoning_content: "r" },
    }));
  }
  for (let index = 0; index < 5_000; index += 1) {
    frames.push(frame({ type: "AIMessageChunk", id: "model-call-1", content: "x" }));
  }
  globalThis.fetch = async () => new Response(frames.join(""), { status: 200 });

  const activities = [];
  const hasAnswer = await streamChat("thread", "research", () => {}, (label, activity) => {
    activities.push({ label, activity });
  });

  assert.equal(hasAnswer, false);
  assert.deepEqual(activities.map(({ activity }) => activity.kind), [
    "model_stream",
    "model_stream",
    "model_stream",
  ]);
  assert.deepEqual(activities.map(({ activity }) => activity.phase), [
    "streaming",
    "reasoning",
    "content",
  ]);
  assert.ok(activities.every(({ activity }) => activity.modelCallId === "model-call-1"));
});

test("streamChat surfaces a native approval interrupt", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response([
    "event: updates",
    'data: {"__interrupt__":[{"value":{"action_requests":[{"name":"publish_ai_trader_strategy","args":{"title":"Exact proposal"}}]}}]}',
    "",
    "",
  ].join("\n"), { status: 200 });

  const interrupts = [];
  const hasAnswer = await streamChat(
    "thread",
    "publish",
    () => {},
    () => {},
    undefined,
    undefined,
    (interrupt, activity) => interrupts.push({ interrupt, activity }),
  );

  assert.equal(hasAnswer, false);
  assert.equal(interrupts[0].interrupt.action_requests[0].name, "publish_ai_trader_strategy");
  assert.equal(interrupts[0].interrupt.action_requests[0].args.title, "Exact proposal");
  assert.equal(interrupts[0].activity.agentName, "ai-trader-agent");
});

test("streamChat attributes memory approval interrupts to supervisor", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response([
    "event: updates",
    'data: {"__interrupt__":[{"value":{"action_requests":[{"name":"save_user_memory","args":{"memory_key":"preference.response_style","content":"bounded"}}]}}]}',
    "",
    "",
  ].join("\n"), { status: 200 });

  const interrupts = [];
  await streamChat(
    "thread",
    "remember",
    () => {},
    () => {},
    undefined,
    undefined,
    (approval, activity) => interrupts.push({ approval, activity }),
  );

  assert.equal(interrupts[0].approval.action_requests[0].name, "save_user_memory");
  assert.equal(
    interrupts[0].approval.action_requests[0].args.memory_key,
    "preference.response_style",
  );
  assert.equal(interrupts[0].activity.agentName, "supervisor");
});

test("streamChat preserves exact quant approval arguments and agent ownership", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response([
    "event: updates",
    'data: {"__interrupt__":[{"value":{"action_requests":[{"name":"run_governed_qlib_experiment","args":{"dataset_revision_id":"dsr_v1_exact","model":"lightgbm"}}]}}]}',
    "",
    "",
  ].join("\n"), { status: 200 });

  const interrupts = [];
  await streamChat(
    "thread",
    "run experiment",
    () => {},
    () => {},
    undefined,
    undefined,
    (approval, activity) => interrupts.push({ approval, activity }),
  );

  assert.equal(
    interrupts[0].approval.action_requests[0].args.dataset_revision_id,
    "dsr_v1_exact",
  );
  assert.equal(interrupts[0].approval.action_requests[0].args.model, "lightgbm");
  assert.equal(interrupts[0].activity.agentName, "quant-researcher");
});

test("streamChat ignores malformed non-error frames and keeps reading", async () => {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response([
    "event: messages-tuple",
    "data: {not-json}",
    "",
    "event: messages-tuple",
    'data: [{"type":"ai","content":"Recovered"},{}]',
    "",
    "",
  ].join("\n"), { status: 200 });

  const tokens = [];
  const hasAnswer = await streamChat("thread", "hello", (token) => tokens.push(token), () => {});

  assert.equal(hasAnswer, true);
  assert.deepEqual(tokens, ["Recovered"]);
});
