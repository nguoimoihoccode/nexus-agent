import type { ReactNode } from "react";
import { AlertTriangle, FileQuestion, LoaderCircle, ShieldX } from "lucide-react";
import { Link } from "@tanstack/react-router";

type AsyncStateProps = {
  kind?: "loading" | "empty" | "error" | "unauthorized";
  title: string;
  message?: string;
  action?: ReactNode;
};

export function AsyncState({ kind = "empty", title, message, action }: AsyncStateProps) {
  const Icon = kind === "loading"
    ? LoaderCircle
    : kind === "unauthorized"
      ? ShieldX
      : kind === "error"
        ? AlertTriangle
        : FileQuestion;
  return (
    <section className={`async-state ${kind}`} role={kind === "error" || kind === "unauthorized" ? "alert" : "status"}>
      <Icon className={kind === "loading" ? "spin" : ""} size={26} />
      <h2>{title}</h2>
      {message && <p>{message}</p>}
      {action}
    </section>
  );
}

export function RouteFailure({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <AsyncState
      kind="error"
      title="Không thể mở trang"
      message={error.message || "Đã xảy ra lỗi không mong đợi."}
      action={<button className="primary-action" type="button" onClick={onRetry}>Thử lại</button>}
    />
  );
}

export function RouteNotFound() {
  return (
    <AsyncState
      title="Không tìm thấy trang"
      message="Đường dẫn không tồn tại hoặc định danh không hợp lệ."
      action={<Link className="primary-action" to="/chat">Về trang chat</Link>}
    />
  );
}
