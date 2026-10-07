import { authenticatedHeaders, notifyAuthExpired } from "../auth/session.js";

const configuredApiUrl = import.meta.env?.VITE_LANGGRAPH_API_URL || "/api";
if (import.meta.env.PROD && configuredApiUrl !== "/api") {
  throw new Error("Production API URL must remain same-origin at /api.");
}
export const API_URL = configuredApiUrl;
const MAX_CHAT_JSON_BYTES = 8 * 1024 * 1024;
const MAX_ERROR_BYTES = 64 * 1024;

export async function requestHeaders(headers: HeadersInit = {}): Promise<HeadersInit> {
  return authenticatedHeaders(headers);
}

export async function readError(response: Response): Promise<string> {
  if (response.status === 401) notifyAuthExpired();
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_ERROR_BYTES) {
    return `Backend error response vượt quá giới hạn (${response.status}).`;
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_ERROR_BYTES) {
    return `Backend error response vượt quá giới hạn (${response.status}).`;
  }
  try {
    const parsed = JSON.parse(body);
    return parsed.detail || parsed.message || body;
  } catch {
    return body || `${response.status} ${response.statusText}`;
  }
}

export async function readJson(response: Response): Promise<unknown> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_CHAT_JSON_BYTES) {
    throw new ChatApiError("Backend JSON response vượt quá giới hạn 8 MiB.", 502);
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_CHAT_JSON_BYTES) {
    throw new ChatApiError("Backend JSON response vượt quá giới hạn 8 MiB.", 502);
  }
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new ChatApiError("Backend returned malformed JSON.", 502);
  }
}

export class ChatApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ChatApiError";
    this.status = status;
  }
}
