import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("vscode", () => ({ ExtensionMode: { Development: 1, Production: 2 } }));
import { containedPath, resolveRuntime, targetFor } from "../../src/runtime/resolveRuntime";

const dirs: string[] = [];
function temp(): string { const path = mkdtempSync(join(tmpdir(), "oca-runtime-test-")); dirs.push(path); return path; }
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });

describe("runtime resolution", () => {
  it("runs the checkout through uv for the default development launch", () => {
    const context = { extensionMode: 1, extensionPath: "/repo/vscode-extension" } as never;
    const config = { executable: "open-code-agent", arguments: ["app-server"] } as never;
    expect(resolveRuntime(context, config)).toMatchObject({
      mode: "development",
      executable: "uv",
      arguments: ["run", "--project", "/repo", "open-code-agent", "app-server"],
      cwd: "/repo",
    });
  });
  it("preserves configured Python/uv launch in development mode", () => {
    const context = { extensionMode: 1 } as never;
    const config = { executable: "uv", arguments: ["run", "open-code-agent", "app-server"], cwd: "/repo" } as never;
    expect(resolveRuntime(context, config)).toMatchObject({ mode: "development", executable: "uv", arguments: ["run", "open-code-agent", "app-server"], cwd: "/repo" });
  });
  it("resolves only the matching packaged target", () => {
    const root = temp(); const runtimeDir = join(root, "runtime", "linux-x64"); mkdirSync(runtimeDir, { recursive: true });
    const runtimePath = join(runtimeDir, "open-code-agent"); writeFileSync(runtimePath, "binary"); chmodSync(runtimePath, 0o755);
    const context = { extensionMode: 2, extensionPath: root } as never;
    expect(resolveRuntime(context, {} as never)).toMatchObject({ mode: "bundled", executable: runtimePath, arguments: ["app-server"] });
  });
  it("reports unsupported platform and architecture pairs", () => {
    expect(() => targetFor("linux", "ia32")).toThrow(/no runtime for linux\/ia32/);
    expect(() => targetFor("win32", "arm64")).toThrow(/no runtime for win32\/arm64/);
  });
  it("rejects paths that escape extension install directory", () => {
    const root = temp(); const outside = temp(); const candidate = join(outside, "runtime"); writeFileSync(candidate, "binary");
    expect(() => containedPath(root, candidate)).toThrow(/outside the extension/);
  });
  it("detects symlinks that escape extension install directory", () => {
    const root = temp(); const outside = temp(); const target = join(outside, "binary"); writeFileSync(target, "binary");
    const link = join(root, "link"); symlinkSync(target, link);
    expect(() => containedPath(root, link)).toThrow(/outside the extension/);
  });
});
