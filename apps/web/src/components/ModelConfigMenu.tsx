import { useEffect, useState } from "react";

export type ModelConfigMenuProps = {
  sessionID: string;
  isOpen: boolean;
  onClose: () => void;
};

const DEFAULTS = {
  temperature: 0.7,
  max_tokens: 4096,
  top_p: 1.0,
  system_prompt: "You are a helpful assistant.",
};

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const resp = await fetch(path, { credentials: "same-origin", ...init });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status}: ${text || resp.statusText}`);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

export function ModelConfigMenu(props: ModelConfigMenuProps) {
  const [config, setConfig] = useState({ ...DEFAULTS });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!props.isOpen || !props.sessionID) return;
    setError(null);
    let cancelled = false;
    fetchJson(`/api/sessions/${encodeURIComponent(props.sessionID)}/config`)
      .then((body) => {
        if (cancelled || !body) return;
        const b = body as Partial<typeof DEFAULTS>;
        setConfig({
          temperature: typeof b.temperature === "number" ? b.temperature : DEFAULTS.temperature,
          max_tokens: typeof b.max_tokens === "number" ? b.max_tokens : DEFAULTS.max_tokens,
          top_p: typeof b.top_p === "number" ? b.top_p : DEFAULTS.top_p,
          system_prompt:
            typeof b.system_prompt === "string" ? b.system_prompt : DEFAULTS.system_prompt,
        });
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [props.isOpen, props.sessionID]);

  if (!props.isOpen) return null;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await fetchJson(`/api/sessions/${encodeURIComponent(props.sessionID)}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      props.onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="config-modal-overlay"
      role="presentation"
      onClick={props.onClose}
      data-testid="model-config-menu"
    >
      <div
        className="config-modal-content"
        role="dialog"
        aria-modal="true"
        aria-label="Model configuration"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="config-modal-header">
          <h2>Model configuration</h2>
          <button
            type="button"
            className="config-modal-close"
            aria-label="Close model configuration"
            onClick={props.onClose}
          >
            ×
          </button>
        </div>
        <div className="config-modal-body">
          <div className="config-field">
            <label htmlFor="cfg-temperature">
              Temperature: <strong>{config.temperature.toFixed(2)}</strong>
            </label>
            <input
              id="cfg-temperature"
              type="range"
              min={0}
              max={2}
              step={0.01}
              value={config.temperature}
              onChange={(e) =>
                setConfig({ ...config, temperature: parseFloat(e.target.value) })
              }
              data-testid="cfg-temperature-input"
            />
            <small>0 = deterministic, 2 = creative.</small>
          </div>
          <div className="config-field">
            <label htmlFor="cfg-max-tokens">
              Max tokens: <strong>{config.max_tokens}</strong>
            </label>
            <input
              id="cfg-max-tokens"
              type="range"
              min={256}
              max={8192}
              step={256}
              value={config.max_tokens}
              onChange={(e) =>
                setConfig({ ...config, max_tokens: parseInt(e.target.value, 10) })
              }
              data-testid="cfg-max-tokens-input"
            />
            <small>Upper bound on response length.</small>
          </div>
          <div className="config-field">
            <label htmlFor="cfg-top-p">
              Top P: <strong>{config.top_p.toFixed(2)}</strong>
            </label>
            <input
              id="cfg-top-p"
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={config.top_p}
              onChange={(e) => setConfig({ ...config, top_p: parseFloat(e.target.value) })}
              data-testid="cfg-top-p-input"
            />
            <small>Nucleus sampling threshold.</small>
          </div>
          <div className="config-field">
            <label htmlFor="cfg-system-prompt">System prompt</label>
            <textarea
              id="cfg-system-prompt"
              rows={4}
              className="config-system-prompt"
              value={config.system_prompt}
              onChange={(e) => setConfig({ ...config, system_prompt: e.target.value })}
              data-testid="cfg-system-prompt-input"
            />
            <small>Prepended to the conversation if no system message is already present.</small>
          </div>
          {error && (
            <p className="config-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="config-modal-footer">
          <button type="button" className="config-cancel" onClick={props.onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="config-save"
            onClick={save}
            disabled={saving}
            data-testid="cfg-save"
          >
            {saving ? "Saving..." : "Save configuration"}
          </button>
        </div>
      </div>
    </div>
  );
}
