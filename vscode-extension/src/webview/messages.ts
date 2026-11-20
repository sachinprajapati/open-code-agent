import type { Mode } from "../runtime/protocolTypes";
export type WebviewMessage = { type: "ready" } | { type: "sendPrompt"; text: string } | { type: "cancelRun" } | { type: "requestTimeout" } | { type: "selectProfile"; profileId: string } | { type: "selectModel"; modelId: string } | { type: "selectMode"; mode: Mode } | { type: "clearSession" } | { type: "newSession" } | { type: "restartRuntime" } | { type: "openSettings" } | { type: "approvalDecision"; approvalId: string; fingerprint: string; approved: boolean };
export type ExtensionMessage = { type: "hydrateState"; state: unknown } | { type: "runtimeStateChanged"; state: string } | { type: "approvalRequested"; approval: unknown } | { type: "statusMessage"; message: string } | { type: "errorMessage"; message: string };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === "string" && value.length <= 100_000;
export function parseWebviewMessage(value: unknown): WebviewMessage | undefined {
  if (!record(value) || !string(value.type)) return undefined;
  switch (value.type) {
    case "ready": case "cancelRun": case "requestTimeout": case "clearSession": case "newSession": case "restartRuntime": case "openSettings": return { type: value.type };
    case "sendPrompt": return string(value.text) ? { type: value.type, text: value.text } : undefined;
    case "selectProfile": return string(value.profileId) ? { type: value.type, profileId: value.profileId } : undefined;
    case "selectModel": return string(value.modelId) ? { type: value.type, modelId: value.modelId } : undefined;
    case "selectMode": return value.mode === "plan" || value.mode === "ask" || value.mode === "auto-edit" ? { type: value.type, mode: value.mode } : undefined;
    case "approvalDecision": return string(value.approvalId) && string(value.fingerprint) && typeof value.approved === "boolean" ? { type: value.type, approvalId: value.approvalId, fingerprint: value.fingerprint, approved: value.approved } : undefined;
    default: return undefined;
  }
}
