import { describe, expect, it, vi } from "vitest";

vi.mock("vscode", () => ({ Uri: { joinPath: vi.fn(), }, }));

import { html } from "../../src/webview/html";

describe("MCP settings webview", () => {
  it("renders a reachable MCP Servers settings view and complete add form entry point", () => {
    const webview = { cspSource: "vscode-webview", asWebviewUri: vi.fn(() => "asset") } as never;
    const markup = html(webview, {} as never);
    expect(markup).toContain("⚙ Settings");
    expect(markup).toContain('id="open-mcp-servers"');
    expect(markup).toContain(">MCP Servers<");
    expect(markup).toContain('id="add-mcp-server"');
    expect(markup).toContain("Add MCP Server");
    expect(markup).toContain("No MCP servers configured");
    expect(markup).toContain("Back to Provider Settings");
  });
});
