import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { basename } from "node:path";

const [file, target] = process.argv.slice(2);
if (!file || !target) throw new Error("Usage: verify-package.mjs <file.vsix> <target>");
function archive(args) {
  const unzipArgs = args[0] === "list" ? ["-Z1", file] : args[0] === "manifest" ? ["-p", file, "extension.vsixmanifest"] : ["-Z", "-v", file, args[1]];
  const unzip = spawnSync("unzip", unzipArgs, { encoding: "utf8" });
  if (unzip.status === 0) return unzip;
  const tarArgs = args[0] === "list" ? ["-tf", file] : ["-xOf", file, args[0] === "manifest" ? "extension.vsixmanifest" : args[1]];
  return spawnSync("tar", tarArgs, { encoding: "utf8" });
}
const listing = archive(["list"]);
if (listing.status !== 0) throw new Error("Cannot inspect VSIX; unzip or tar with ZIP support is required.");
const entries = listing.stdout.split(/\r?\n/).filter(Boolean);
const exe = target.startsWith("win32-") ? "open-code-agent.exe" : "open-code-agent";
const expected = "extension/runtime/" + target + "/" + exe;
if (!entries.includes(expected)) throw new Error("Expected bundled runtime is missing: " + expected);
if (!target.startsWith("win32-")) {
  const details = archive(["mode", expected]);
  if (details.status !== 0 || !details.stdout.includes("Unix file attributes (100755 octal)")) throw new Error("Bundled runtime is not marked executable.");
}
const forbidden = entries.filter((entry) => /(^|\/)(\.venv|tests?|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|uv-cache)(\/|$)|(^|\/)(\.env[^/]*|.*\.(pem|key|p12)|credentials\.json|profiles\.json)$/i.test(entry));
if (forbidden.length) throw new Error("Forbidden package contents: " + forbidden.join(", "));
const manifest = archive(["manifest"]);
if (manifest.status !== 0 || !manifest.stdout.includes("TargetPlatform=\"" + target + "\"")) throw new Error("VSIX target metadata does not match " + target + ".");
const digest = createHash("sha256").update(readFileSync(file)).digest("hex");
writeFileSync(file + ".sha256", digest + "  " + basename(file) + "\n");
console.log("Verified " + expected + "; SHA-256 " + digest);
