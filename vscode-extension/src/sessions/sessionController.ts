import * as vscode from "vscode";
import type { ApprovalController } from "../approvals/approvalController";
import { defaultMode } from "../configuration";
import type { Logger } from "../logging";
import type { AppServerProcess, RuntimeState } from "../runtime/appServerProcess";
import type { AgentEvent, JsonObject, Mode, Model, Profile } from "../runtime/protocolTypes";
import { initialState, type SessionState } from "./state";
import { beginCancellation, beginRequest, finishRequest, setProgress } from "./requestState";

const isObject = (value: unknown): value is JsonObject => typeof value === "object" && value !== null && !Array.isArray(value);
export class SessionController implements vscode.Disposable {
  private state: SessionState = initialState(defaultMode());
  private workspace: vscode.WorkspaceFolder | undefined;
  private lastSequence = 0;
  // JSON-RPC responses and notifications can be delivered in the same stdout
  // chunk. Buffer events that arrive before turn/start's response supplies the
  // run ID, otherwise the sequence guard would discard the whole run.
  private pendingRunEvents: AgentEvent[] = [];
  private mcpServers: JsonObject[] = [];
  private approvalController: ApprovalController | undefined;
  private readonly emitter = new vscode.EventEmitter<SessionState>();
  readonly onDidChange = this.emitter.event;
  constructor(private readonly runtime: AppServerProcess, private readonly logger: Logger, private readonly developmentWorkspace?: vscode.WorkspaceFolder) {
    runtime.on("state", (state: RuntimeState) => { this.state.runtime = state; if (state !== "ready") this.invalidate(); this.publish(); });
    runtime.on("notification", (method: string, params: JsonObject) => { if (method === "agent/event") this.event(params); });
    vscode.workspace.onDidChangeWorkspaceFolders(() => { void this.closeForWorkspaceChange(); });
  }
  setApprovals(controller: ApprovalController): void { this.approvalController = controller; }
  async setMcpServers(servers: JsonObject[]): Promise<void> { this.requireIdle(); this.mcpServers = structuredClone(servers); await this.closeSession(); this.publish(); }
  snapshot(): SessionState { return structuredClone(this.state); }
  async refreshProfiles(): Promise<void> {
    const result = await this.runtime.request("profiles/list"); if (!isObject(result) || !Array.isArray(result.profiles)) throw new Error("Invalid profiles response.");
    this.state.profiles = result.profiles.filter(isObject).map((item) => item as unknown as Profile);
    const configured = vscode.workspace.getConfiguration("openCodeAgent").get<string>("defaultProfile", "");
    this.state.profile = this.state.profiles.find((profile) => profile.profile_id === configured)?.profile_id ?? this.state.profiles.find((profile) => profile.is_default)?.profile_id ?? this.state.profiles[0]?.profile_id;
    await this.refreshModels(); this.publish();
  }
  async selectProfile(profile: string): Promise<void> { this.requireIdle(); this.state.profile = profile; this.state.model = undefined; await this.closeSession(); await this.refreshModels(); this.publish(); }
  async selectModel(model: string): Promise<void> { this.requireIdle(); this.state.model = model || undefined; await this.closeSession(); this.publish(); }
  async selectMode(mode: Mode): Promise<void> { this.requireIdle(); this.state.mode = mode; await this.closeSession(); this.publish(); }
  async send(text: string): Promise<void> {
    if (!text.trim()) return; this.requireIdle(); this.trace("Prompt accepted; preparing the agent session."); this.pendingRunEvents = []; this.lastSequence = 0; beginRequest(this.state); this.state.transcript.push({ kind: "user", text }); this.publish(); try { setProgress(this.state, "Preparing agent session..."); this.publish(); await this.ensureSession();
      // A Stop click during setup prevents the turn from ever reaching the agent.
      if (this.state.requestState !== "processing") { finishRequest(this.state); return; }
      setProgress(this.state, "Connecting to agent..."); this.publish(); const result = await this.runtime.request("turn/start", { session_id: this.state.sessionId!, text }); if (!isObject(result) || typeof result.run_id !== "string") throw new Error("Invalid turn response."); this.state.runId = result.run_id; this.lastSequence = 0; this.trace(`Run accepted by app server (${result.run_id}).`); this.flushPendingEvents(result.run_id);
      // The start RPC can be in flight when Stop is clicked; cancel as soon as it supplies a run ID.
      if (this.state.cancelling) { this.trace(`Cancelling run (${result.run_id}) after it started.`); await this.runtime.request("turn/cancel", { session_id: this.state.sessionId!, run_id: this.state.runId }); } else setProgress(this.state, "Waiting for the LLM response...");
    } catch (error) { this.pendingRunEvents = []; this.logger.error(`[session] Request failed: ${error instanceof Error ? error.message : "unknown error"}`); finishRequest(this.state); throw error; } finally { this.state.starting = false; this.publish(); }
  }
  async cancel(): Promise<void> { if (this.state.requestState === "idle" || this.state.cancelling) return; beginCancellation(this.state); this.approvalController?.failClosed(); this.publish(); if (!this.state.sessionId || !this.state.runId) return; try { await this.runtime.request("turn/cancel", { session_id: this.state.sessionId, run_id: this.state.runId }); } catch (error) { finishRequest(this.state); this.publish(); throw error; } }
  async timeout(): Promise<void> { if (!this.state.sessionId || !this.state.runId) { finishRequest(this.state); this.publish(); return; } const sessionId = this.state.sessionId; const runId = this.state.runId; this.approvalController?.failClosed(); finishRequest(this.state); this.publish(); await this.runtime.request("turn/cancel", { session_id: sessionId, run_id: runId }).catch(() => undefined); }
  async clear(): Promise<void> { this.requireIdle(); if (this.state.sessionId) await this.runtime.request("session/clear", { session_id: this.state.sessionId }); this.state.transcript = []; this.publish(); }
  async newSession(): Promise<void> { this.requireIdle(); await this.closeSession(); this.state.transcript = []; this.publish(); }
  async restart(): Promise<void> { this.approvalController?.failClosed(); await this.runtime.restart(); await this.refreshProfiles(); }
  async disposeAsync(): Promise<void> { this.approvalController?.failClosed(); await this.closeSession(); }
  dispose(): void { this.emitter.dispose(); }
  private async ensureSession(): Promise<void> {
    if (this.state.sessionId) return; this.trace("Selecting workspace for agent session."); this.workspace = await this.pickWorkspace(); if (!this.workspace) throw new Error("Open a workspace folder before starting an agent session."); if (!this.state.profile) throw new Error("Configure a provider profile with the OpenCodeAgent CLI first.");
    setProgress(this.state, "Creating agent session..."); this.publish(); this.trace(`Creating session for ${this.workspace.uri.fsPath}.`);
    const result = await this.runtime.request("session/create", { profile_id: this.state.profile, ...(this.state.model ? { model: this.state.model } : {}), mode: this.state.mode === "auto-edit" ? "auto_edit" : this.state.mode, workspace_path: this.workspace.uri.fsPath, mcp_servers: this.mcpServers });
    if (!isObject(result) || typeof result.session_id !== "string") throw new Error("Invalid session response."); this.state.sessionId = result.session_id; this.state.workspaceName = this.workspace.name; this.trace(`Session created for workspace ${this.workspace.name} (${result.session_id}).`);
  }
  private async pickWorkspace(): Promise<vscode.WorkspaceFolder | undefined> {
    const folders = vscode.workspace.workspaceFolders ?? [];
    if (folders.length < 1) {
      if (this.developmentWorkspace) this.trace(`VS Code supplied no workspace; using development workspace ${this.developmentWorkspace.uri.fsPath}.`);
      return this.developmentWorkspace;
    }
    if (this.workspace && folders.includes(this.workspace)) return this.workspace;
    // A Quick Pick is not reliably visible while this view owns focus in an
    // Extension Development Host. Do not leave a request pending indefinitely:
    // use VS Code's ordered primary workspace folder for this in-memory session.
    if (folders.length > 1) this.trace(`Multiple workspace folders found; using primary folder ${folders[0].uri.fsPath}.`);
    return folders[0];
  }
  private async refreshModels(): Promise<void> { if (!this.state.profile) { this.state.models = []; return; } const result = await this.runtime.request("models/list", { profile_id: this.state.profile }); if (!isObject(result) || !Array.isArray(result.models)) throw new Error("Invalid models response."); this.state.models = result.models.filter(isObject).map((item) => item as unknown as Model); const preferred = vscode.workspace.getConfiguration("openCodeAgent").get<string>("defaultModel", ""); this.state.model = this.state.models.find((model) => model.id === preferred)?.id ?? this.state.models[0]?.id; }
  private event(raw: JsonObject): void {
    const event = raw as unknown as AgentEvent;
    if (event.session_id !== this.state.sessionId) return;
    if (!this.state.runId) {
      if (this.state.requestState === "processing" || this.state.requestState === "cancelling") this.pendingRunEvents.push(event);
      return;
    }
    if (event.run_id !== this.state.runId) return;
    this.handleEvent(event);
  }
  private flushPendingEvents(runId: string): void {
    const pending = this.pendingRunEvents; this.pendingRunEvents = [];
    for (const event of pending) if (event.run_id === runId) this.handleEvent(event);
  }
  private handleEvent(event: AgentEvent): void {
    if (!Number.isInteger(event.sequence) || event.sequence !== this.lastSequence + 1) { this.logger.error("Ignored out-of-order agent event."); return; } this.lastSequence = event.sequence;
    this.trace(`Agent event: ${event.type} (#${event.sequence}).`);
    if (event.type === "assistant_text_delta" && typeof event.payload.text === "string") { const previous = this.state.transcript.at(-1); if (previous?.kind === "assistant") previous.text += event.payload.text; else this.state.transcript.push({ kind: "assistant", text: event.payload.text }); setProgress(this.state, "Streaming response..."); }
    else if (event.type === "reasoning_activity") { this.state.transcript.push({ kind: "activity", text: "Thinking…" }); setProgress(this.state, "Thinking..."); }
    else if (event.type === "tool_execution_started") { const tool = String(event.payload.tool_name ?? "agent tool"); this.state.transcript.push({ kind: "activity", text: `Running tool: ${tool}` }); setProgress(this.state, `Running tool: ${tool}`); }
    else if (event.type.startsWith("tool_") || event.type.includes("proposal")) { this.state.transcript.push({ kind: "activity", text: `${event.type.replaceAll("_", " ")}: ${String(event.payload.tool_name ?? "agent tool")}` }); setProgress(this.state, "Generating response..."); }
    else if (event.type === "run_started") setProgress(this.state, "Agent run started...");
    else if (event.type === "model_step_started") setProgress(this.state, "Request sent to LLM; waiting for response...");
    if (["run_completed", "run_failed", "run_limit_reached", "run_cancelled"].includes(event.type)) { if (event.type !== "run_completed") this.state.transcript.push({ kind: event.type === "run_cancelled" ? "activity" : "error", text: String(event.payload.message ?? event.payload.reason ?? event.type.replaceAll("_", " ")) }); finishRequest(this.state); this.approvalController?.failClosed(); }
    this.publish();
  }
  private requireIdle(): void { if (this.state.requestState !== "idle" || this.state.runId || this.state.starting) throw new Error("Wait for the active run to finish or cancel it first."); }
  private async closeSession(): Promise<void> { if (!this.state.sessionId) return; const id = this.state.sessionId; this.invalidate(); await this.runtime.request("session/close", { session_id: id }).catch(() => undefined); }
  private invalidate(): void { this.state.sessionId = undefined; finishRequest(this.state); this.lastSequence = 0; this.pendingRunEvents = []; }
  private async closeForWorkspaceChange(): Promise<void> { await this.closeSession(); this.workspace = undefined; this.publish(); }
  private trace(message: string): void { this.logger.debug(`[session] ${message}`); }
  private publish(): void { this.emitter.fire(this.snapshot()); }
}
