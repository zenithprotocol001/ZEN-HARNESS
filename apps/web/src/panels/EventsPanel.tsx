import { DHCEvent, formatPayload, isToolResult } from "../sanitize";
import { Markdown, ToolResult } from "../components/Markdown";

type WsState = "connecting" | "open" | "closed" | "unauthorized" | "no-token";

export function EventsPanel(props: {
  events: DHCEvent[];
  wsState: WsState;
  authToken: string;
}) {
  return (
    <>
      {props.wsState === "unauthorized" && (
        <div className="event" data-type="auth">
          <strong>auth</strong>
          <pre className="tool-result">
            Server rejected the bearer token. Refresh the page to retry;
            if the issue persists the server has been restarted with a new
            token.
          </pre>
        </div>
      )}
      {props.wsState === "no-token" && (
        <div className="event" data-type="auth">
          <strong>auth</strong>
          <pre className="tool-result">
            No bearer token found in this page. The C1 server must embed
            &lt;meta name="dhc-token"&gt; in index.html. If you opened
            this URL from a cached browser tab, hard-refresh.
          </pre>
        </div>
      )}
      {props.events.map((e, i) => {
        const payloadStr = formatPayload(e.payload);
        const toolResultMode = isToolResult(e.payload);
        return (
          <div key={i} className="event" data-type={e.event}>
            <div>
              <strong>{e.event}</strong>
            </div>
            {toolResultMode ? (
              <pre className="tool-result">
                <ToolResult
                  text={
                    (e.payload as Record<string, unknown>).result as string ??
                    String((e.payload as Record<string, unknown>).error ?? "")
                  }
                />
              </pre>
            ) : (
              <pre className="tool-result">
                <Markdown source={payloadStr} />
              </pre>
            )}
          </div>
        );
      })}
    </>
  );
}
