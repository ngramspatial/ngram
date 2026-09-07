// Developer-only fixture; bundles the real XR components, never ships with the surface.
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, extname, sep } from 'node:path';

const ar = fileURLToPath(new URL('../', import.meta.url));
const output = resolve(ar, '../.runtime/spatial-preview');
const dist = resolve(ar, 'packages/surface-webxr/dist');
await mkdir(output, { recursive: true });
await build({ entryPoints: [resolve(ar, 'tests/spatial-preview.ts')], bundle: true, format: 'esm',
  outfile: resolve(output, 'spatial-preview.js'), sourcemap: true, logLevel: 'info' });
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.svg': 'image/svg+xml', '.png': 'image/png' };
if (process.argv.includes('--build-only')) process.exit(0);
createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  const file = path === '/spatial-preview.html' ? resolve(ar, 'tests/spatial-preview.html')
    : path.startsWith('/spatial-preview.js') ? resolve(output, path.slice(1))
    : resolve(dist, '.' + (path === '/' ? '/index.html' : path));
  if (![dist, output, resolve(ar, 'tests')].some(root => file.startsWith(root + sep))) { res.writeHead(403).end(); return; }
  try {
    const content = await readFile(file);
    res.writeHead(200, { 'Content-Type': types[extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' });
    res.end(content);
  } catch { res.writeHead(404).end(); }
}).listen(4173, '127.0.0.1', () => console.log('Spatial review: http://127.0.0.1:4173/spatial-preview.html'));
