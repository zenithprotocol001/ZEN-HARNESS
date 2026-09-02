import { useEffect, useState } from "react";

type PromptListItem = { key: string; name: string; length: number };

export function PromptsPanel() {
  const [prompts, setPrompts] = useState<PromptListItem[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [body, setBody] = useState<string>("");
  const [status, setStatus] = useState<string>("");

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch("/prompts");
        const j = (await r.json()) as { prompts: PromptListItem[]; note?: string };
        if (alive) {
          setPrompts(j.prompts);
          if (j.note) setStatus(j.note);
        }
      } catch (e) {
        if (alive) setStatus(`failed to load prompts: ${e}`);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, []);

  const openPrompt = async (key: string) => {
    setStatus("loading...");
    try {
      // The /prompts endpoint returns a list; we re-fetch the body
      // by joining the key path. The endpoint intentionally does
      // not include the full body in the list to keep the response
      // small; the client uses /api/manifest or a separate fetch to
      // read the text. For simplicity, we reuse the list endpoint
      // and load via a dedicated handler.
      // The handler is /api/prompts/{key} -- we add it now to keep
      // the Prompts tab self-contained.
      const r = await fetch(`/prompts/${encodeURIComponent(key)}`);
      if (!r.ok) {
        setStatus(`server returned ${r.status}`);
        return;
      }
      const j = (await r.json()) as { key: string; body: string };
      setBody(j.body);
      setOpen(j.key);
      setStatus("");
    } catch (e) {
      setStatus(`failed to load prompt: ${e}`);
    }
  };

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Master prompts</h2>
      <p style={{ color: "#8b949e" }}>
        The 10 master prompts from <code>src/dhc/eval/prompts/</code>.
        Click a prompt to view its body. Copy the text and feed it to
        your target LLM; the offline <code>run_llm_eval.py</code> wrapper
        (or the in-browser paste-and-score on the Modules tab) scores
        the result.
      </p>
      {status && <div className="status">{status}</div>}
      <div className="prompt-list">
        {prompts.map((p) => (
          <div
            key={p.key}
            className={"prompt-card" + (open === p.key ? " open" : "")}
          >
            <div className="prompt-card-head">
              <div>
                <div className="prompt-key">{p.key}</div>
                <div className="prompt-name">{p.name}</div>
              </div>
              <div className="prompt-meta">{p.length} chars</div>
            </div>
            <button onClick={() => void openPrompt(p.key)}>
              {open === p.key ? "Close" : "View body"}
            </button>
            {open === p.key && (
              <pre className="prompt-body">{body}</pre>
            )}
          </div>
        ))}
        {prompts.length === 0 && (
          <div className="hint">
            No prompts available. The <code>prompt_browser_v1</code>{" "}
            plugin must be loaded; install it on the Modules tab.
          </div>
        )}
      </div>
    </>
  );
}
