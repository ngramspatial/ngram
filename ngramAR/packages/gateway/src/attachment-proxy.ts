import type { IncomingMessage, ServerResponse } from 'node:http';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const MAX_BYTES = 100 * 1024 * 1024;

/** Browser -> configured worker disk. No public storage URL or worker token in messages. */
export async function proxyAttachment(req: IncomingMessage, res: ServerResponse,
  config: { bridgeUrl: string; token?: string }, id?: string): Promise<void> {
  const upload = req.method === 'POST' && !id;
  const download = (req.method === 'GET' || req.method === 'HEAD') && /^[a-f0-9]{32}$/.test(id ?? '');
  const fail = (status: number, error: string) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify({ error }));
  };
  if (!upload && !download) return fail(405, 'Unsupported attachment request');
  if (upload && req.headers.origin) {
    try { if (new URL(req.headers.origin).host !== req.headers.host) return fail(403, 'Upload origin rejected'); }
    catch { return fail(403, 'Upload origin rejected'); }
  }
  if (upload && (Number(req.headers['content-length'] ?? 0) > MAX_BYTES || req.headers['content-encoding']))
    return fail(413, 'Files must be 100 MB or smaller');
  const url = new URL(config.bridgeUrl);
  if (!['ws:', 'wss:', 'http:', 'https:'].includes(url.protocol)) return fail(502, 'Unsupported worker transport');
  url.protocol = ['wss:', 'https:'].includes(url.protocol) ? 'https:' : 'http:';
  url.pathname = url.pathname.replace(/\/$/, '') + '/attachments' + (id ? '/' + id : '');
  url.search = ''; url.hash = ''; url.username = ''; url.password = '';
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 300_000);
  const disconnect = () => { if (!res.writableEnded) abort.abort(); };
  res.on('close', disconnect);
  let bytes = 0;
  const limit = new Transform({ transform(chunk, _encoding, callback) {
    bytes += chunk.length;
    callback(bytes > MAX_BYTES ? Error('Upload exceeds 100 MB') : null, chunk);
  } });
  const requestError = () => abort.abort();
  req.on('error', requestError);
  try {
    const headers: Record<string, string> = config.token ? { Authorization: `Bearer ${config.token}` } : {};
    for (const key of ['content-type', 'content-length', 'x-ngram-filename', 'range']) {
      const value = req.headers[key];
      if (typeof value === 'string') headers[key] = value;
    }
    const options: RequestInit & { duplex?: string } = { method: req.method, headers, signal: abort.signal, redirect: 'error' };
    if (upload) {
      options.body = Readable.toWeb(req.pipe(limit)) as ReadableStream<Uint8Array>;
      options.duplex = 'half';
    }
    const response = await fetch(url, options);
    if (!response.ok && response.status !== 304 && response.status !== 416) {
      await response.body?.cancel();
      return fail(response.status, response.status === 404 ? 'Attachment is unavailable. Reattach the file; the worker may need updating.' : `Attachment request failed (${response.status})`);
    }
    const out: Record<string, string> = { 'X-Content-Type-Options': 'nosniff' };
    for (const name of ['content-type', 'content-length', 'cache-control', 'content-disposition', 'content-security-policy', 'accept-ranges', 'content-range']) {
      const value = response.headers.get(name);
      if (value) out[name] = value;
    }
    res.writeHead(response.status, out);
    if (response.body) await pipeline(Readable.fromWeb(response.body as any), res);
    else res.end();
  } catch {
    if (res.headersSent) res.destroy();
    else fail(bytes > MAX_BYTES ? 413 : 502, bytes > MAX_BYTES ? 'Files must be 100 MB or smaller' : 'Attachment transfer failed. Your draft is still here; retry when connected.');
  } finally {
    clearTimeout(timer); res.off('close', disconnect); req.off('error', requestError);
    if (upload) { req.unpipe(limit); limit.destroy(); }
  }
}
