import { useEffect, useState } from "react";
import { DHCEvent } from "./sanitize";
import { ModulesPanel } from "./panels/ModulesPanel";
import { EventsPanel } from "./panels/EventsPanel";
import { PromptsPanel } from "./panels/PromptsPanel";
import { ChatPanel } from "./panels/ChatPanel";

type Tab = "modules" | "events" | "prompts" | "chat";
type WsState = "connecting" | "open" | "closed" | "unauthorized" | "no-token";

/**
 * Read the per-launch auth token that the Python bridge embeds in the
 * served index.html. This is the only place the token lives in the
 * client. The meta tag is set by the server at start time and wiped
 * on stop.
 */
function readAuthToken(): string {
  const el = document.querySelector('meta[name="dhc-token"]');
  if (!el) return "";
  return (el.getAttribute("content") || "").trim();
}

function App() {
  const [tab, setTab] = useState<Tab>("modules");
  const [events, setEvents] = useState<DHCEvent[]>([]);
  const [wsState, setWsState] = useState<WsState>("connecting");
  const [authToken, setAuthToken] = useState<string>("");

  useEffect(() => {
    const token = readAuthToken();
    if (!token) {
      setWsState("no-token");
      return;
    }
    setAuthToken(token);

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    // The bearer token travels in the query string because the browser
    // WebSocket API cannot set custom headers on the handshake.
    const url = `${proto}://${window.location.host}/ws?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    ws.onopen = () => setWsState("open");
    ws.onclose = (ev) => {
      if (ev && ev.code === 1008) {
        setWsState("unauthorized");
      } else {
        setWsState("closed");
      }
    };
    ws.onerror = () => setWsState("closed");
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as DHCEvent;
        setEvents((prev) => [...prev, data].slice(-200));
      } catch {
        // swallow malformed frames
      }
    };
    return () => ws.close();
  }, []);

  const stateColor =
    wsState === "open"
      ? "#3fb950"
      : wsState === "unauthorized" || wsState === "no-token"
        ? "#f78166"
        : "#d29922";

  return (
    <>
      <header>
        <strong>DHC Web Core</strong>{" "}
        <span style={{ color: stateColor }}>[{wsState}]</span>
        <nav style={{ marginLeft: 24, display: "inline-block" }}>
          <TabButton current={tab} value="modules" setTab={setTab}>
            Modules
          </TabButton>
          <TabButton current={tab} value="events" setTab={setTab}>
            Events
          </TabButton>
          <TabButton current={tab} value="prompts" setTab={setTab}>
            Prompts
          </TabButton>
          <TabButton current={tab} value="chat" setTab={setTab}>
            Chat
          </TabButton>
        </nav>
        <span style={{ float: "right", color: "#8b949e", fontSize: 12 }}>
          token {authToken ? `${authToken.slice(0, 6)}...${authToken.slice(-3)}` : "(none)"}
        </span>
      </header>
      <main>
        {tab === "modules" && <ModulesPanel events={events} wsState={wsState} />}
        {tab === "events" && <EventsPanel events={events} wsState={wsState} authToken={authToken} />}
        {tab === "prompts" && <PromptsPanel />}
        {tab === "chat" && <ChatPanel authToken={authToken} wsState={wsState} />}
      </main>
    </>
  );
}

function TabButton(props: {
  current: Tab;
  value: Tab;
  setTab: (t: Tab) => void;
  children: React.ReactNode;
}) {
  const active = props.current === props.value;
  return (
    <button
      onClick={() => props.setTab(props.value)}
      style={{
        background: "transparent",
        color: active ? "#58a6ff" : "#8b949e",
        border: "none",
        borderBottom: active ? "2px solid #58a6ff" : "2px solid transparent",
        padding: "4px 12px",
        marginRight: 8,
        cursor: "pointer",
        font: "inherit",
        fontWeight: active ? 600 : 400,
      }}
    >
      {props.children}
    </button>
  );
}

export { App };
