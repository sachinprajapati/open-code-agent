import type { Mode } from "../runtime/protocolTypes";
export type ProviderDraft = { profile_id: string; display_name: string; base_url: string; default_model: string; request_timeout_seconds?: number };
export type WebviewMessage = { type: "ready" } | { type: "sendPrompt"; text: string } | { type: "cancelRun" } | { type: "requestTimeout" } | { type: "selectProfile"; profileId: string } | { type: "selectModel"; modelId: string } | { type: "selectMode"; mode: Mode } | { type: "clearSession" } | { type: "newSession" } | { type: "restartRuntime" } | { type: "openSettings" } | { type: "openProviderManagement" } | { type: "closeProviderManagement" } | { type: "providerAdd" } | { type: "providerEdit"; profileId: string } | { type: "providerSave"; profile: ProviderDraft; apiKey?: string; replaceKey: boolean } | { type: "providerDelete"; profileId: string } | { type: "providerDefault"; profileId: string } | { type: "providerTest"; profileId: string } | { type: "providerModels"; profileId: string } | { type: "approvalDecision"; approvalId: string; fingerprint: string; approved: boolean };
export type ExtensionMessage = { type: "hydrateState"; state: unknown } | { type: "runtimeStateChanged"; state: string } | { type: "approvalRequested"; approval: unknown } | { type: "statusMessage"; message: string } | { type: "errorMessage"; message: string };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === "string" && value.length <= 100_000;
const profileId = (value: unknown): value is string => string(value) && /^[a-z0-9][a-z0-9_.-]*$/.test(value);
const draft = (value: unknown): ProviderDraft | undefined => {
  if (!record(value) || !profileId(value.profile_id) || !string(value.display_name) || !string(value.base_url) || !string(value.default_model)) return undefined;
  if (value.request_timeout_seconds !== undefined && (typeof value.request_timeout_seconds !== "number" || !Number.isFinite(value.request_timeout_seconds) || value.request_timeout_seconds <= 0)) return undefined;
  return value as ProviderDraft;
};
export function parseWebviewMessage(value: unknown): WebviewMessage | undefined {
  if (!record(value) || !string(value.type)) return undefined;
  switch (value.type) {
    case "ready": case "cancelRun": case "requestTimeout": case "clearSession": case "newSession": case "restartRuntime": case "openSettings": case "openProviderManagement": case "closeProviderManagement": case "providerAdd": return { type: value.type };
    case "sendPrompt": return string(value.text) ? { type: value.type, text: value.text } : undefined;
    case "selectProfile": return string(value.profileId) ? { type: value.type, profileId: value.profileId } : undefined;
    case "selectModel": return string(value.modelId) ? { type: value.type, modelId: value.modelId } : undefined;
    case "selectMode": return value.mode === "plan" || value.mode === "ask" || value.mode === "auto-edit" ? { type: value.type, mode: value.mode } : undefined;
    case "approvalDecision": return string(value.approvalId) && string(value.fingerprint) && typeof value.approved === "boolean" ? { type: value.type, approvalId: value.approvalId, fingerprint: value.fingerprint, approved: value.approved } : undefined;
    case "providerSave": { const profile = draft(value.profile); if (!profile || (value.apiKey !== undefined && !string(value.apiKey)) || typeof value.replaceKey !== "boolean") return undefined; return { type: value.type, profile, ...(value.apiKey !== undefined ? { apiKey: value.apiKey } : {}), replaceKey: value.replaceKey }; }
    case "providerEdit": case "providerDelete": case "providerDefault": case "providerTest": case "providerModels": return profileId(value.profileId) ? { type: value.type, profileId: value.profileId } : undefined;
    default: return undefined;
  }
}
