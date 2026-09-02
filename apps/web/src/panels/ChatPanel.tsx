import { useCallback, useEffect, useRef, useState } from "react";
import { ChatFrame, Message, Session, SessionSummary, ToolCall } from "../types/chat";
import { Markdown, ToolResult } from "../components/Markdown";
import { ModelSelect, ModelOption } from "../components/ModelSelect";
import { SettingsModal } from "../components/SettingsModal";
import { ModelConfigMenu } from "../components/ModelConfigMenu";
import { SessionList } from "./SessionList";
import { SearchOverlay } from "../components/SearchOverlay";

type WsState = "connecting" | "open" | "closed" | "unauthorized" | "no-token";

const EMPTY_SESSION_MESSAGES: Message[] = [];

/**
 * <ChatPanel/> is the v1.2.0 chat tab.
 *
 * Layout:
 *   ┌──────────────────────┬──────────────────────────────────┐
 *   │ <SessionList/>       │ <ChatMessages/>                  │
 *   │ (left rail)          │ - assistant bubbles use <Markdown>│
 *   │ - new session btn    │ - tool results use <ToolResult>  │
 *   │ - Today/Yesterday... │ - tool calls shown inline         │
 *   │ - search via Ctrl+K  ├──────────────────────────────────┤
 *   │                      │ <InputArea/>                      │
 *   └──────────────────────┴──────────────────────────────────┘
 *
 * Streaming: connects to /ws/chat, sends `chat.send` frames,
 * appends `chat.delta` chunks to the active assistant bubble,
 * and finalizes with `chat.done` (which persists the turn via
 * the server-side SessionManager).
 */
