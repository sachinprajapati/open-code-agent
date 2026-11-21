import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import type * as childProcess from "node:child_process";
import type { ChildProcessWithoutNullStreams, SpawnOptionsWithoutStdio } from "node:child_process";
import { describe, expect, it, vi } from "vitest";
import type { ServerConfiguration } from "../../src/configuration";
import type { Logger } from "../../src/logging";
import { AppServerProcess } from "../../src/runtime/appServerProcess";

type Spawn = typeof childProcess.spawn;

vi.mock("vscode", () => ({
  window: { createOutputChannel: () => ({ appendLine: () => undefined, dispose: () => undefined }) },
  workspace: { getConfiguration: () => ({ get: <T>(_key: string, value: T) => value }), workspaceFolders: [] },
}));

class FakeChild extends EventEmitter {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  pid = 42;
  readonly signals: NodeJS.Signals[] = [];
  constructor(private readonly exitOnTerm = true) {
    super();
    this.stdin.on("data", (chunk: Buffer) => {
      const request = JSON.parse(chunk.toString()) as { id?: string; method?: string };
      if (request.method === "initialize") this.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { selected_protocol_version: "1.0", capabilities: { profiles: true, sessions: true, streaming_events: true, bidirectional_approvals: true, cancellation: true, mcp: true }, server_name: "test", server_version: "1" } })}\n`);
    });
  }
  kill(signal: NodeJS.Signals = "SIGTERM"): boolean {
    this.signals.push(signal);
    if (signal === "SIGKILL" || this.exitOnTerm) this.exit(signal);
    return true;
  }
  exit(signal: NodeJS.Signals = "SIGTERM"): void {
    if (this.exitCode !== null || this.signalCode !== null) return;
    this.signalCode = signal;
    this.emit("exit", null, signal);
  }
}

const config: ServerConfiguration = { executable: "open-code-agent", arguments: ["app-server"], startupTimeoutMs: 100, shutdownTimeoutMs: 5, maxRestarts: 3, debug: false };
const logger = { debug: () => undefined, info: () => undefined, error: () => undefined } as unknown as Logger;
const asChild = (child: FakeChild): ChildProcessWithoutNullStreams => child as unknown as ChildProcessWithoutNullStreams;

describe("AppServerProcess lifecycle", () => {
  it("shuts down the extension-owned child with SIGTERM", async () => {
    const child = new FakeChild();
    const runtime = new AppServerProcess(logger, () => config, (() => asChild(child)) as unknown as Spawn, "win32");
    await runtime.start(); await runtime.stop();
    expect(child.signals).toEqual(["SIGTERM"]);
    expect(runtime.runtimeState).toBe("stopped");
  });

  it("uses structured argv with shell execution disabled", async () => {
    const child = new FakeChild();
    const spawn = ((_command: string, args: readonly string[], options: SpawnOptionsWithoutStdio) => {
      expect(args).toEqual(config.arguments);
      expect(options.shell).toBe(false);
      return asChild(child);
    }) as unknown as Spawn;
    const runtime = new AppServerProcess(logger, () => config, spawn, "win32");
    await runtime.start(); await runtime.stop();
  });

  it("terminates the complete Windows process tree after the graceful timeout", async () => {
    const child = new FakeChild(false);
    const calls: Array<{ executable: string; args: readonly string[] }> = [];
    const spawn = ((executable: string, args: readonly string[]) => { calls.push({ executable, args }); return asChild(child); }) as unknown as Spawn;
    const runtime = new AppServerProcess(logger, () => config, spawn, "win32");
    await runtime.start(); await runtime.stop();
    expect(child.signals).toEqual(["SIGTERM"]);
    expect(calls).toContainEqual({ executable: "taskkill", args: ["/pid", "42", "/T", "/F"] });
  });

  it("does not create duplicate children and can start a clean runtime after reload cleanup", async () => {
    const children: FakeChild[] = [];
    const spawn = ((_command: string, _args: readonly string[], options: SpawnOptionsWithoutStdio) => {
      expect(options.detached).toBe(true);
      const child = new FakeChild(); children.push(child); return asChild(child);
    }) as unknown as Spawn;
    const runtime = new AppServerProcess(logger, () => config, spawn, "linux", (pid, signal) => children.find((child) => child.pid === pid)?.kill(signal), () => children.some((child) => child.exitCode === null && child.signalCode === null));
    await Promise.all([runtime.start(), runtime.start()]);
    expect(children).toHaveLength(1);
    await runtime.stop(); await runtime.start();
    expect(children).toHaveLength(2);
    await runtime.stop();
  });

  it("cleans up an already-exited child without signalling it", async () => {
    const child = new FakeChild();
    const runtime = new AppServerProcess(logger, () => config, (() => asChild(child)) as unknown as Spawn, "win32");
    await runtime.start(); child.exit("SIGTERM"); await runtime.stop();
    expect(child.signals).toEqual([]);
    expect(runtime.runtimeState).toBe("stopped");
  });

  it("kills remaining group children after the tracked parent exits", async () => {
    const parent = new FakeChild(); const childAlive = { value: true }; const signals: NodeJS.Signals[] = [];
    const runtime = new AppServerProcess(logger, () => config, (() => asChild(parent)) as unknown as Spawn, "linux",
      (_pid, signal) => { signals.push(signal); parent.exit(signal); if (signal === "SIGKILL") childAlive.value = false; },
      () => parent.exitCode === null && parent.signalCode === null || childAlive.value);
    await runtime.start(); await runtime.stop();
    expect(signals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(runtime.runtimeState).toBe("stopped");
  });

  it("cleans up a packaged executable through the same owned process group", async () => {
    const child = new FakeChild(); const spawned: string[] = []; const signals: NodeJS.Signals[] = [];
    const spawn = ((command: string, _args: readonly string[], options: SpawnOptionsWithoutStdio) => { spawned.push(command); expect(options.detached).toBe(true); return asChild(child); }) as unknown as Spawn;
    const runtime = new AppServerProcess(logger, () => ({ ...config, executable: "/extension/runtime/linux-x64/open-code-agent" }), spawn, "linux", (_pid, signal) => { signals.push(signal); child.exit(signal); }, () => child.exitCode === null && child.signalCode === null);
    await runtime.start(); await runtime.stop();
    expect(spawned).toEqual(["/extension/runtime/linux-x64/open-code-agent"]);
    expect(signals).toEqual(["SIGTERM"]);
  });

  it("terminates a process group when startup fails", async () => {
    const child = new FakeChild(); const signals: NodeJS.Signals[] = [];
    child.stdin.removeAllListeners("data");
    const runtime = new AppServerProcess(logger, () => config, (() => asChild(child)) as unknown as Spawn, "linux", (_pid, signal) => { signals.push(signal); child.exit(signal); }, () => child.exitCode === null && child.signalCode === null);
    await expect(runtime.start()).rejects.toThrow();
    expect(signals).toEqual(["SIGTERM"]);
    expect(runtime.runtimeState).toBe("failed");
  });
});
