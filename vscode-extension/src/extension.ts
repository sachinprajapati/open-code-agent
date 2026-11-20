import * as vscode from "vscode";
import { basename, dirname, join } from "node:path";
import { ApprovalController } from "./approvals/approvalController";
import { registerCommands } from "./commands";
import { configuration } from "./configuration";
import { Logger } from "./logging";
import { AppServerProcess } from "./runtime/appServerProcess";
import { SessionController } from "./sessions/sessionController";
import { ChatViewProvider } from "./webview/chatViewProvider";

let shutdown: (() => Promise<void>) | undefined;
export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const logger = new Logger(() => configuration().debug); const primaryWorkspace = vscode.workspace.workspaceFolders?.[0]; const developmentRoot = context.extensionMode === vscode.ExtensionMode.Development ? dirname(context.extensionUri.fsPath) : undefined; const diagnosticRoot = primaryWorkspace?.uri.scheme === "file" ? primaryWorkspace.uri.fsPath : developmentRoot; if (diagnosticRoot) await logger.enableFile(join(diagnosticRoot, ".open-code-agent", "extension.log")); const developmentWorkspace = developmentRoot ? { uri: vscode.Uri.file(developmentRoot), name: basename(developmentRoot), index: 0 } : undefined; const runtime = new AppServerProcess(logger); const sessions = new SessionController(runtime, logger, developmentWorkspace);
  let view: ChatViewProvider; const approvals = new ApprovalController(runtime, (approval) => view.approval(approval), (message) => view.status(message)); view = new ChatViewProvider(context.extensionUri, sessions, approvals); sessions.setApprovals(approvals);
  runtime.on("approval", (approval) => { approvals.request(approval); });
  context.subscriptions.push(logger, sessions, view, vscode.window.registerWebviewViewProvider("openCodeAgent.chatView", view, { webviewOptions: { retainContextWhenHidden: true } })); registerCommands(context, view, sessions, runtime, logger);
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100); status.command = "openCodeAgent.openChat"; status.text = "$(hubot) OpenCodeAgent: starting"; context.subscriptions.push(status); status.show();
  runtime.on("state", (state: string) => { status.text = `$(hubot) OpenCodeAgent: ${state}`; });
  try { await runtime.start(); await sessions.refreshProfiles(); } catch (error) { view.error(error instanceof Error ? error.message : "OpenCodeAgent runtime could not start."); logger.error("Configure openCodeAgent.server.executable and server.arguments, then restart the runtime."); }
  shutdown = async () => { approvals.failClosed(); await sessions.disposeAsync(); await runtime.dispose(); };
}
export async function deactivate(): Promise<void> { await shutdown?.(); shutdown = undefined; }