export function ChatPanel(props: {
  authToken: string;
  wsState: WsState;
}) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [active, setActive] = useState<Session | null>(null);
  const [streaming, setStreaming] = useState<string>("");
  const [searchOpen, setSearchOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [llmOk, setLlmOk] = useState<boolean | null>(null);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [configOpen, setConfigOpen] = useState<boolean>(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  // Refresh the session list (with optional search filter).
  const refreshSessions = useCallback(async (q: string) => {
    try {
      const url = q
        ? `/api/sessions?search=${encodeURIComponent(q)}`
        : "/api/sessions";
      const r = await fetch(url);
      if (!r.ok) return;
      const body = await r.json();
      setSessions(body.sessions || []);
    } catch {
      // swallow; UI just shows stale data
    }
  }, []);

  // Load a single session (full body with messages).
  const loadSession = useCallback(async (id: string) => {
    try {
      const r = await fetch(`/api/sessions/${id}`);
      if (!r.ok) return;
      const body: Session = await r.json();
      setActive(body);
    } catch {
      // swallow
    }
  }, []);

  // Open the WS chat channel.
  useEffect(() => {
    if (!props.authToken) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/chat?token=${encodeURIComponent(props.authToken)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      let frame: ChatFrame;
      try {
        frame = JSON.parse(ev.data) as ChatFrame;
      } catch {
        return;
      }
      if (frame.type === "chat.delta") {
        setStreaming((cur) => cur + frame.delta);
      } else if (frame.type === "chat.tool_call") {
        // Tool calls are shown in the next assistant message.
        // Stash them in a ref attached to the active session.
        setActive((cur) => {
          if (!cur) return cur;
          // Append a synthetic message to surface the tool call.
          const last = cur.messages[cur.messages.length - 1];
          if (last && last.role === "assistant" && last.tool_calls) {
            last.tool_calls.push(...frame.tool_calls);
          } else {
            cur.messages.push({
              id: "tc_" + Date.now().toString(36),
              role: "tool",
              content: JSON.stringify(frame.tool_calls),
              ts_ms: Date.now(),
              tool_calls: frame.tool_calls,
            });
          }
          return { ...cur };
        });
      } else if (frame.type === "chat.done") {
        // Persist the streamed text into the active session.
        setActive((cur) => {
          if (!cur) return cur;
          cur.messages.push({
            id: "m_" + Date.now().toString(36),
            role: "assistant",
            content: "",
            ts_ms: Date.now(),
            tokens: frame.tokens,
          });
          // Replace the last assistant message's content with the
          // streamed text we accumulated (the server already
          // persisted the full turn; this is just the UI).
          const last = cur.messages[cur.messages.length - 1];
          if (last && last.role === "assistant") {
            last.content = streamingRef.current;
          }
          return { ...cur };
        });
        setStreaming("");
        void refreshSessions(searchQuery);
        void loadSession(frame.session_id);
      } else if (frame.type === "chat.error") {
        setStreaming("");
      }
    };
    return () => {
      try { ws.close(); } catch { /* ignore */ }
      wsRef.current = null;
    };
  }, [props.authToken, refreshSessions, loadSession, searchQuery]);

  // Track the latest streaming value so the chat.done handler can
  // snapshot it into the assistant message.
  const streamingRef = useRef<string>("");
  useEffect(() => { streamingRef.current = streaming; }, [streaming]);

  // Initial load: list + LLM health + model registry.
  useEffect(() => {
    void refreshSessions("");
    fetch("/api/llm/health")
      .then((r) => r.json())
      .then((b) => setLlmOk(Boolean(b.ok)))
      .catch(() => setLlmOk(false));
    fetch("/api/models", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { models?: ModelOption[] } | null) => {
        if (b && Array.isArray(b.models)) setModels(b.models);
      })
      .catch(() => {
        // /api/models may 404 in older builds; ignore.
      });
  }, [refreshSessions]);

  // Reload the active session when activeId changes.
  useEffect(() => {
    if (activeId) {
      void loadSession(activeId);
    } else {
      setActive(null);
    }
  }, [activeId, loadSession]);

  // Hotkey: Ctrl+K opens the search overlay.
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        setSearchOpen((s) => !s);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ---------- handlers ----------

  async function newSession() {
    const r = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (r.ok) {
      const s: Session = await r.json();
      setActiveId(s.id);
      await refreshSessions(searchQuery);
    }
  }

  async function pinSession(id: string) {
    const s = sessions.find((x) => x.id === id);
    if (!s) return;
    await fetch(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned: !s.pinned }),
    });
    await refreshSessions(searchQuery);
  }

  async function setSessionModel(id: string, modelId: string) {
    // v1.3.0: PATCH the session with the new model id. The chat
    // WS handler reads `s.model` for the next chat.send frame.
    await fetch(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelId }),
    });
    // Refresh the active session so the new model id is reflected.
    const r = await fetch(`/api/sessions/${id}`);
    if (r.ok) {
      const updated: Session = await r.json();
      setActive(updated);
    }
  }

  async function archiveSession(id: string) {
    await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (activeId === id) {
      setActiveId(null);
    }
    await refreshSessions(searchQuery);
  }

  async function deleteSession(id: string) {
    await fetch(`/api/sessions/${id}?hard=1`, { method: "DELETE" });
    if (activeId === id) {
      setActiveId(null);
    }
    await refreshSessions(searchQuery);
  }

  function send(text: string) {
    if (!activeId) return;
    // Append the user message to the local view immediately.
    setActive((cur) => {
      if (!cur) return cur;
      cur.messages.push({
        id: "m_" + Date.now().toString(36),
        role: "user",
        content: text,
        ts_ms: Date.now(),
      });
      return { ...cur };
    });
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: "chat.send",
        session_id: activeId,
        text,
      }));
    }
  }

  const messages = active?.messages ?? EMPTY_SESSION_MESSAGES;

  return (
    <div className="chat-panel" style={{ display: "flex", height: "100%" }}>
      <aside
        className="chat-sidebar"
        style={{
          width: 240,
          borderRight: "1px solid #30363d",
          overflow: "auto",
          padding: 8,
        }}
      >
        <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
          <button type="button" onClick={() => setSearchOpen(true)} style={smallBtnStyle}>
            🔍 Search
          </button>
        </div>
        <SessionList
          sessions={sessions}
          activeId={activeId}
          onSelect={(id) => setActiveId(id)}
          onNew={newSession}
          onPin={pinSession}
          onArchive={archiveSession}
          onDelete={deleteSession}
        />
      </aside>
      <main className="chat-main" style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <header
          className="chat-header"
          style={{
            padding: "8px 12px",
            borderBottom: "1px solid #30363d",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <strong>{active?.title || "No session selected"}</strong>
          {active && (
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              <ModelSelect
                value={active.model || "mock-llm/default"}
                onChange={(modelId) => setSessionModel(active.id, modelId)}
                disabled={wsState === "connecting"}
              />
              {active && (
                <button
                  type="button"
                  className="chat-config-button"
                  onClick={() => setConfigOpen(true)}
                  aria-label="Open model configuration"
                >
                  ⚙ Config
                </button>
              )}
              <button
                type="button"
                className="chat-settings-button"
                onClick={() => setSettingsOpen(true)}
                aria-label="Open settings"
              >
                ⚙ Keys
              </button>
            </div>
          )}
          {llmOk === false && (
            <span style={{ color: "#f78166", fontSize: 12 }}>LLM: offline</span>
          )}
          {llmOk === true && (
            <span style={{ color: "#3fb950", fontSize: 12 }}>LLM: connected</span>
          )}
        </header>
        <div
          className="chat-messages"
          style={{ flex: 1, overflow: "auto", padding: 12 }}
        >
          {!activeId && (
            <div style={{ color: "#8b949e" }}>
              Click <strong>+ New session</strong> in the sidebar to start a chat.
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {streaming && (
            <div className="message assistant streaming" data-role="assistant">
              <div className="message-content">
                <Markdown source={streaming} />
                <span className="cursor">▌</span>
              </div>
            </div>
          )}
        </div>
        <InputArea onSend={send} disabled={!activeId || props.wsState !== "open"} />
      </main>
      <SearchOverlay
        open={searchOpen}
        query={searchQuery}
        onChange={(q) => {
          setSearchQuery(q);
          void refreshSessions(q);
        }}
        onClose={() => setSearchOpen(false)}
      />
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        models={models}
      />
      {active && (
        <ModelConfigMenu
          sessionID={active.id}
          isOpen={configOpen}
          onClose={() => setConfigOpen(false)}
        />
      )}
    </div>
  );
}

function MessageBubble(props: { message: Message }) {
  const m = props.message;
  const isUser = m.role === "user";
  return (
    <div
      className={`message ${isUser ? "user" : "assistant"}`}
      data-role={m.role}
      style={{
        margin: "6px 0",
        padding: "8px 12px",
        borderRadius: 8,
        background: isUser ? "#1f6feb22" : "#161b22",
        border: "1px solid #30363d",
      }}
    >
      <div style={{ fontSize: 11, color: "#8b949e", marginBottom: 4 }}>
        {m.role}
        {m.tokens && ` · tokens ${m.tokens.prompt + m.tokens.completion}`}
        {m.truncated && " · truncated"}
      </div>
      <div className="message-content">
        {m.role === "tool" && m.tool_calls ? (
          <ToolCallList calls={m.tool_calls} />
        ) : m.role === "tool" ? (
          <ToolResult text={m.content} />
        ) : (
          <Markdown source={m.content} />
        )}
      </div>
    </div>
  );
}

function ToolCallList(props: { calls: ToolCall[] }) {
  return (
    <div className="tool-calls" style={{ fontSize: 12 }}>
      {props.calls.map((tc, i) => (
        <div
          key={i}
          className="tool-call"
          style={{
            background: "#0d1117",
            border: "1px solid #30363d",
            borderRadius: 4,
            padding: 6,
            margin: "4px 0",
          }}
        >
          <strong>🔧 {tc.function?.name || "tool"}</strong>
          {tc.function?.arguments && (
            <pre style={{ margin: "4px 0 0 0", fontSize: 11, color: "#8b949e" }}>
              {tc.function.arguments}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

function InputArea(props: { onSend: (text: string) => void; disabled: boolean }) {
  const [text, setText] = useState<string>("");
  return (
    <div
      className="chat-input"
      style={{
        borderTop: "1px solid #30363d",
        padding: 8,
        display: "flex",
        gap: 8,
      }}
    >
      <textarea
        className="chat-input-area"
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            const t = text.trim();
            if (t) {
              props.onSend(t);
              setText("");
            }
          }
        }}
        placeholder="Ask a question…  (Enter to send, Shift+Enter for newline)"
        disabled={props.disabled}
        style={{
          flex: 1,
          background: "#0d1117",
          color: "#c9d1d9",
          border: "1px solid #30363d",
          borderRadius: 4,
          padding: 8,
          resize: "vertical",
          font: "inherit",
        }}
      />
      <button
        type="button"
        className="chat-send"
        onClick={() => {
          const t = text.trim();
          if (t) {
            props.onSend(t);
            setText("");
          }
        }}
        disabled={props.disabled || !text.trim()}
        style={sendBtnStyle}
      >
        Send
      </button>
    </div>
  );
}

const smallBtnStyle: React.CSSProperties = {
  background: "transparent",
  color: "#8b949e",
  border: "1px solid #30363d",
  borderRadius: 4,
  padding: "4px 8px",
  cursor: "pointer",
  font: "inherit",
  fontSize: 12,
  flex: 1,
};

const sendBtnStyle: React.CSSProperties = {
  background: "#238636",
  color: "white",
  border: "none",
  borderRadius: 4,
  padding: "0 16px",
  cursor: "pointer",
  font: "inherit",
  fontWeight: 600,
};
