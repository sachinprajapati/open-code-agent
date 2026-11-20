import * as vscode from "vscode";
import type { Mode } from "./runtime/protocolTypes";
export interface ServerConfiguration { executable: string; arguments: string[]; cwd?: string; startupTimeoutMs: number; shutdownTimeoutMs: number; maxRestarts: number; debug: boolean; }
export function configuration(): ServerConfiguration {
  const config = vscode.workspace.getConfiguration("openCodeAgent");
  return { executable: config.get<string>("server.executable", "open-code-agent"), arguments: config.get<string[]>("server.arguments", ["app-server"]), cwd: config.get<string>("server.cwd", "") || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath, startupTimeoutMs: config.get<number>("server.startupTimeoutMs", 10000), shutdownTimeoutMs: config.get<number>("server.shutdownTimeoutMs", 5000), maxRestarts: config.get<number>("maxRestarts", 3), debug: config.get<boolean>("debug", false) };
}
export function defaultMode(): Mode { return vscode.workspace.getConfiguration("openCodeAgent").get<Mode>("defaultMode", "plan"); }
