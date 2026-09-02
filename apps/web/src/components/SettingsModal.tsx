import { useEffect, useMemo, useState } from "react";
import type { ModelOption } from "./ModelSelect";

export type SettingsModalProps = {
  isOpen: boolean;
  onClose: () => void;
  /** Pre-fetched list of models. Used to group keys by provider. */
  models: ModelOption[];
};

type SaveState =
  | { kind: "empty" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; message: string };

function secretNameFor(provider: string, models: ModelOption[]): string {
  // Per ADR-0007, the secret name uses the model id without the
  // provider prefix. Pick the first model for this provider as
  // the canonical key. (Settings only needs ONE key per provider
  // to talk to the API; per-model keys would be a v1.4.0 feature.)
  const m = models.find((x) => x.provider === provider);
  if (!m) return `llm_provider_${provider}_`;
  const modelPart = m.id.includes("/") ? m.id.split("/").slice(1).join("/") : m.id;
  return `llm_provider_${provider}_${modelPart}`;
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const resp = await fetch(path, { credentials: "same-origin", ...init });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status}: ${text || resp.statusText}`);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

export function SettingsModal(props: SettingsModalProps) {
  const providers = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const m of props.models) {
      if (!seen.has(m.provider)) {
        seen.add(m.provider);
        out.push(m.provider);
      }
    }
    return out;
  }, [props.models]);

  // Map of provider -> { saved: bool, state: SaveState, draft: string }.
  const [rows, setRows] = useState<
    Record<string, { saved: boolean; state: SaveState; draft: string }>
  >({});

  // Discover which provider keys are already saved. The secrets
  // listing endpoint only returns names; we filter for the
  // `llm_provider_` prefix and infer "saved" from name membership.
  useEffect(() => {
    if (!props.isOpen) return;
    let cancelled = false;
    (async () => {
      try {
        const body = (await fetchJson("/api/secrets")) as { names?: string[] };
        const saved = new Set<string>();
        for (const n of body.names ?? []) {
          if (!n.startsWith("llm_provider_")) continue;
          // Pull the provider segment: `llm_provider_{provider}_{model_part}`.
          const rest = n.slice("llm_provider_".length);
          const idx = rest.indexOf("_");
          if (idx > 0) saved.add(rest.slice(0, idx));
        }
        if (cancelled) return;
        setRows((prev) => {
          const next = { ...prev };
          for (const p of providers) {
            const cur = next[p] ?? { saved: false, state: { kind: "empty" }, draft: "" };
            cur.saved = saved.has(p);
            next[p] = cur;
          }
          return next;
        });
      } catch {
        // No secrets service → no provider keys are saved.
        if (cancelled) return;
        setRows((prev) => {
          const next = { ...prev };
          for (const p of providers) {
            const cur = next[p] ?? { saved: false, state: { kind: "empty" }, draft: "" };
            cur.saved = false;
            next[p] = cur;
          }
          return next;
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.isOpen, providers.join("|")]);

  if (!props.isOpen) return null;

  const saveKey = async (provider: string) => {
    const row = rows[provider];
    if (!row || !row.draft.trim()) {
      setRows((prev) => ({
        ...prev,
        [provider]: { ...row, state: { kind: "error", message: "API key cannot be empty" } },
      }));
      return;
    }
    setRows((prev) => ({ ...prev, [provider]: { ...row, state: { kind: "saving" } } }));
    try {
      const name = secretNameFor(provider, props.models);
      await fetchJson("/api/secrets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, value: row.draft }),
      });
      setRows((prev) => ({
        ...prev,
        [provider]: { saved: true, state: { kind: "saved" }, draft: "" },
      }));
    } catch (e) {
      setRows((prev) => ({
        ...prev,
        [provider]: { ...row, state: { kind: "error", message: String(e) } },
      }));
    }
  };

  const deleteKey = async (provider: string) => {
    try {
      const name = secretNameFor(provider, props.models);
      await fetchJson(`/api/secrets/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      setRows((prev) => ({
        ...prev,
        [provider]: { saved: false, state: { kind: "empty" }, draft: "" },
      }));
    } catch (e) {
      setRows((prev) => ({
        ...prev,
        [provider]: { ...prev[provider], state: { kind: "error", message: String(e) } },
      }));
    }
  };

  return (
    <div
      className="settings-modal-overlay"
      role="presentation"
      onClick={props.onClose}
      data-testid="settings-modal"
    >
      <div
        className="settings-modal-content"
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="settings-modal-header">
          <h2>Settings</h2>
          <button
            type="button"
            className="settings-modal-close"
            aria-label="Close settings"
            onClick={props.onClose}
          >
            ×
          </button>
        </div>
        <div className="settings-modal-body">
          <h3>API Keys</h3>
          <p className="settings-modal-description">
            Enter your API keys for each provider. Keys are encrypted
            and stored locally in the harness's encrypted log; they
            are never sent to the browser in any response.
          </p>
          {providers.length === 0 && (
            <p className="settings-modal-empty">No models available.</p>
          )}
          {providers.map((provider) => {
            const row = rows[provider] ?? {
              saved: false,
              state: { kind: "empty" as const },
              draft: "",
            };
            const modelsForProvider = props.models.filter((m) => m.provider === provider);
            return (
              <div key={provider} className="settings-provider-row" data-provider={provider}>
                <h4>{provider}</h4>
                <p className="settings-provider-models">
                  Models: {modelsForProvider.map((m) => m.name).join(", ")}
                </p>
                {row.saved ? (
                  <div className="settings-key-status">
                    <span className="settings-key-saved" data-testid={`saved-${provider}`}>
                      ✓ Key saved
                    </span>
                    <input
                      type="password"
                      className="settings-key-input"
                      placeholder={`Replace ${provider} API key`}
                      value={row.draft}
                      onChange={(e) =>
                        setRows((prev) => ({ ...prev, [provider]: { ...row, draft: e.target.value } }))
                      }
                    />
                    <button
                      type="button"
                      className="settings-save-button"
                      onClick={() => saveKey(provider)}
                      disabled={row.state.kind === "saving" || !row.draft.trim()}
                    >
                      {row.state.kind === "saving" ? "Saving..." : "Update"}
                    </button>
                    <button
                      type="button"
                      className="settings-delete-button"
                      onClick={() => deleteKey(provider)}
                    >
                      Delete
                    </button>
                  </div>
                ) : (
                  <div className="settings-key-input-group">
                    <input
                      type="password"
                      className="settings-key-input"
                      placeholder={`Enter ${provider} API key`}
                      value={row.draft}
                      onChange={(e) =>
                        setRows((prev) => ({ ...prev, [provider]: { ...row, draft: e.target.value } }))
                      }
                    />
                    <button
                      type="button"
                      className="settings-save-button"
                      onClick={() => saveKey(provider)}
                      disabled={row.state.kind === "saving" || !row.draft.trim()}
                    >
                      {row.state.kind === "saving" ? "Saving..." : "Save"}
                    </button>
                  </div>
                )}
                {row.state.kind === "error" && (
                  <p className="settings-error" role="alert">
                    {row.state.message}
                  </p>
                )}
              </div>
            );
          })}
        </div>
        <div className="settings-modal-footer">
          <button type="button" className="settings-close-button" onClick={props.onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
