import { type ReactNode, useEffect, useState } from "react";

import { loadSession, signIn, type BrowserSession } from "./session.js";

type AuthGateProps = {
  children: (sessionKey: string) => ReactNode;
};

type AuthGateState = {
  loading: boolean;
  session: BrowserSession | null;
  error: string;
};

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Không thể xác thực phiên đăng nhập.";
}

export function AuthGate({ children }: AuthGateProps) {
  const [state, setState] = useState<AuthGateState>({ loading: true, session: null, error: "" });

  useEffect(() => {
    let active = true;
    const refresh = async (expired = false) => {
      try {
        const session = await loadSession(true);
        if (active) setState({
          loading: false,
          session,
          error: session ? "" : expired ? "Phiên đăng nhập đã hết hạn." : "",
        });
      } catch (error) {
        if (active) setState({ loading: false, session: null, error: errorMessage(error) });
      }
    };
    void refresh();
    const onExpired = () => { void refresh(true); };
    window.addEventListener("nexus:auth-expired", onExpired);
    return () => {
      active = false;
      window.removeEventListener("nexus:auth-expired", onExpired);
    };
  }, []);

  if (state.loading) return <main className="auth-screen">Đang xác thực…</main>;
  if (!state.session) {
    return (
      <main className="auth-screen">
        <h1>Nexus Agent</h1>
        <p>{state.error || "Đăng nhập để tiếp tục."}</p>
        <button type="button" onClick={signIn}>Đăng nhập</button>
      </main>
    );
  }
  return children(state.session.session_key);
}
