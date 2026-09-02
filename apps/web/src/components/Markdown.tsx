import { renderMarkdown, renderToolResult } from "../sanitize";

/**
 * <Markdown/> is the ONLY component in the app that calls
 * `dangerouslySetInnerHTML`. The PowerShell invariant script
 * (`scripts/invariants_check.ps1`) scans for this string and fails
 * the build if it appears outside this file. Both `EventsPanel`
 * (live event stream) and `ChatPanel` (chat assistant bubbles)
 * render markdown through this component.
 *
 * The HTML string is always passed through one of the
 * `sanitize.ts` helpers, which apply DOMPurify with a strict
 * `ALLOWED_TAGS`/`FORBID_TAGS` policy. The component is a thin
 * wrapper so the security-critical line is in exactly one place.
 */
export function Markdown(props: { source: string }) {
  return (
    <span
      dangerouslySetInnerHTML={{ __html: renderMarkdown(props.source) }}
    />
  );
}

/**
 * <ToolResult/> is the markdown-equivalent for plain-text tool
 * outputs. It uses `renderToolResult`, which strips all tags and
 * keeps only the text content. Also guarded by the
 * `dangerouslySetInnerHTML` invariant.
 */
export function ToolResult(props: { text: string }) {
  return (
    <span
      dangerouslySetInnerHTML={{ __html: renderToolResult(props.text) }}
    />
  );
}
