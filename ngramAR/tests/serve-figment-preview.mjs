import { build } from "esbuild";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname, sep } from "node:path";

const ar = fileURLToPath(new URL("../", import.meta.url));
const output = resolve(ar, "../.runtime/figment-preview"), dist = resolve(ar, "packages/surface-webxr/dist");
await mkdir(output, { recursive: true });
await build({ entryPoints: [resolve(ar, "tests/figment-preview.ts")], bundle: true, format: "esm", outfile: resolve(output, "figment-preview.js"), sourcemap: true, logLevel: "info" });
if (process.argv.includes("--build-only")) process.exit(0);
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".ttf": "font/ttf", ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png" };
createServer(async (req, res) => {
  try {
    const path = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    if (path === "/lantern-recipe.json") {
      res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(await readFile(resolve(ar, "tests/figment-lantern.json"))); return;
    }
    const file = path.startsWith("/figment-preview.js") ? resolve(output, path.slice(1)) : resolve(dist, "." + (path === "/" ? "/index.html" : path));
    if (![output, dist].some(root => file.startsWith(root + sep))) { res.writeHead(403).end(); return; }
    let content = await readFile(file);
    if (path === "/") content = content.toString().replace('src="app.js"', 'src="/figment-preview.js"');
    res.writeHead(200, { "Content-Type": types[extname(file)] ?? "application/octet-stream", "Cache-Control": "no-store" }); res.end(content);
  } catch { res.writeHead(404).end(); }
}).listen(4175, "127.0.0.1", () => console.log("Figment review: http://localhost:4175/"));
