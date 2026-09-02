import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SettingsModal } from "../components/SettingsModal";
import type { ModelOption } from "../components/ModelSelect";

const MODELS: ModelOption[] = [
  {
    id: "openai/gpt-4o-mini",
    name: "GPT-4o mini",
    provider: "openai",
    context_length: 128000,
    pricing_input: 0.15,
    pricing_output: 0.6,
    capabilities: ["chat"],
  },
  {
    id: "openai/gpt-4.1",
    name: "GPT-4.1",
    provider: "openai",
    context_length: 128000,
    pricing_input: 2.0,
    pricing_output: 8.0,
    capabilities: ["chat"],
  },
  {
    id: "anthropic/claude-3-5-sonnet-latest",
    name: "Claude 3.5 Sonnet",
    provider: "anthropic",
    context_length: 200000,
    pricing_input: 3.0,
    pricing_output: 15.0,
    capabilities: ["chat"],
  },
  {
    id: "openrouter/auto",
    name: "Auto",
    provider: "openrouter",
    context_length: 128000,
    pricing_input: 1.0,
    pricing_output: 3.0,
    capabilities: ["chat"],
  },
];

function makeFetch(handlers: Record<string, (init?: RequestInit) => Response>) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const h = handlers[key];
    if (!h) throw new Error(`unexpected fetch: ${key}`);
    return h(init);
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function emptyResponse(status = 204): Response {
  return new Response(null, { status });
}

describe("<SettingsModal />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders providers when open", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [] }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    await waitFor(() => {
      expect(screen.getByText("openai")).toBeInTheDocument();
      expect(screen.getByText("anthropic")).toBeInTheDocument();
      expect(screen.getByText("openrouter")).toBeInTheDocument();
    });
  });

  it("shows 'Key saved' badge for already-saved providers", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          names: ["llm_provider_openai_gpt-4o-mini", "llm_provider_anthropic_claude-3-5-sonnet-latest"],
        }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    await waitFor(() => {
      expect(screen.getByTestId("saved-openai")).toHaveTextContent(/Key saved/);
      expect(screen.getByTestId("saved-anthropic")).toHaveTextContent(/Key saved/);
    });
  });

  it("save calls POST /api/secrets with the right name + value", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/secrets" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ names: [] });
      }
      if (url === "/api/secrets" && init?.method === "POST") {
        return emptyResponse(204);
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    });
    globalThis.fetch = f as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    await waitFor(() => screen.getByText("openai"));
    const openaiInput = screen.getAllByPlaceholderText(/Enter openai API key/)[0];
    fireEvent.change(openaiInput, { target: { value: "sk-test-1234" } });
    fireEvent.click(screen.getAllByText("Save")[0]);
    await waitFor(() => {
      const post = calls.find((c) => c.url === "/api/secrets" && c.init?.method === "POST");
      expect(post).toBeDefined();
      const body = JSON.parse(String(post!.init!.body));
      expect(body.name).toBe("llm_provider_openai_gpt-4o-mini");
      expect(body.value).toBe("sk-test-1234");
    });
  });

  it("delete calls DELETE /api/secrets/{name}", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/secrets" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ names: ["llm_provider_openai_gpt-4o-mini"] });
      }
      if (url.startsWith("/api/secrets/") && init?.method === "DELETE") {
        return emptyResponse(204);
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    });
    globalThis.fetch = f as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    await waitFor(() => screen.getByTestId("saved-openai"));
    fireEvent.click(screen.getAllByText("Delete")[0]);
    await waitFor(() => {
      const del = calls.find((c) => c.url.startsWith("/api/secrets/") && c.init?.method === "DELETE");
      expect(del).toBeDefined();
      expect(del!.url).toContain("llm_provider_openai_gpt-4o-mini");
    });
  });
});
