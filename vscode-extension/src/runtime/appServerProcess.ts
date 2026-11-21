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
type SpawnProcess = typeof spawn;
type ProcessGroupSignal = (pid: number, signal: NodeJS.Signals) => void;
type ProcessGroupAlive = (pid: number) => boolean;

export class AppServerProcess extends EventEmitter {
  private process: ChildProcessWithoutNullStreams | undefined;
  private rpc: JsonRpcClient | undefined;
  private state: RuntimeState = "stopped";
  private starting: Promise<void> | undefined;
  private stopping: Promise<void> | undefined;
  private restarts = 0;
  private readonly approvalWaiters = new Set<(result: Json) => void>();
  capabilities: Record<string, boolean> = {};
  constructor(
    private readonly logger: Logger,
    private readonly getConfig: () => ServerConfiguration = configuration,
    private readonly spawnProcess: SpawnProcess = spawn,
    private readonly platform = process.platform,
    private readonly signalProcessGroup: ProcessGroupSignal = (pid, signal) => process.kill(-pid, signal),
    private readonly processGroupAlive: ProcessGroupAlive = (pid) => {
      try { process.kill(-pid, 0); return true; }
      catch (error) { return (error as NodeJS.ErrnoException).code === "EPERM"; }
    },
  ) { super(); }
  get runtimeState(): RuntimeState { return this.state; }
  async start(): Promise<void> {
    if (this.state === "ready") return;
    if (this.starting) return this.starting;
    if (this.process && this.isAlive(this.process)) throw new ProtocolError("Runtime is already starting or stopping.");
    if (this.process) this.clearExitedProcess();
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
  registerServerRequestHandler(method: string, handler: (params: JsonObject) => Promise<Json | undefined>): void {
    this.rpc?.registerRequestHandler(method, handler);
  }
  notify(method: string, params: JsonObject = {}): Promise<void> { return this.rpc ? this.rpc.notify(method, params) : Promise.reject(new ProtocolError("Runtime is unavailable.")); }
  async stop(): Promise<void> {
    if (this.stopping) return this.stopping;
    this.stopping = this.stopInternal().finally(() => { this.stopping = undefined; });
    return this.stopping;
  }
  async dispose(): Promise<void> {
    for (const resolve of this.approvalWaiters) resolve(null);
    this.approvalWaiters.clear();
    await this.stop();
    this.restarts = 0;
    this.removeAllListeners();
  }
  private async startInternal(): Promise<void> {
    const config = this.getConfig(); this.transition("starting");
    try {
      // Detached gives this extension-owned server a separate Unix process group.
      // It lets cleanup reach uv/Python descendants without affecting CLI commands.
      const child = this.spawnProcess(config.executable, config.arguments, { cwd: config.cwd, shell: false, stdio: "pipe", windowsHide: true, detached: this.platform !== "win32" });
      this.process = child; const rpc = new JsonRpcClient(child.stdin, 30_000, (message) => this.logger.debug(message)); this.rpc = rpc;
      child.stdout.on("data", (data: Buffer) => rpc.accept(data));
      child.stderr.on("data", (data: Buffer) => this.logger.info(`server stderr: ${data.toString().trim()}`));
      child.on("error", (error) => this.fail(`Unable to start runtime: ${error.message}`));
      child.on("exit", (code, signal) => { rpc.disconnect(`Runtime exited (${code ?? "none"}/${signal ?? "none"}).`); if (this.state !== "stopping" && this.state !== "stopped") this.fail(`Runtime exited unexpectedly (${code ?? "none"}/${signal ?? "none"}).`); });
      rpc.on("notification", (method: string, params: JsonObject) => this.emit("notification", method, params));
      rpc.registerRequestHandler("approval/request", async (params) => {
        this.emit("approval", params);
        return await new Promise<Json>((resolve) => {
          const event = `approval:${String(params.approval_id)}`;
          const finish = (result: Json) => { this.approvalWaiters.delete(finish); this.removeListener(event, finish); resolve(result); };
          this.approvalWaiters.add(finish);
          this.once(event, finish);
        });
      });
      this.emit("rpcReady", rpc);
      const initialized = await rpc.request("initialize", { client_name: "OpenCodeAgent VS Code", client_version: "0.1.0", supported_protocol_versions: [PROTOCOL_VERSION], client_capabilities: { approvals: true, streaming_events: true, cancellation: true } }, config.startupTimeoutMs) as unknown as InitializeResult;
      if (initialized.selected_protocol_version !== PROTOCOL_VERSION) throw new ProtocolError("The app server selected an incompatible protocol version.");
      for (const capability of Object.keys(REQUIRED_CAPABILITIES)) if (!initialized.capabilities?.[capability]) throw new ProtocolError(`The app server lacks required capability: ${capability}`);
      this.capabilities = initialized.capabilities; await rpc.notify("initialized"); this.logger.info(`Ready: ${initialized.server_name} ${initialized.server_version}, protocol ${initialized.selected_protocol_version}.`); this.transition("ready");
    } catch (error) {
      const child = this.process;
      this.rpc?.dispose(); this.rpc = undefined;
      if (child) await this.terminate(child, this.getConfig().shutdownTimeoutMs);
      this.clearChild(child); this.process = undefined;
      this.fail(error instanceof Error ? error.message : "Runtime startup failed."); throw error;
    }
  }
  respondApproval(id: string, result: Json): void { this.emit(`approval:${id}`, result); }
  private transition(state: RuntimeState): void { this.state = state; this.emit("state", state); }
  private fail(message: string): void { this.logger.error(message); this.transition("failed"); }
  private async stopInternal(): Promise<void> {
    const child = this.process; const rpc = this.rpc;
    if (!child) { rpc?.dispose(); this.rpc = undefined; this.capabilities = {}; this.transition("stopped"); return; }
    this.transition("stopping");
    try {
      // The protocol shutdown lets the server close sessions and reap its own
      // workers before we fall back to signals for the whole owned group.
      if (this.rpc && this.isAlive(child)) {
        try { await this.rpc.request("shutdown", {}, Math.min(500, this.getConfig().shutdownTimeoutMs)); }
        catch { /* The server may already be exiting or the connection may be broken. */ }
      }
      child.stdin.end();
      child.stdout.destroy();
      child.stderr.destroy();
      await this.terminate(child, this.getConfig().shutdownTimeoutMs);
    }
    finally {
      rpc?.dispose(); this.clearChild(child);
      if (this.process === child) this.process = undefined;
      if (this.rpc === rpc) this.rpc = undefined;
      this.capabilities = {}; this.transition("stopped");
    }
  }
  private async terminate(child: ChildProcessWithoutNullStreams, timeout: number): Promise<void> {
    if (!this.isOwnedProcessAlive(child)) return;
    this.signal(child, "SIGTERM");
    if (await this.waitForOwnedProcessExit(child, timeout)) return;
    this.logger.info("Runtime did not exit after SIGTERM; sending SIGKILL.");
    if (this.platform === "win32" && child.pid) this.killWindowsProcessTree(child.pid);
    else this.signal(child, "SIGKILL");
    await this.waitForOwnedProcessExit(child, timeout);
  }
  private killWindowsProcessTree(pid: number): void {
    const killer = this.spawnProcess("taskkill", ["/pid", String(pid), "/T", "/F"], { shell: false, stdio: "ignore", windowsHide: true });
    killer.on("error", (error) => this.logger.debug(`Could not terminate runtime process tree: ${error.message}`));
  }
  private signal(child: ChildProcessWithoutNullStreams, signal: NodeJS.Signals): void {
    try {
      if (this.platform !== "win32" && child.pid) this.signalProcessGroup(child.pid, signal);
      else child.kill(signal);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") this.logger.debug(`Could not send ${signal}: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  }
  private isOwnedProcessAlive(child: ChildProcessWithoutNullStreams): boolean {
    if (this.isAlive(child)) return true;
    if (this.platform === "win32" || !child.pid) return false;
    return this.processGroupAlive(child.pid);
  }
  private async waitForOwnedProcessExit(child: ChildProcessWithoutNullStreams, timeout: number): Promise<boolean> {
    const deadline = Date.now() + timeout;
    while (this.isOwnedProcessAlive(child) && Date.now() < deadline) {
      await new Promise<void>((resolve) => setTimeout(resolve, Math.min(50, Math.max(1, deadline - Date.now()))));
    }
    return !this.isOwnedProcessAlive(child);
  }
  private async waitForExit(child: ChildProcessWithoutNullStreams, timeout: number): Promise<boolean> {
    if (!this.isAlive(child)) return true;
    return new Promise<boolean>((resolve) => {
      const onExit = () => { clearTimeout(timer); resolve(true); };
      const timer = setTimeout(() => { child.removeListener("exit", onExit); resolve(!this.isAlive(child)); }, timeout);
      child.once("exit", onExit);
    });
  }
  private isAlive(child: ChildProcessWithoutNullStreams): boolean { return child.exitCode === null && child.signalCode === null; }
  private clearExitedProcess(): void {
    this.rpc?.dispose(); this.rpc = undefined;
    this.clearChild(this.process); this.process = undefined;
    this.capabilities = {};
  }
  private clearChild(child: ChildProcessWithoutNullStreams | undefined): void {
    if (!child) return;
    child.removeAllListeners();
    child.stdin.removeAllListeners(); child.stdout.removeAllListeners(); child.stderr.removeAllListeners();
    child.stdin.destroy(); child.stdout.destroy(); child.stderr.destroy();
  }
}
