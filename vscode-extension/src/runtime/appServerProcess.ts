import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { configuration, type ServerConfiguration } from "../configuration";
import type { Logger } from "../logging";
import { ProtocolError } from "./errors";
import { JsonRpcClient } from "./jsonRpcClient";
import { PROTOCOL_VERSION, REQUIRED_CAPABILITIES } from "./protocol.generated";
import type { ApprovalRequest, InitializeResult, Json, JsonObject } from "./protocolTypes";

export type RuntimeState = "stopped" | "starting" | "ready" | "stopping" | "failed";
export class AppServerProcess extends EventEmitter {
  private process: ChildProcessWithoutNullStreams | undefined;
  private rpc: JsonRpcClient | undefined;
  private state: RuntimeState = "stopped";
  private starting: Promise<void> | undefined;
  private restarts = 0;
  capabilities: Record<string, boolean> = {};
  constructor(private readonly logger: Logger, private readonly getConfig: () => ServerConfiguration = configuration) { super(); }
  get runtimeState(): RuntimeState { return this.state; }
  async start(): Promise<void> {
    if (this.state === "ready") return;
    if (this.starting) return this.starting;
    this.starting = this.startInternal().finally(() => { this.starting = undefined; }); return this.starting;
  }
  async restart(): Promise<void> {
    const max = this.getConfig().maxRestarts;
    if (this.restarts >= max) throw new ProtocolError(`Restart limit (${max}) reached.`);
    this.restarts += 1; await this.stop(); await this.start();
  }
  async request(method: string, params: JsonObject = {}): Promise<Json> {
    await this.start();
    if (!this.rpc) throw new ProtocolError("Runtime is unavailable.");
    this.logger.debug(`[rpc] → ${method}`);
    try {
      const result = await this.rpc.request(method, params);
      this.logger.debug(`[rpc] ← ${method}`);
      return result;
    } catch (error) {
      this.logger.error(`[rpc] ${method} failed: ${error instanceof Error ? error.message : "unknown error"}`);
      throw error;
    }
  }
  notify(method: string, params: JsonObject = {}): Promise<void> { return this.rpc ? this.rpc.notify(method, params) : Promise.reject(new ProtocolError("Runtime is unavailable.")); }
  async stop(): Promise<void> {
    const child = this.process; const rpc = this.rpc; if (!child) { this.transition("stopped"); return; }
    this.transition("stopping");
    try { if (rpc) { await rpc.request("shutdown", {}, this.getConfig().shutdownTimeoutMs).catch(() => undefined); await rpc.notify("exit").catch(() => undefined); } } finally {
      await this.waitForExit(child, this.getConfig().shutdownTimeoutMs);
      rpc?.disconnect("Runtime stopped."); this.rpc = undefined; this.process = undefined; this.transition("stopped");
    }
  }
  dispose(): Promise<void> { return this.stop(); }
  private async startInternal(): Promise<void> {
    const config = this.getConfig(); this.transition("starting");
    try {
      const child = spawn(config.executable, config.arguments, { cwd: config.cwd, shell: false, stdio: "pipe", windowsHide: true });
      this.process = child; const rpc = new JsonRpcClient(child.stdin, 30_000, (message) => this.logger.debug(message)); this.rpc = rpc;
      child.stdout.on("data", (data: Buffer) => rpc.accept(data));
      child.stderr.on("data", (data: Buffer) => this.logger.info(`server stderr: ${data.toString().trim()}`));
      child.on("error", (error) => this.fail(`Unable to start runtime: ${error.message}`));
      child.on("exit", (code, signal) => { rpc.disconnect(`Runtime exited (${code ?? "none"}/${signal ?? "none"}).`); if (this.state !== "stopping" && this.state !== "stopped") this.fail(`Runtime exited unexpectedly (${code ?? "none"}/${signal ?? "none"}).`); });
      rpc.on("notification", (method: string, params: JsonObject) => this.emit("notification", method, params));
      rpc.registerRequestHandler("approval/request", async (params) => { this.emit("approval", params); return await new Promise<Json>((resolve) => this.once(`approval:${String(params.approval_id)}`, resolve)); });
      const initialized = await rpc.request("initialize", { client_name: "OpenCodeAgent VS Code", client_version: "0.1.0", supported_protocol_versions: [PROTOCOL_VERSION], client_capabilities: { approvals: true, streaming_events: true, cancellation: true } }, config.startupTimeoutMs) as unknown as InitializeResult;
      if (initialized.selected_protocol_version !== PROTOCOL_VERSION) throw new ProtocolError("The app server selected an incompatible protocol version.");
      for (const capability of Object.keys(REQUIRED_CAPABILITIES)) if (!initialized.capabilities?.[capability]) throw new ProtocolError(`The app server lacks required capability: ${capability}`);
      this.capabilities = initialized.capabilities; await rpc.notify("initialized"); this.logger.info(`Ready: ${initialized.server_name} ${initialized.server_version}, protocol ${initialized.selected_protocol_version}.`); this.transition("ready");
    } catch (error) { this.rpc?.disconnect("Runtime startup failed."); this.process?.kill(); this.rpc = undefined; this.process = undefined; this.fail(error instanceof Error ? error.message : "Runtime startup failed."); throw error; }
  }
  respondApproval(id: string, result: Json): void { this.emit(`approval:${id}`, result); }
  private transition(state: RuntimeState): void { this.state = state; this.emit("state", state); }
  private fail(message: string): void { this.logger.error(message); this.transition("failed"); }
  private async waitForExit(child: ChildProcessWithoutNullStreams, timeout: number): Promise<void> {
    if (child.exitCode !== null) return;
    await Promise.race([new Promise<void>((resolve) => child.once("exit", () => resolve())), new Promise<void>((resolve) => setTimeout(resolve, timeout))]);
    if (child.exitCode === null) { this.logger.info("Runtime did not exit in time; terminating it."); child.kill(); await new Promise<void>((resolve) => child.once("exit", () => resolve())); }
  }
}
