import * as vscode from "vscode";
import { basename, dirname, join } from "node:path";
import { ApprovalController } from "./approvals/approvalController";
import { registerCommands } from "./commands";
import { configuration } from "./configuration";
import { Logger } from "./logging";
import { AppServerProcess } from "./runtime/appServerProcess";
import type { JsonRpcClient } from "./runtime/jsonRpcClient";
import { SessionController } from "./sessions/sessionController";
import { ChatViewProvider } from "./webview/chatViewProvider";
import { resolveRuntime } from "./runtime/resolveRuntime";

let shutdown: (() => Promise<void>) | undefined;
export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const logger = new Logger(() => configuration().debug); const primaryWorkspace = vscode.workspace.workspaceFolders?.[0]; const developmentRoot = context.extensionMode === vscode.ExtensionMode.Development ? dirname(context.extensionUri.fsPath) : undefined; const diagnosticRoot = primaryWorkspace?.uri.scheme === "file" ? primaryWorkspace.uri.fsPath : developmentRoot; if (diagnosticRoot) await logger.enableFile(join(diagnosticRoot, ".open-code-agent", "extension.log")); const developmentWorkspace = developmentRoot ? { uri: vscode.Uri.file(developmentRoot), name: basename(developmentRoot), index: 0 } : undefined;
  let resolution;
  try { resolution = resolveRuntime(context, configuration()); }
  catch (error) { const message = error instanceof Error ? error.message : "OpenCodeAgent runtime is unavailable."; void vscode.window.showErrorMessage(message); logger.error(message); resolution = { mode: "bundled" as const, executable: "", arguments: [] }; }
  const runtime = new AppServerProcess(logger, () => ({ ...configuration(), executable: resolution.executable, arguments: resolution.arguments })); const sessions = new SessionController(runtime, logger, developmentWorkspace);
  runtime.on("rpcReady", (rpc: JsonRpcClient) => rpc.registerRequestHandler("secret/resolve", async (params) => {
    const reference = params.secret_ref;
    if (typeof reference !== "string" || !/^vscode:[a-z0-9][a-z0-9_.-]*$/.test(reference)) return {} as Record<string, never>;
    const secret = await context.secrets.get(`openCodeAgent.providerApiKey.${reference.slice("vscode:".length)}`);
    // A secret is returned only over the existing private stdio protocol to
    // satisfy this request; it is neither logged nor retained in webview state.
    return secret ? { secret } : {};
  }));
  let view: ChatViewProvider; const approvals = new ApprovalController(runtime, (approval) => view.approval(approval), (message) => view.status(message)); view = new ChatViewProvider(context.extensionUri, sessions, approvals, runtime, context.secrets); sessions.setApprovals(approvals);
  runtime.on("approval", (approval) => { approvals.request(approval); });
  context.subscriptions.push(logger, sessions, view, vscode.window.registerWebviewViewProvider("openCodeAgent.chatView", view, { webviewOptions: { retainContextWhenHidden: true } })); registerCommands(context, view, sessions, runtime, logger, resolution);
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100); status.command = "openCodeAgent.openChat"; status.text = "$(hubot) OpenCodeAgent: starting"; context.subscriptions.push(status); status.show();
  runtime.on("state", (state: string) => { status.text = `$(hubot) OpenCodeAgent: ${state}`; });
  let shutdownPromise: Promise<void> | undefined;
  const cleanup = () => {
    if (!shutdownPromise) shutdownPromise = (async () => {
      approvals.failClosed();
      // The app-server shutdown path closes active sessions itself. Stop its
      // owned process group first so a stalled session RPC cannot block host
      // deactivation and leave the bundled runtime running.
      await runtime.dispose();
      await sessions.disposeAsync().catch(() => undefined);
      sessions.dispose();
    })();
    return shutdownPromise;
  };
  shutdown = cleanup;
  // VS Code disposes subscriptions on reload/window close; deactivate awaits the
  // same idempotent cleanup when the host gives us that opportunity.
  context.subscriptions.push({ dispose: () => { void cleanup(); } });
  try { if (!resolution.executable) throw new Error("Install the VSIX matching your platform and architecture."); await runtime.start(); await sessions.refreshProfiles(); } catch (error) { view.error(error instanceof Error ? error.message : "OpenCodeAgent runtime could not start."); logger.error("Check the OpenCodeAgent output channel and verify that the installed VSIX matches this computer."); }
}
export async function deactivate(): Promise<void> { await shutdown?.(); shutdown = undefined; }
