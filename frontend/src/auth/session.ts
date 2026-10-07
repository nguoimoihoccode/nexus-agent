import { z } from "zod";

import { clearNexusBrowserState } from "../security/browserState.js";

const MAX_AUTH_RESPONSE_BYTES = 16 * 1024;
const sessionSchema = z.discriminatedUnion("auth_mode", [
  z.object({
    authenticated: z.literal(true),
    auth_mode: z.literal("local"),
    session_key: z.string().min(1).max(200),
    csrf_token: z.literal(""),
  }),
  z.object({
    authenticated: z.literal(true),
    auth_mode: z.literal("browser"),
    session_key: z.string().min(1).max(200),
    csrf_token: z.string().min(32).max(200),
  }),
]);
const logoutSchema = z.object({ logout_url: z.string().max(4096).nullable() });

export type BrowserSession = z.infer<typeof sessionSchema>;

let session: BrowserSession | null = null;
let loading: Promise<BrowserSession | null> | null = null;

async function readAuthJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== "application/json" && !contentType?.endsWith("+json")) {
    throw new Error("Backend trả về auth response không đúng định dạng JSON.");
  }
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_AUTH_RESPONSE_BYTES) {
    throw new Error("Backend trả về auth response quá lớn.");
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_AUTH_RESPONSE_BYTES) {
    throw new Error("Backend trả về auth response quá lớn.");
  }
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new Error("Backend trả về auth response không hợp lệ.");
  }
}

export async function loadSession(force = false): Promise<BrowserSession | null> {
  if (!force && session) return session;
  if (!force && loading) return loading;
  loading = fetch("/auth/session", {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  }).then(async (response) => {
    if (response.status === 401) {
      session = null;
      return null;
    }
    if (!response.ok) throw new Error(`Không thể xác thực phiên đăng nhập (${response.status}).`);
    const parsed = sessionSchema.safeParse(await readAuthJson(response));
    if (!parsed.success) throw new Error("Backend trả về browser session không hợp lệ.");
    session = parsed.data;
    return session;
  }).finally(() => {
    loading = null;
  });
  return loading;
}

export async function authenticatedHeaders(headers: HeadersInit = {}): Promise<Headers> {
  const result = new Headers(headers);
  if (session?.csrf_token) result.set("X-Nexus-CSRF", session.csrf_token);
  return result;
}

export function browserSessionEnabled(): boolean {
  return session?.auth_mode === "browser";
}

export function notifyAuthExpired(): void {
  session = null;
  window.dispatchEvent(new Event("nexus:auth-expired"));
}

function currentReturnTo(): string {
  const target = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  return target.startsWith("/") && !target.startsWith("//") && !target.startsWith("/auth/")
    ? target
    : "/chat";
}

export function signIn(): void {
  window.location.assign(`/auth/login?${new URLSearchParams({ return_to: currentReturnTo() })}`);
}

export async function signOut(): Promise<void> {
  const active = await loadSession();
  let logoutUrl: string | null = null;
  let failure: Error | null = null;
  try {
    const response = await fetch("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: await authenticatedHeaders({ Accept: "application/json" }),
    });
    if (response.ok) {
      const parsed = logoutSchema.safeParse(await readAuthJson(response));
      if (!parsed.success) throw new Error("Backend trả về logout contract không hợp lệ.");
      if (parsed.data.logout_url) {
        try {
          const target = new URL(parsed.data.logout_url, window.location.origin);
          if (target.protocol === "https:" || target.origin === window.location.origin) logoutUrl = target.toString();
        } catch {
          logoutUrl = null;
        }
      }
    } else if (active?.auth_mode === "browser") {
      failure = new Error(`Không thể đăng xuất phiên hiện tại (${response.status}).`);
    }
  } catch (error) {
    failure = error instanceof Error ? error : new Error("Không thể đăng xuất phiên hiện tại.");
  } finally {
    session = null;
    clearNexusBrowserState(true);
  }
  if (failure) throw failure;
  window.location.assign(logoutUrl || window.location.origin);
}
