// Ceiling for a single SSE frame. This is the one that bounds one hostile
// payload, so it stays tight: a frame above 1 MiB aborts the stream.
export const MAX_SSE_FRAME_BYTES = 1024 * 1024;

// Ceiling for one connection, raised from 8 MiB. A stream that crossed it was
// not a runaway: the supervisor runs five agents under per-agent model- and
// tool-call limits (backend/source/core/config.py), `updates` is streamed for
// every subgraph node, and each token arrives as its own event carrying the
// run metadata again. A long but entirely legitimate run therefore crossed
// 8 MiB and the browser discarded a completed answer. Those same call limits
// are what keep the stream bounded, so this ceiling only has to clear the
// worst legitimate run, not an unbounded one.
export const MAX_SSE_STREAM_BYTES = 32 * 1024 * 1024;

export const MAX_ASSISTANT_MESSAGE_BYTES = 1024 * 1024;

export class StreamSecurityError extends Error {}
