import { useEffect, useState } from "react";

export type ModelOption = {
  id: string;
  name: string;
  provider: string;
  context_length: number;
  pricing_input: number;
  pricing_output: number;
  capabilities: string[];
};

export type ModelSelectProps = {
  /** The currently-selected model id (from the parent session). */
  value: string;
  /** Called when the user picks a different model. */
  onChange: (modelId: string) => void;
  /** Optional: a pre-fetched list (skips the GET). */
  models?: ModelOption[];
  /** Whether the dropdown is disabled (e.g. mid-stream). */
  disabled?: boolean;
};

/**
 * <ModelSelect/> is the v1.3.0 chat header widget that lets the
 * user pick a model. It fetches `GET /api/models` on mount, groups
 * options by provider via <optgroup>, and calls `onChange` when the
 * selection changes.
 *
 * Per ADR-0006 and ADR-0007, this component does NOT collect API
 * keys (the Settings modal is deferred to v1.3.1). If the user
 * picks a live provider model without a key, the chat WS will
 * surface a `chat.error` frame; the parent (ChatPanel) renders
 * the error inline.
 */
export function ModelSelect(props: ModelSelectProps) {
  const [models, setModels] = useState<ModelOption[]>(props.models ?? []);
  const [loading, setLoading] = useState(!props.models);

  useEffect(() => {
    if (props.models) {
      setModels(props.models);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetch("/api/models", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((body: { models: ModelOption[] }) => {
        if (!cancelled) {
          setModels(body.models ?? []);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [props.models]);

  // Group by provider for the <optgroup>.
  const grouped = new Map<string, ModelOption[]>();
  for (const m of models) {
    const arr = grouped.get(m.provider) ?? [];
    arr.push(m);
    grouped.set(m.provider, arr);
  }

  return (
    <label className="model-select" aria-label="Model">
      <span className="model-select__label">Model</span>
      <select
        className="model-select__input"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        disabled={props.disabled || loading}
      >
        {Array.from(grouped.entries()).map(([provider, list]) => (
          <optgroup key={provider} label={provider}>
            {list.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}
