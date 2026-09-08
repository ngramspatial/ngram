// @ts-nocheck
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

/** Stream only named Blender resources from the configured entity bridge.
 * No arbitrary target URL, worker filesystem path, or backend token reaches the browser.
 */
export async function proxyBlender(req, res, config, resource) {
  if (req.method === 'POST' && req.headers.origin) {
    let sameOrigin = false;
    try { sameOrigin = new URL(req.headers.origin).host === req.headers.host; } catch { /* malformed origin */ }
    if (!sameOrigin) { res.writeHead(403); res.end(); return; }
  }
  if (!/^[a-zA-Z0-9_-]{1,64}\/(?:status|stop|renders\/[a-f0-9]{32}\/view\.jpg|[1-9][0-9]*\/(?:preview\.glb|project\.blend))$/.test(resource) ||
      (req.method !== 'GET' && !(req.method === 'POST' && resource.endsWith('/stop'))) ||
      (req.method === 'GET' && resource.endsWith('/stop'))) {
    res.writeHead(404); res.end(); return;
  }
  const url = new URL(config.bridgeUrl);
  if (!['ws:', 'wss:', 'http:', 'https:'].includes(url.protocol)) throw Error('Unsupported bridge transport');
  url.protocol = ['wss:', 'https:'].includes(url.protocol) ? 'https:' : 'http:';
  url.pathname = url.pathname.replace(/\/$/, '') + '/blender/' + resource;
  url.search = ''; url.hash = ''; url.username = ''; url.password = '';
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 120000);
  const disconnect = () => { if (!res.writableEnded) abort.abort(); };
  res.on('close', disconnect);
  try {
    const response = await fetch(url, {
      method: req.method, redirect: 'error', signal: abort.signal,
      headers: config.token ? { Authorization: `Bearer ${config.token}` } : {},
    });
    if (!response.ok) {
      await response.body?.cancel();
      res.writeHead(response.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify({ ok: false, error: `Blender host unavailable (${response.status})` }));
      return;
    }
    const headers = { 'X-Content-Type-Options': 'nosniff' };
    for (const name of ['content-type', 'content-length', 'cache-control', 'content-disposition'])
      if (response.headers.has(name)) headers[name] = response.headers.get(name);
    res.writeHead(200, headers);
    if (response.body) await pipeline(Readable.fromWeb(response.body), res);
    else res.end();
  } catch {
    if (res.headersSent) res.destroy();
    else { res.writeHead(502, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: false, error: 'Blender host connection failed' })); }
  } finally {
    clearTimeout(timer); res.off('close', disconnect);
  }
}
