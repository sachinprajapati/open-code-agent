import type { Mode } from "../runtime/protocolTypes";
export type ProviderDraft = { profile_id: string; display_name: string; base_url: string; default_model: string; request_timeout_seconds?: number };
export type McpServerDraft = { serverId: string; displayName: string; transport: "stdio"; command: string; arguments: string; workingDirectory: string; environmentVariables: string };
export type WebviewMessage = { type: "ready" } | { type: "sendPrompt"; text: string } | { type: "cancelRun" } | { type: "requestTimeout" } | { type: "selectProfile"; profileId: string } | { type: "selectModel"; modelId: string } | { type: "selectMode"; mode: Mode } | { type: "clearSession" } | { type: "newSession" } | { type: "restartRuntime" } | { type: "openSettings" } | { type: "openHistory" } | { type: "closeHistory" } | { type: "historySearch"; query: string } | { type: "resumeTask"; taskId: string } | { type: "renameTask"; taskId: string; title: string } | { type: "deleteTask"; taskId: string } | { type: "checkpointCompare"; checkpointId: string } | { type: "checkpointRestore"; checkpointId: string } | { type: "openProviderManagement" } | { type: "closeProviderManagement" } | { type: "openMcpManagement" } | { type: "closeMcpManagement" } | { type: "mcpSave"; server: McpServerDraft } | { type: "mcpDelete"; serverId: string } | { type: "mcpConnect"; serverId: string } | { type: "mcpRefreshTools"; serverId: string } | { type: "providerAdd" } | { type: "providerEdit"; profileId: string } | { type: "providerSave"; profile: ProviderDraft; apiKey?: string; replaceKey: boolean } | { type: "providerDelete"; profileId: string } | { type: "providerDefault"; profileId: string } | { type: "providerTest"; profileId: string } | { type: "providerModels"; profileId: string } | { type: "approvalDecision"; approvalId: string; fingerprint: string; approved: boolean };
export type ExtensionMessage = { type: "hydrateState"; state: unknown } | { type: "taskHistory"; tasks: unknown[] } | { type: "runtimeStateChanged"; state: string } | { type: "approvalRequested"; approval: unknown } | { type: "statusMessage"; message: string } | { type: "errorMessage"; message: string };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === "string" && value.length <= 100_000;
const profileId = (value: unknown): value is string => string(value) && /^[a-z0-9][a-z0-9_.-]*$/.test(value);
const draft = (value: unknown): ProviderDraft | undefined => {
  if (!record(value) || !profileId(value.profile_id) || !string(value.display_name) || !string(value.base_url) || !string(value.default_model)) return undefined;
  if (value.request_timeout_seconds !== undefined && (typeof value.request_timeout_seconds !== "number" || !Number.isFinite(value.request_timeout_seconds) || value.request_timeout_seconds <= 0)) return undefined;
  return value as ProviderDraft;
};
const mcpDraft = (value: unknown): McpServerDraft | undefined => !record(value) || !profileId(value.serverId) || !string(value.displayName) || !string(value.command) || !string(value.arguments) || !string(value.workingDirectory) || !string(value.environmentVariables) || value.transport !== "stdio" ? undefined : value as McpServerDraft;
export function parseWebviewMessage(value: unknown): WebviewMessage | undefined {
  if (!record(value) || !string(value.type)) return undefined;
  switch (value.type) {
    case "ready": case "cancelRun": case "requestTimeout": case "clearSession": case "newSession": case "restartRuntime": case "openSettings": case "openHistory": case "closeHistory": case "openProviderManagement": case "closeProviderManagement": case "openMcpManagement": case "closeMcpManagement": case "providerAdd": return { type: value.type };
    case "historySearch": return string(value.query) ? { type: value.type, query: value.query } : undefined;
    case "resumeTask": case "deleteTask": return string(value.taskId) ? { type: value.type, taskId: value.taskId } : undefined;
case "renameTask": return string(value.taskId) && string(value.title) && value.title.trim().length > 0 && value.title.trim().length <= 200 ? { type: value.type, taskId: value.taskId, title: value.title } : undefined;
    case "checkpointCompare": case "checkpointRestore": return string(value.checkpointId) ? { type: value.type, checkpointId: value.checkpointId } : undefined;
    case "sendPrompt": return string(value.text) ? { type: value.type, text: value.text } : undefined;
    case "selectProfile": return string(value.profileId) ? { type: value.type, profileId: value.profileId } : undefined;
    case "selectModel": return string(value.modelId) ? { type: value.type, modelId: value.modelId } : undefined;
    case "selectMode": return value.mode === "plan" || value.mode === "ask" || value.mode === "auto-edit" ? { type: value.type, mode: value.mode } : undefined;
    case "approvalDecision": return string(value.approvalId) && string(value.fingerprint) && typeof value.approved === "boolean" ? { type: value.type, approvalId: value.approvalId, fingerprint: value.fingerprint, approved: value.approved } : undefined;
    case "providerSave": { const profile = draft(value.profile); if (!profile || (value.apiKey !== undefined && !string(value.apiKey)) || typeof value.replaceKey !== "boolean") return undefined; return { type: value.type, profile, ...(value.apiKey !== undefined ? { apiKey: value.apiKey } : {}), replaceKey: value.replaceKey }; }
    case "mcpSave": { const server = mcpDraft(value.server); return server ? { type: value.type, server } : undefined; }
    case "mcpDelete": case "mcpConnect": case "mcpRefreshTools": return profileId(value.serverId) ? { type: value.type, serverId: value.serverId } : undefined;
    case "providerEdit": case "providerDelete": case "providerDefault": case "providerTest": case "providerModels": return profileId(value.profileId) ? { type: value.type, profileId: value.profileId } : undefined;
    default: return undefined;
  }
}
