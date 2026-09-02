import { SessionSummary } from "../types/chat";

/**
 * <SessionList/> is the left-rail session history. It groups
 * sessions by recency (Today / Yesterday / Older) and supports
 * search via the parent (the search input lives in the parent
 * ChatPanel; this component is purely presentational).
 *
 * Props:
 *   sessions   - the list to render (already filtered by the parent)
 *   activeId   - the currently-open session id, or null
 *   onSelect   - called when the user clicks a session
 *   onNew      - called when the user clicks "New session"
 *   onPin      - called when the user pins/unpins a session
 *   onArchive  - called when the user archives a session
 *   onDelete   - called when the user hard-deletes a session
 */
export function SessionList(props: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onPin: (id: string) => void;
  onArchive: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const groups = groupByRecency(props.sessions);
  return (
    <div className="session-list">
      <div className="session-list-header">
        <strong>Sessions</strong>
        <button
          type="button"
          className="session-list-new"
          onClick={() => props.onNew()}
          title="New session"
        >
          +
        </button>
      </div>
      {groups.length === 0 && (
        <div className="session-list-empty">No sessions yet.</div>
      )}
      {groups.map((g) => (
        <div key={g.label} className="session-list-group">
          <div className="session-list-group-label">{g.label}</div>
          {g.sessions.map((s) => (
            <SessionCard
              key={s.id}
              session={s}
              active={s.id === props.activeId}
              onSelect={() => props.onSelect(s.id)}
              onPin={() => props.onPin(s.id)}
              onArchive={() => props.onArchive(s.id)}
              onDelete={() => props.onDelete(s.id)}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function SessionCard(props: {
  session: SessionSummary;
  active: boolean;
  onSelect: () => void;
  onPin: () => void;
  onArchive: () => void;
  onDelete: () => void;
}) {
  const s = props.session;
  const style: React.CSSProperties = {
    cursor: "pointer",
    padding: "6px 8px",
    borderRadius: 4,
    background: props.active ? "#1f6feb22" : "transparent",
    border: props.active ? "1px solid #1f6feb55" : "1px solid transparent",
    display: "flex",
    alignItems: "center",
    gap: 6,
  };
  return (
    <div
      className="session-card"
      data-session-id={s.id}
      style={style}
      onClick={props.onSelect}
    >
      {s.pinned && <span title="Pinned">📌</span>}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            color: "#c9d1d9",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {s.title || "New session"}
        </div>
        <div style={{ fontSize: 11, color: "#8b949e" }}>
          {s.message_count} msg{s.message_count === 1 ? "" : "s"}
        </div>
      </div>
      <button
        type="button"
        title={s.pinned ? "Unpin" : "Pin"}
        onClick={(e) => { e.stopPropagation(); props.onPin(); }}
        style={iconButtonStyle}
      >
        📌
      </button>
      <button
        type="button"
        title="Archive"
        onClick={(e) => { e.stopPropagation(); props.onArchive(); }}
        style={iconButtonStyle}
      >
        🗄
      </button>
      <button
        type="button"
        title="Delete"
        onClick={(e) => { e.stopPropagation(); props.onDelete(); }}
        style={iconButtonStyle}
      >
        ✕
      </button>
    </div>
  );
}

const iconButtonStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#8b949e",
  cursor: "pointer",
  fontSize: 12,
  padding: 2,
};

type Group = { label: string; sessions: SessionSummary[] };

function groupByRecency(sessions: SessionSummary[]): Group[] {
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;
  const today: SessionSummary[] = [];
  const yesterday: SessionSummary[] = [];
  const older: SessionSummary[] = [];
  for (const s of sessions) {
    const ageDays = (now - s.updated_at) / dayMs;
    if (ageDays < 1) today.push(s);
    else if (ageDays < 2) yesterday.push(s);
    else older.push(s);
  }
  const groups: Group[] = [];
  if (today.length) groups.push({ label: "Today", sessions: today });
  if (yesterday.length) groups.push({ label: "Yesterday", sessions: yesterday });
  if (older.length) groups.push({ label: "Older", sessions: older });
  return groups;
}
