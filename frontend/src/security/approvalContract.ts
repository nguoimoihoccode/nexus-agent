import { z } from "zod";

import type { JsonRecord } from "../types/contracts.js";

export const APPROVAL_ACTION_NAMES = [
  "save_user_memory",
  "delete_user_memory",
  "prepare_qlib_dataset",
  "fetch_factor_snapshot",
  "run_governed_qlib_experiment",
  "record_experiment_interpretation",
  "publish_ai_trader_strategy",
  "publish_ai_trader_discussion",
] as const;

export type SupportedApprovalActionName = typeof APPROVAL_ACTION_NAMES[number];

export type ApprovalAction = {
  name: string;
  args: JsonRecord;
  supported: boolean;
};

export type ApprovalRequest = {
  action_requests: ApprovalAction[];
  status: "verified" | "unsupported" | "malformed";
  blocked_reason: string | null;
};

const MAX_APPROVAL_ACTIONS = 8;
const MAX_APPROVAL_ARGUMENT_BYTES = 16 * 1024;
const MAX_APPROVAL_ARGUMENT_DEPTH = 6;
const supportedActions = new Set<string>(APPROVAL_ACTION_NAMES);
const textEncoder = new TextEncoder();

const approvalEnvelopeSchema = z.object({
  action_requests: z.unknown().optional(),
  actionRequests: z.unknown().optional(),
});

function isJsonRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasBoundedDepth(value: unknown, depth = 0): boolean {
  if (depth > MAX_APPROVAL_ARGUMENT_DEPTH) return false;
  if (Array.isArray(value)) return value.every((item) => hasBoundedDepth(item, depth + 1));
  if (isJsonRecord(value)) {
    return Object.entries(value).every(([key, item]) => (
      key.length <= 200 && hasBoundedDepth(item, depth + 1)
    ));
  }
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function boundedArguments(value: unknown): JsonRecord | null {
  if (!isJsonRecord(value) || !hasBoundedDepth(value)) return null;
  try {
    const encoded = JSON.stringify(value);
    return textEncoder.encode(encoded).byteLength <= MAX_APPROVAL_ARGUMENT_BYTES ? value : null;
  } catch {
    return null;
  }
}

function malformed(reason: string): ApprovalRequest {
  return { action_requests: [], status: "malformed", blocked_reason: reason };
}

export function parseApprovalRequest(value: unknown): ApprovalRequest {
  const envelope = approvalEnvelopeSchema.safeParse(value);
  if (!envelope.success) return malformed("Yêu cầu phê duyệt không đúng định dạng.");
  const rawActions = envelope.data.action_requests ?? envelope.data.actionRequests;
  if (!Array.isArray(rawActions) || rawActions.length < 1 || rawActions.length > MAX_APPROVAL_ACTIONS) {
    return malformed(`Yêu cầu phải có từ 1 đến ${MAX_APPROVAL_ACTIONS} hành động.`);
  }

  const actions: ApprovalAction[] = [];
  for (const rawAction of rawActions) {
    if (!isJsonRecord(rawAction)) return malformed("Một hành động phê duyệt không đúng định dạng.");
    const rawName = rawAction.name ?? rawAction.action;
    if (typeof rawName !== "string" || !/^[a-z][a-z0-9_]{0,99}$/.test(rawName)) {
      return malformed("Tên hành động phê duyệt không hợp lệ.");
    }
    const args = boundedArguments(rawAction.args ?? rawAction.arguments ?? {});
    if (!args) return malformed(`Tham số của hành động ${rawName} vượt giới hạn an toàn.`);
    actions.push({ name: rawName, args, supported: supportedActions.has(rawName) });
  }

  const unsupported = actions.filter((action) => !action.supported);
  if (unsupported.length) {
    return {
      action_requests: actions,
      status: "unsupported",
      blocked_reason: `Không hỗ trợ phê duyệt hành động: ${unsupported.map((action) => action.name).join(", ")}.`,
    };
  }
  return { action_requests: actions, status: "verified", blocked_reason: null };
}

export function canApprove(request: ApprovalRequest): boolean {
  return request.status === "verified" && request.action_requests.length > 0;
}

export function canReject(request: ApprovalRequest): boolean {
  return request.status !== "malformed" && request.action_requests.length > 0;
}

function displayList(value: unknown): string | null {
  return Array.isArray(value) && value.length
    ? value.map(String).slice(0, 12).join(", ")
    : null;
}

export function approvalSummary(action: ApprovalAction): Array<{ label: string; value: string }> {
  const field = (key: string) => {
    const value = action.args[key];
    return value === null || value === undefined || value === "" ? null : String(value);
  };
  const fields: Array<[string, string | null]> = action.name === "save_user_memory"
    ? [["Memory key", field("memory_key")], ["Nội dung", field("content")]]
    : action.name === "delete_user_memory"
      ? [["Phạm vi", "Toàn bộ persistent memory của tài khoản"]]
      : action.name === "prepare_qlib_dataset"
        ? [["Mã", displayList(action.args.symbols)], ["Từ", field("start_date")], ["Đến", field("end_date")], ["Provider", field("provider")]]
        : action.name === "fetch_factor_snapshot"
          ? [["Mã", displayList(action.args.symbols)], ["Ngày", field("as_of_date")], ["Provider", field("provider")]]
          : action.name === "run_governed_qlib_experiment"
            ? [["Dataset", field("dataset_revision_id")], ["Model", field("model")], ["Test từ", field("test_start")], ["Test đến", field("test_end")]]
            : action.name === "record_experiment_interpretation"
              ? [
                ["Experiment", field("experiment_id")],
                ["Tóm tắt", field("summary")],
                ["Số findings", Array.isArray(action.args.findings) ? String(action.args.findings.length) : null],
                ["Evidence", displayList(action.args.evidence_ids)],
              ]
            : action.name === "publish_ai_trader_strategy"
              ? [
                ["Thị trường", field("market")],
                ["Tiêu đề", field("title")],
                ["Mã", field("symbols") ?? field("symbol")],
                ["Nội dung", field("content")],
                ["Action digest", field("action_digest")],
              ]
              : action.name === "publish_ai_trader_discussion"
                ? [
                  ["Thị trường", field("market")],
                  ["Tiêu đề", field("title")],
                  ["Mã", field("symbol")],
                  ["Nội dung", field("content")],
                  ["Action digest", field("action_digest")],
                ]
              : [["Trạng thái", "Hành động chưa được frontend hỗ trợ"]];
  return fields
    .filter((entry): entry is [string, string] => Boolean(entry[1]))
    .map(([label, value]) => ({ label, value: value.length > 2_000 ? `${value.slice(0, 2_000)}…` : value }));
}
