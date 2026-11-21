import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const artifactDir = resolve("../artifacts");
const targets = ["linux-x64", "darwin-arm64", "darwin-x64", "win32-x64"];
const artifacts = targets.map((target) => ({ target, file: resolve(artifactDir, `open-code-agent-${target}.vsix`) })).filter(({ file }) => existsSync(file));
if (artifacts.length === 0) throw new Error("No VSIX artifacts found in ../artifacts.");
for (const { target, file } of artifacts) {
  const result = spawnSync(process.execPath, ["scripts/verify-package.mjs", file, target], { stdio: "inherit", shell: false });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
