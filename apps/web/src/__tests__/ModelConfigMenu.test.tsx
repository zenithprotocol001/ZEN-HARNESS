import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ModelConfigMenu } from "../components/ModelConfigMenu";

describe("<ModelConfigMenu />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the config on open and shows the current values", async () => {
    globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/sessions/abc/config" && (init?.method ?? "GET") === "GET") {
        return new Response(
          JSON.stringify({
            temperature: 0.42,
            max_tokens: 1234,
            top_p: 0.77,
            system_prompt: "be terse",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    }) as unknown as typeof fetch;
    render(
      <ModelConfigMenu sessionID="abc" isOpen={true} onClose={() => {}} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("cfg-temperature-input").value).toBe("0.42");
      expect(screen.getByTestId("cfg-max-tokens-input").value).toBe("1234");
      expect(screen.getByTestId("cfg-top-p-input").value).toBe("0.77");
    });
    expect(screen.getByTestId("cfg-system-prompt-input").value).toBe("be terse");
  });

  it("falls back to defaults when GET returns 404", async () => {
    globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/sessions/abc/config" && (init?.method ?? "GET") === "GET") {
        return new Response("not found", { status: 404 });
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    }) as unknown as typeof fetch;
    render(
      <ModelConfigMenu sessionID="abc" isOpen={true} onClose={() => {}} />,
    );
    // Defaults shown even when fetch fails.
    expect(screen.getByTestId("cfg-temperature-input").value).toBe("0.7");
  });

  it("save POSTs the config and closes", async () => {
    let onCloseCalls = 0;
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/sessions/abc/config" && (init?.method ?? "GET") === "GET") {
        return new Response(
          JSON.stringify({
            temperature: 0.7,
            max_tokens: 4096,
            top_p: 1.0,
            system_prompt: "x",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === "/api/sessions/abc/config" && init?.method === "POST") {
        return new Response(null, { status: 204 });
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    });
    globalThis.fetch = f as unknown as typeof fetch;
    const onClose = () => {
      onCloseCalls += 1;
    };
    render(
      <ModelConfigMenu sessionID="abc" isOpen={true} onClose={onClose} />,
    );
    await waitFor(() => screen.getByTestId("cfg-save"));
    fireEvent.click(screen.getByTestId("cfg-save"));
    await waitFor(() => {
      const post = f.mock.calls.find(
        (c) => c[1]?.method === "POST" && c[0] === "/api/sessions/abc/config",
      );
      expect(post).toBeDefined();
      expect(onCloseCalls).toBe(1);
    });
  });
});
