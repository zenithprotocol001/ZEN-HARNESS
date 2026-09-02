import { useEffect, useState } from "react";
import { DHCEvent } from "../sanitize";
import { ModuleCard } from "../components/ModuleCard";

type WsState = "connecting" | "open" | "closed" | "unauthorized" | "no-token";

type Health = "ok" | "idle" | "error";

type ManifestModule = { id: string; key: string };
type ManifestPlugin = {
  id: string;
  name: string;
  version: string;
  loaded: boolean;
};

type Healthz = {
  ok: boolean;
  modules?: ManifestModule[];
  plugins_discovered?: ManifestPlugin[];
  plugins_loaded?: ManifestPlugin[];
};

type EvalResult = {
  module: string;
  functionality: number;
  security: number;
  dhc_v: number;
  floor_triggered: boolean;
  unit_pass_rate: number;
  tests_passed: number;
  tests_failed_or_errored: number;
  findings: Array<{ severity: string; description: string }>;
};

function moduleHealth(events: DHCEvent[], modId: string): Health {
  const filtered = events.filter((e) => e.event.startsWith(modId + "/") || e.event === modId);
  if (filtered.length === 0) return "idle";
  const last = filtered[filtered.length - 1];
  const payload = last.payload as Record<string, unknown> | null;
  if (last.event.endsWith("/error") || (payload && "error" in payload)) {
    return "error";
  }
  return "ok";
}

function pluginHealth(events: DHCEvent[], pluginId: string): Health {
  const filtered = events.filter((e) => {
    const p = e.payload as Record<string, unknown> | null;
    return p && (p.plugin_id === pluginId || p.id === pluginId);
  });
  if (filtered.length === 0) return "idle";
  return "ok";
}

export function ModulesPanel(props: {
  events: DHCEvent[];
  wsState: WsState;
}) {
  const [healthz, setHealthz] = useState<Healthz | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [evalResult, setEvalResult] = useState<EvalResult | null>(null);
  const [pasted, setPasted] = useState<string>("");
  const [pasteModule, setPasteModule] = useState<string>("c4");

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("/healthz");
        const j = (await r.json()) as Healthz;
        if (alive) setHealthz(j);
      } catch {
        // ignore; next poll will retry
      }
    };
    void poll();
    const id = setInterval(poll, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const load = async (pluginId: string) => {
    setBusy(pluginId);
    try {
      await fetch(`/plugins/${pluginId}`, { method: "POST" });
      const r = await fetch("/healthz");
      setHealthz((await r.json()) as Healthz);
    } finally {
      setBusy(null);
    }
  };
  const unload = async (pluginId: string) => {
    setBusy(pluginId);
    try {
      await fetch(`/plugins/${pluginId}`, { method: "DELETE" });
      const r = await fetch("/healthz");
      setHealthz((await r.json()) as Healthz);
    } finally {
      setBusy(null);
    }
  };
  const pasteScore = async () => {
    setBusy("paste");
    try {
      const r = await fetch("/api/eval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ module: pasteModule, code: pasted }),
      });
      setEvalResult((await r.json()) as EvalResult);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Core modules</h2>
      <div className="grid">
        {(healthz?.modules ?? Array.from({ length: 10 }, (_, i) => ({ id: `c${i + 1}`, key: `c${i + 1}` }))).map(
          (m) => (
            <ModuleCard
              key={m.id}
              id={m.id}
              name={m.id.toUpperCase()}
              health={moduleHealth(props.events, m.id)}
              version="1.0.0"
              kind="core"
            />
          ),
        )}
      </div>

      <h2>Plugins</h2>
      <div className="grid">
        {(healthz?.plugins_discovered ?? []).map((p) => (
          <ModuleCard
            key={p.id}
            id={p.id}
            name={p.name}
            health={pluginHealth(props.events, p.id)}
            version={p.version}
            kind="plugin"
            loaded={p.loaded}
            busy={busy === p.id}
            onLoad={p.loaded ? undefined : () => load(p.id)}
            onUnload={p.loaded ? () => unload(p.id) : undefined}
          />
        ))}
        {healthz && (healthz.plugins_discovered ?? []).length === 0 && (
          <div className="hint">
            No plugins discovered. Add a plugin under{" "}
            <code>src/dhc/plugins/&lt;id&gt;/</code> and refresh.
          </div>
        )}
      </div>

      <h2>Paste-and-score</h2>
      <p style={{ color: "#8b949e" }}>
        Paste an LLM response and pick the target module. We run the
        module's tests in a subprocess with the pasted code and score
        the result using the same Finding pipeline as the offline eval
        wrapper.
      </p>
      <div className="paste-row">
        <select
          value={pasteModule}
          onChange={(e) => setPasteModule(e.target.value)}
        >
          {(healthz?.modules ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.id}
            </option>
          ))}
        </select>
        <textarea
          placeholder={`# paste a Python module body here\nimport os\ndef example():\n    return 1`}
          value={pasted}
          onChange={(e) => setPasted(e.target.value)}
          rows={8}
        />
        <button onClick={pasteScore} disabled={busy === "paste" || !pasted}>
          {busy === "paste" ? "Scoring..." : "Score"}
        </button>
      </div>
      {evalResult && (
        <div className="eval-result">
          <h3>
            dhc_v = {evalResult.dhc_v.toFixed(2)}
            <span
              className="band"
              style={{
                color:
                  evalResult.dhc_v >= 80
                    ? "#3fb950"
                    : evalResult.dhc_v >= 50
                      ? "#d29922"
                      : "#f78166",
              }}
            >
              {" "}
              {evalResult.dhc_v >= 80
                ? "production_ready"
                : evalResult.dhc_v >= 50
                  ? "experimental"
                  : "unsafe"}
            </span>
          </h3>
          <div>
            functionality {evalResult.functionality.toFixed(1)} ·
            security {evalResult.security.toFixed(1)} · unit pass{" "}
            {(evalResult.unit_pass_rate * 100).toFixed(0)}% ·{" "}
            {evalResult.tests_passed} passed / {evalResult.tests_failed_or_errored}{" "}
            failed
          </div>
          <ul>
            {evalResult.findings.map((f, i) => (
              <li key={i}>
                <span
                  className="severity"
                  style={{
                    color:
                      f.severity === "critical"
                        ? "#f78166"
                        : f.severity === "high"
                          ? "#d29922"
                          : "#8b949e",
                  }}
                >
                  [{f.severity}]
                </span>{" "}
                {f.description}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
