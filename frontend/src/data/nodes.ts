import type { GraphNode } from "./types.js";

export const BASE_NODES: GraphNode[] = [
  { id: "input", title: "User Request", subtitle: "Natural language input", icon: "user", x: 4, y: 41, tone: "lime", detail: "Điểm vào của một run. Request được gắn với context và thread_id." },
  { id: "prompt", title: "System Prompt", subtitle: "factory.py · code constant", icon: "fileCode", x: 23, y: 9, tone: "violet", detail: "Chỉ dẫn supervisor nằm trực tiếp trong code; project skills và subagent prompts vẫn thuộc harness scope riêng." },
  { id: "memory", title: "User Memory", subtitle: "PostgreSQL · theo tài khoản", icon: "memory", x: 23, y: 73, tone: "blue", detail: "Các fact có key riêng theo actor, kèm bản tổng hợp tương thích API. Supervisor chỉ cập nhật sau explicit consent và native approval." },
  { id: "supervisor", title: "Supervisor Agent", subtitle: "Code system prompt", icon: "network", x: 37, y: 40, tone: "lime", large: true, detail: "Runtime điều phối bằng system prompt trong code, PostgreSQL user memory và project-scoped harness skills." },
  { id: "model", title: "AI Model", subtitle: "DeepSeek · reasoning", icon: "brain", x: 55, y: 10, tone: "coral", detail: "Khối suy luận thuần túy: nhận context từ agent và trả reasoning/tool intent. Model không trực tiếp thực thi tool hay ghi memory." },
  { id: "researcher", title: "Researcher", subtitle: "Research subagent", icon: "bot", x: 55, y: 63, tone: "violet", detail: "Subagent chuyên tra cứu, đối chiếu nguồn và tổng hợp báo cáo có citation." },
  { id: "ai-trader-agent", title: "AI-Trader Agent", subtitle: "Repo worker subagent", icon: "megaphone", x: 55, y: 51, tone: "coral", detail: "Subagent đọc context từ AI-Trader worker và chỉ publish strategy/discussion khi người dùng đã xác nhận rõ." },
  { id: "quant-data-agent", title: "Quant Data Agent", subtitle: "OpenBB data subagent", icon: "database", x: 55, y: 76, tone: "blue", detail: "Subagent chạy pipeline MVP OpenBB sang Qlib dataset: fetch OHLCV, validate staging, build provider layout và validate dataset." },
  { id: "quant-researcher", title: "Quant Researcher", subtitle: "Qlib research subagent", icon: "chart", x: 55, y: 89, tone: "lime", detail: "Subagent read-only chạy workflow CSI300 được allowlist và giải thích signal, portfolio metrics cùng giới hạn backtest." },
  { id: "research-skill", title: "Research Skill", subtitle: ".deepagents/skills/research", icon: "sparkles", x: 70, y: 63, tone: "pink", detail: "Workflow hướng dẫn researcher tìm nguồn, tổng hợp và trích dẫn." },
  { id: "ai-trader-skill", title: "AI-Trader Skill", subtitle: ".deepagents/skills/ai-trader", icon: "megaphone", x: 70, y: 51, tone: "coral", detail: "Workflow đọc market-intel/feed, poll heartbeat và publish strategy/discussion sau xác nhận." },
  { id: "quant-data-skill", title: "Quant Data Skill", subtitle: ".deepagents/skills/quant-data", icon: "database", x: 70, y: 76, tone: "blue", detail: "Workflow validate request, fetch OpenBB OHLCV, validate staging, build dataset và validate Qlib provider." },
  { id: "quant-research-skill", title: "Quant Research Skill", subtitle: ".deepagents/skills/quant-research", icon: "chart", x: 70, y: 89, tone: "lime", detail: "Workflow validate dữ liệu, chạy một Qlib experiment và diễn giải kết quả có cảnh báo." },
  { id: "web-search", title: "Web Search", subtitle: "Tavily · cited sources", icon: "search", x: 85, y: 63, tone: "pink", detail: "Tìm nguồn web cho chủ đề tổng quát hoặc thông tin hiện tại; yêu cầu TAVILY_API_KEY và trả URL để researcher trích dẫn." },
  { id: "ai-trader-api", title: "AI-Trader API", subtitle: "Repo worker service", icon: "broadcast", x: 85, y: 51, tone: "coral", detail: "Worker trong repo cung cấp market-intel, signal feed, publish strategy/discussion và heartbeat." },
  { id: "openbb-ingestion", title: "Quant Data Worker", subtitle: "OpenBB · dataset build", icon: "database", x: 85, y: 76, tone: "blue", detail: "Service riêng cho quant-data-agent: fetch yfinance OHLCV, lưu staging data, build Qlib provider layout và validate artefact." },
  { id: "qlib-worker", title: "Qlib Worker", subtitle: "LightGBM · Alpha158 · CSI300", icon: "server", x: 85, y: 89, tone: "lime", detail: "Service nội bộ chạy job Qlib bất đồng bộ, lưu metrics và artifacts trên volumes riêng." },
  { id: "output", title: "Final Answer", subtitle: "Streaming response", icon: "fileCode", x: 85, y: 10, tone: "lime", detail: "Token được stream dần về client sau khi supervisor tổng hợp kết quả." },
  { id: "checkpoint", title: "Checkpoint", subtitle: "PostgreSQL", icon: "database", x: 85, y: 40, tone: "blue", detail: "Lưu state theo thread để run có thể tiếp tục và quan sát lại." },
];

export const AGENT_NODE_IDS: string[] = ["researcher", "quant-data-agent", "quant-researcher", "ai-trader-agent"];

export const BUILTIN_SKILL_NODES: Record<string, GraphNode | undefined> = {
  research: BASE_NODES.find((node) => node.id === "research-skill"),
  "quant-data": BASE_NODES.find((node) => node.id === "quant-data-skill"),
  "quant-research": BASE_NODES.find((node) => node.id === "quant-research-skill"),
  "ai-trader": BASE_NODES.find((node) => node.id === "ai-trader-skill"),
};

export const ICON_BY_NAME: Record<string, string> = {
  sparkles: "sparkles",
  fileCode: "fileCode",
  terminal: "terminal",
  chart: "chart",
  database: "database",
  broadcast: "broadcast",
  megaphone: "megaphone",
  messages: "messages",
};

export const AGENT_RESOURCE_NODES: Record<string, string[]> = {
  researcher: ["web-search"],
  "quant-data-agent": ["openbb-ingestion"],
  "quant-researcher": ["qlib-worker"],
  "ai-trader-agent": ["ai-trader-api"],
};
