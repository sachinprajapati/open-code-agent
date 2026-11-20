import * as vscode from "vscode";
import type { ChatViewProvider } from "./webview/chatViewProvider";
import type { SessionController } from "./sessions/sessionController";
import type { AppServerProcess } from "./runtime/appServerProcess";
import type { Logger } from "./logging";
export function registerCommands(context: vscode.ExtensionContext, view: ChatViewProvider, sessions: SessionController, runtime: AppServerProcess, logger: Logger): void {
  const command = (id: string, action: () => PromiseLike<void> | void) => context.subscriptions.push(vscode.commands.registerCommand(id, () => { void (async () => { try { await action(); } catch (error) { view.error(error instanceof Error ? error.message : "OpenCodeAgent command failed."); } })(); }));
  command("openCodeAgent.openChat", () => { void vscode.commands.executeCommand("workbench.view.extension.openCodeAgent"); view.reveal(); });
  command("openCodeAgent.newSession", () => sessions.newSession()); command("openCodeAgent.clearSession", () => sessions.clear()); command("openCodeAgent.cancelRun", () => sessions.cancel()); command("openCodeAgent.restartRuntime", () => sessions.restart()); command("openCodeAgent.refreshProfiles", () => sessions.refreshProfiles()); command("openCodeAgent.showOutput", () => logger.channel.show(true)); command("openCodeAgent.openSettings", () => vscode.commands.executeCommand("workbench.action.openSettings", "@ext:open-code-agent"));
  context.subscriptions.push({ dispose: () => void runtime.dispose() });
}
