import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

const targets = ["linux-x64", "darwin-arm64", "darwin-x64", "win32-x64"];
for (const target of targets) {
  const exe = target.startsWith("win32-") ? "open-code-agent.exe" : "open-code-agent";
  if (!existsSync(`runtime/${target}/${exe}`)) throw new Error(`Missing ${target} native build. Each runtime must be built on its matching target OS and architecture.`);
  const result = spawnSync(process.execPath, ["scripts/package.mjs", target], { stdio: "inherit", shell: false });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
