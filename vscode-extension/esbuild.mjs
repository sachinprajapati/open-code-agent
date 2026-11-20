import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");
const context = await esbuild.context({
  entryPoints: ["src/extension.ts"], bundle: true, platform: "node", target: "node18",
  external: ["vscode"], format: "cjs", sourcemap: true, outfile: "dist/extension.js",
});
if (watch) await context.watch(); else { await context.rebuild(); await context.dispose(); }
