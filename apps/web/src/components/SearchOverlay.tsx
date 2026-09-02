/**
 * <SearchOverlay/> is a Ctrl+K modal that filters sessions by
 * title or message content. It calls back to the parent with the
 * current query; the parent does the actual filtering via the
 * /api/sessions?search=... endpoint.
 */
import { useEffect, useRef } from "react";

export function SearchOverlay(props: {
  open: boolean;
  query: string;
  onChange: (q: string) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (props.open && ref.current) {
      ref.current.focus();
      ref.current.select();
    }
  }, [props.open]);

  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        if (props.open) {
          props.onClose();
        }
      } else if (ev.key === "Escape" && props.open) {
        props.onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.open, props.onClose]);

  if (!props.open) return null;
  return (
    <div
      className="search-overlay-backdrop"
      onClick={props.onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        zIndex: 100,
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: 80,
      }}
    >
      <div
        className="search-overlay"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#0d1117",
          border: "1px solid #30363d",
          borderRadius: 8,
          padding: 12,
          width: 480,
        }}
      >
        <input
          ref={ref}
          className="search-overlay-input"
          type="text"
          placeholder="Search sessions by title or content…"
          value={props.query}
          onChange={(e) => props.onChange(e.target.value)}
          style={{
            width: "100%",
            padding: "8px 10px",
            background: "#161b22",
            color: "#c9d1d9",
            border: "1px solid #30363d",
            borderRadius: 4,
            font: "inherit",
            boxSizing: "border-box",
          }}
        />
        <div style={{ fontSize: 11, color: "#8b949e", marginTop: 6 }}>
          Press <kbd>Esc</kbd> to close · <kbd>Ctrl+K</kbd> to toggle
        </div>
      </div>
    </div>
  );
}
