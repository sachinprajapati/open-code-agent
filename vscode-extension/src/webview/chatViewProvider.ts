import * as vscode from "vscode";
import type { ApprovalController } from "../approvals/approvalController";
import type { SessionController } from "../sessions/sessionController";
import { html } from "./html";
import { parseWebviewMessage } from "./messages";

export class ChatViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  constructor(private readonly extensionUri: vscode.Uri, private readonly sessions: SessionController, private readonly approvals: ApprovalController) { this.disposables.push(sessions.onDidChange((state) => this.post({ type: "hydrateState", state }))); }
  resolveWebviewView(view: vscode.WebviewView): void { this.view = view; view.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] }; view.webview.html = html(view.webview, this.extensionUri); this.disposables.push(view.webview.onDidReceiveMessage((raw: unknown) => { const message = parseWebviewMessage(raw); if (!message) { this.error("Ignored an invalid webview message."); return; } void this.handle(message); }), view.onDidDispose(() => this.approvals.failClosed())); }
  reveal(): void { this.view?.show(true); }
  status(message: string): void { this.post({ type: "statusMessage", message }); }
  error(message: string): void { this.post({ type: "errorMessage", message }); }
  approval(approval: unknown): void { this.post({ type: "approvalRequested", approval }); }
  dispose(): void { for (const disposable of this.disposables) disposable.dispose(); }
  private async handle(message: NonNullable<ReturnType<typeof parseWebviewMessage>>): Promise<void> { try { switch (message.type) { case "ready": this.post({ type: "hydrateState", state: this.sessions.snapshot() }); break; case "sendPrompt": await this.sessions.send(message.text); break; case "cancelRun": await this.sessions.cancel(); break; case "requestTimeout": await this.sessions.timeout(); break; case "selectProfile": await this.sessions.selectProfile(message.profileId); break; case "selectModel": await this.sessions.selectModel(message.modelId); break; case "selectMode": await this.sessions.selectMode(message.mode); break; case "clearSession": await this.sessions.clear(); break; case "newSession": await this.sessions.newSession(); break; case "restartRuntime": await this.sessions.restart(); break; case "openSettings": await vscode.commands.executeCommand("workbench.action.openSettings", "@ext:open-code-agent"); break; case "approvalDecision": this.approvals.decide(message.approvalId, message.fingerprint, message.approved); break; } } catch (error) { this.error(error instanceof Error ? error.message : "OpenCodeAgent action failed."); } }
  private post(message: unknown): void { void this.view?.webview.postMessage(message); }
}
