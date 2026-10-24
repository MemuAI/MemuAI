// node build_vendor.mjs <node_modules parent> [--only pdflib] — rebuilds static/vendor (cm bundle + pdf.js copy
// + pdf-lib bundle). --only pdflib rebuilds just the PDF editing bundle (Round 31).
import { createRequire } from "node:module";
import { cpSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(here, "../static/vendor");
const args = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const only = process.argv.includes("--only") ? process.argv[process.argv.indexOf("--only") + 1] : "";
const modules = args[0] ? path.resolve(args[0], "node_modules") : path.resolve(here, "node_modules");
const { build } = createRequire(path.join(modules, "x.js"))("esbuild");
rmSync(path.join(out, "pdflib"), { recursive: true, force: true });
await build({ entryPoints: { pdflib: path.join(here, "pdf-entry.mjs") }, bundle: true, format: "esm", minify: true,
  target: ["chrome110"], outdir: path.join(out, "pdflib"), nodePaths: [modules], legalComments: "linked" });
if (only === "pdflib") { console.log("pdflib built in", out); process.exit(0); }
rmSync(path.join(out, "cm"), { recursive: true, force: true });
await build({ entryPoints: { cm: path.join(here, "cm-entry.mjs") }, bundle: true, splitting: true, format: "esm",
  minify: true, target: ["chrome110"], outdir: path.join(out, "cm"), nodePaths: [modules], legalComments: "linked",
  chunkNames: "c-[hash]" });
// pdfjs/polyfills.mjs and pdfjs/pdf.worker.compat.mjs are hand-written (Round 34, older WebView2) — only copy over them.
mkdirSync(path.join(out, "pdfjs"), { recursive: true });
for (const f of ["pdf.min.mjs", "pdf.worker.min.mjs"]) cpSync(path.join(modules, "pdfjs-dist/build", f), path.join(out, "pdfjs", f));
cpSync(path.join(modules, "pdfjs-dist/LICENSE"), path.join(out, "pdfjs", "LICENSE"));
console.log("vendor built in", out);
