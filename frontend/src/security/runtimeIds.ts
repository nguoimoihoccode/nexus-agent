const RUNTIME_ID = /^[A-Za-z0-9._:-]{1,200}$/;
const EVENT_ID = /^[\x21-\x7E]{1,200}$/;

export function parseRuntimeId(value: unknown, label = "Runtime ID"): string {
  if (typeof value !== "string" || !RUNTIME_ID.test(value)) {
    throw new Error(`${label} không hợp lệ.`);
  }
  return value;
}

export function parseEventId(value: unknown): string {
  if (typeof value !== "string" || !EVENT_ID.test(value)) {
    throw new Error("Stream event ID không hợp lệ.");
  }
  return value;
}

export function runtimePath(value: unknown, label?: string): string {
  return encodeURIComponent(parseRuntimeId(value, label));
}
