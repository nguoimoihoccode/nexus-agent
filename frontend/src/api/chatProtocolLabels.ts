export const SUBAGENT_NAMES = [
  "researcher",
  "quant-data-agent",
  "quant-researcher",
  "ai-trader-agent",
] as const;

export type SubagentName = typeof SUBAGENT_NAMES[number];

export const SUBAGENT_TITLE: Record<SubagentName, string> = {
  researcher: "Researcher",
  "quant-data-agent": "Quant Data Agent",
  "quant-researcher": "Quant Researcher",
  "ai-trader-agent": "AI-Trader Agent",
};

export const SUBAGENT_WRITING: Record<SubagentName, string> = {
  researcher: "Researcher đang viết báo cáo",
  "quant-data-agent": "Quant Data Agent đang viết trạng thái dataset",
  "quant-researcher": "Quant Researcher đang viết báo cáo định lượng",
  "ai-trader-agent": "AI-Trader Agent đang viết trạng thái",
};

export const SUBAGENT_REASONING: Record<SubagentName, string> = {
  researcher: "Researcher đang phân tích tài liệu",
  "quant-data-agent": "Quant Data Agent đang kiểm tra yêu cầu dữ liệu",
  "quant-researcher": "Quant Researcher đang phân tích kết quả Qlib",
  "ai-trader-agent": "AI-Trader Agent đang kiểm tra context và publish gate",
};

export const SUBAGENT_READING: Record<SubagentName, string> = {
  researcher: "Researcher đang tra cứu nguồn",
  "quant-data-agent": "Quant Data Agent đang kiểm tra pipeline dữ liệu",
  "quant-researcher": "Quant Researcher đang kiểm tra dataset và experiment",
  "ai-trader-agent": "AI-Trader Agent đang đọc market context",
};
