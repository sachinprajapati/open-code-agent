import { describe, expect, it } from "vitest";
import { parseWebviewMessage } from "../../src/webview/messages";
describe("webview messages", () => { it("accepts only the narrow action union", () => { expect(parseWebviewMessage({ type: "sendPrompt", text: "hello" })).toEqual({ type: "sendPrompt", text: "hello" }); expect(parseWebviewMessage({ type: "requestTimeout" })).toEqual({ type: "requestTimeout" }); expect(parseWebviewMessage({ type: "rpc", method: "shutdown" })).toBeUndefined(); expect(parseWebviewMessage({ type: "selectMode", mode: "danger" })).toBeUndefined(); }); });
