import DOMPurify from "dompurify";
import { marked } from "marked";

export type DHCEvent = {
  event: string;
  payload: unknown;
};

export function formatPayload(p: unknown): string {
  try {
    return JSON.stringify(p, null, 2);
  } catch {
    return String(p);
  }
}

export function isToolResult(p: unknown): boolean {
  if (typeof p !== "object" || p === null) return false;
  const obj = p as Record<string, unknown>;
  return (
    "tool_name" in obj &&
    (("result" in obj && typeof obj.result === "string") || "error" in obj)
  );
}

export function renderMarkdown(md: string): string {
  const raw = marked.parse(md, { async: false }) as string;
  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS: [
      "b",
      "i",
      "em",
      "strong",
      "code",
      "pre",
      "ul",
      "ol",
      "li",
      "p",
      "br",
      "span",
    ],
    ALLOWED_ATTR: ["class"],
    FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form", "input"],
    FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover", "style", "srcset"],
  });
}

export function renderToolResult(text: string): string {
  return DOMPurify.sanitize(text, {
    ALLOWED_TAGS: [],
    ALLOWED_ATTR: [],
    KEEP_CONTENT: true,
  });
}
