import { PassThrough } from "node:stream";
import { describe, expect, it } from "vitest";
import { JsonRpcClient } from "../../src/runtime/jsonRpcClient";
describe("JsonRpcClient", () => {
  it("correlates requests and handles notifications", async () => { const stream = new PassThrough(); const rpc = new JsonRpcClient(stream); const notices: string[] = []; rpc.on("notification", (method: string) => notices.push(method)); const pending = rpc.request("profiles/list"); await new Promise((resolve) => setImmediate(resolve)); const sent = stream.read().toString(); expect(sent).toContain("client:1"); rpc.accept('{"jsonrpc":"2.0","method":"agent/event","params":{}}\n'); rpc.accept('{"jsonrpc":"2.0","id":"client:1","result":{"ok":true}}\n'); await expect(pending).resolves.toEqual({ ok: true }); expect(notices).toEqual(["agent/event"]); });
  it("returns method not found for an unknown server request", async () => { const stream = new PassThrough(); const rpc = new JsonRpcClient(stream); rpc.accept('{"jsonrpc":"2.0","id":"server:1","method":"unknown"}\n'); await new Promise((resolve) => setImmediate(resolve)); expect(stream.read().toString()).toContain("-32601"); });
  it("rejects pending requests when disconnected", async () => { const stream = new PassThrough(); const rpc = new JsonRpcClient(stream); const pending = rpc.request("x"); rpc.disconnect(); await expect(pending).rejects.toThrow("closed"); });
});
