import * as vscode from "vscode";
import type { ChatViewProvider } from "./webview/chatViewProvider";
import type { SessionController } from "./sessions/sessionController";
import type { AppServerProcess } from "./runtime/appServerProcess";
import type { Logger } from "./logging";
import type { RuntimeResolution } from "./runtime/resolveRuntime";
import { arch, platform } from "node:process";
import { spawn } from "node:child_process";
export function registerCommands(context: vscode.ExtensionContext, view: ChatViewProvider, sessions: SessionController, runtime: AppServerProcess, logger: Logger, resolution: RuntimeResolution): void {
  const command = (id: string, action: () => PromiseLike<void> | void) => context.subscriptions.push(vscode.commands.registerCommand(id, () => { void (async () => { try { await action(); } catch (error) { view.error(error instanceof Error ? error.message : "OpenCodeAgent command failed."); } })(); }));
  command("openCodeAgent.openChat", () => { void vscode.commands.executeCommand("workbench.view.extension.openCodeAgent"); view.reveal(); });
  command("openCodeAgent.diagnostics", async () => {
    const mode = resolution.mode;
    let health = runtime.runtimeState === "ready" ? "healthy: app-server initialized" : `state: ${runtime.runtimeState}`;
    if (mode === "bundled" && resolution.executable) health = await new Promise<string>((resolve) => {
      const child = spawn(resolution.executable, ["--runtime-info"], { shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
      let output = ""; let error = "";
      child.stdout.on("data", (data: Buffer) => { output += data.toString(); }); child.stderr.on("data", (data: Buffer) => { error += data.toString(); });
      const timer = setTimeout(() => { child.kill(); resolve("health probe timed out"); }, 5000);
      child.once("error", (cause) => { clearTimeout(timer); resolve(`health probe failed: ${cause.message}`); });
      child.once("exit", (code) => { clearTimeout(timer); resolve(code === 0 ? output.trim() : `health probe exited ${code}: ${error.trim()}`); });
    });
    const details = [`Extension version: ${String(context.extension.packageJSON.version ?? "unknown")}`, `Platform / architecture: ${platform}/${arch}`, `Runtime mode: ${mode}`, `Runtime path: ${resolution.executable || "unavailable"}`, `Runtime health: ${health}`].join("\n");
    logger.info(details.replaceAll("\n", " | "));
    void vscode.window.showInformationMessage(details, { modal: true });
  });
  command("openCodeAgent.newSession", () => sessions.newSession()); command("openCodeAgent.clearSession", () => sessions.clear()); command("openCodeAgent.cancelRun", () => sessions.cancel()); command("openCodeAgent.restartRuntime", () => sessions.restart()); command("openCodeAgent.refreshProfiles", () => sessions.refreshProfiles()); command("openCodeAgent.showOutput", () => logger.channel.show(true)); command("openCodeAgent.openSettings", () => vscode.commands.executeCommand("workbench.action.openSettings", "@ext:open-code-agent"));
  context.subscriptions.push({ dispose: () => void runtime.dispose() });
}
