// Session type definitions shared by ChatPanel, SessionList, and
// SearchOverlay. Mirrors the JSON shape of GET /api/sessions and
// GET /api/sessions/{id}.

export type Role = "user" | "assistant" | "system" | "tool";

export type Message = {
  id: string;
  role: Role;
  content: string;
  ts_ms: number;
  tool_calls?: ToolCall[];
  tokens?: { prompt: number; completion: number };
  truncated?: boolean;
};

export type ToolCall = {
  index?: number;
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
};

export type SessionSummary = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  model: string;
  tags: string[];
  pinned: boolean;
  archived: boolean;
};

export type Session = SessionSummary & {
  messages: Message[];
};

export type ChatFrame =
  | { type: "chat.delta"; session_id: string; delta: string }
  | { type: "chat.tool_call"; session_id: string; tool_calls: ToolCall[] }
  | { type: "chat.done"; session_id: string; tokens: { prompt: number; completion: number }; latency_ms: number }
  | { type: "chat.error"; code: string; message?: string; session_id?: string };
