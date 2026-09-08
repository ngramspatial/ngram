import { build } from 'esbuild';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, extname, sep } from 'node:path';
const ar = fileURLToPath(new URL('../', import.meta.url));
const output = resolve(ar, '../.runtime/work-status-preview'), dist = resolve(ar, 'packages/surface-webxr/dist');
await mkdir(output, { recursive: true });
await build({ entryPoints: [resolve(ar, 'tests/work-status-preview.ts')], bundle: true, format: 'esm', outfile: resolve(output, 'preview.js') });
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.svg': 'image/svg+xml', '.png': 'image/png' };
createServer(async (req, res) => {
  try {
    const path = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    const file = path === '/preview.js' ? resolve(output, 'preview.js') : resolve(dist, '.' + (path === '/' ? '/index.html' : path));
    if (![output, dist].some(root => file.startsWith(root + sep))) { res.writeHead(403).end(); return; }
    let data = await readFile(file);
    if (path === '/') data = data.toString().replace('src="app.js"', 'src="/preview.js"');
    res.writeHead(200, { 'Content-Type': types[extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' }).end(data);
  } catch { res.writeHead(404).end(); }
}).listen(4178, '127.0.0.1', () => console.log('Activity preview: http://localhost:4178/'));
