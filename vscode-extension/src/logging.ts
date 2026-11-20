import * as vscode from "vscode";
import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { redact } from "./redaction";

export { redact } from "./redaction";
export class Logger implements vscode.Disposable {
  readonly channel = vscode.window.createOutputChannel("OpenCodeAgent");
  private filePath: string | undefined;
  private pendingWrite: Promise<void> = Promise.resolve();
  constructor(private readonly debugEnabled: () => boolean) {}
  async enableFile(path: string): Promise<void> {
    try {
      await mkdir(dirname(path), { recursive: true });
      this.filePath = path;
      this.write("info", `Persistent diagnostic log: ${path}`);
    } catch (error) {
      this.channel.appendLine(`[error] Could not create diagnostic log: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  }
  info(message: string): void { this.write("info", message); }
  error(message: string): void { this.write("error", message); }
  debug(message: string): void { if (this.debugEnabled()) this.write("debug", message); }
  dispose(): void { this.channel.dispose(); }
  private write(level: "info" | "error" | "debug", message: string): void {
    const line = `[${level}] ${redact(message)}`;
    this.channel.appendLine(line);
    if (!this.filePath) return;
    this.pendingWrite = this.pendingWrite.then(() => appendFile(this.filePath!, `${new Date().toISOString()} ${line}\n`, "utf8")).catch(() => undefined);
  }
}
