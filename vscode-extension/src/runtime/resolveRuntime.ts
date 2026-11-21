import { existsSync, realpathSync } from "node:fs";
import { arch, platform } from "node:process";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import * as vscode from "vscode";
import type { ServerConfiguration } from "../configuration";

export interface RuntimeResolution {
  mode: "development" | "bundled";
  executable: string;
  arguments: string[];
  cwd?: string;
}

export function targetFor(hostPlatform = platform, hostArch = arch): string {
  const os = hostPlatform === "win32" ? "win32" : hostPlatform === "darwin" ? "darwin" : hostPlatform === "linux" ? "linux" : undefined;
  const cpu = hostArch === "x64" ? "x64" : hostArch === "arm64" ? "arm64" : undefined;
  if (!os || !cpu || (os === "win32" && cpu !== "x64")) throw new Error(`OpenCodeAgent has no runtime for ${hostPlatform}/${hostArch}. Install a VSIX built for a supported platform and architecture.`);
  return `${os}-${cpu}`;
}

export function containedPath(extensionPath: string, candidate: string): string {
  const root = realpathSync(extensionPath);
  const resolved = resolve(candidate);
  const real = realpathSync(resolved);
  const rel = relative(root, real);
  if (rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel)) throw new Error("Bundled runtime path is outside the extension installation directory.");
  return real;
}

export function resolveRuntime(context: vscode.ExtensionContext, config: ServerConfiguration): RuntimeResolution {
  if (context.extensionMode === vscode.ExtensionMode.Development) {
    return { mode: "development", executable: config.executable, arguments: config.arguments, cwd: config.cwd };
  }
  const target = targetFor();
  const filename = target.startsWith("win32-") ? "open-code-agent.exe" : "open-code-agent";
  const path = join(context.extensionPath, "runtime", target, filename);
  if (!existsSync(path)) throw new Error(`Bundled OpenCodeAgent runtime is missing for ${target}. Install the ${target} VSIX for this computer.`);
  return { mode: "bundled", executable: containedPath(context.extensionPath, path), arguments: ["app-server"], cwd: config.cwd };
}
