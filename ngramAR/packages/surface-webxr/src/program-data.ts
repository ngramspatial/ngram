import { parseProgramDataSource, type ProgramDataSource } from '@ngram-ar/core';

/** Only explicitly published project JSON crosses into a behavior, never network access. */
export class ProgramDataFeed {
  readonly source: ProgramDataSource | null;
  data: Record<string, unknown> | null = null;
  status = { status: 'waiting', receivedAt: 0, checkedAt: 0, error: '' };
  private request: AbortController | null = null;
  private nextDue = 0;
  private active = false;
  constructor(value: unknown) { this.source = parseProgramDataSource(value); }
  start() { if (!this.active) { this.active = true; this.nextDue = 0; } }
  stop() { this.active = false; this.request?.abort(); this.request = null; }
  async refresh(now = Date.now()): Promise<void> {
    if (!this.source || !this.active || this.request || now < this.nextDue) return;
    const request = new AbortController();
    this.request = request;
    this.nextDue = now + this.source.intervalSeconds * 1000;
    const timeout = setTimeout(() => request.abort(), 10000);
    try {
      const response = await fetch(this.source.url, { signal: request.signal, cache: 'no-store', redirect: 'error', credentials: 'same-origin' });
      if (!response.ok) { await response.body?.cancel(); throw Error(`Feed unavailable (${response.status})`); }
      const reader = response.body?.getReader();
      if (!reader) throw Error('Empty feed response');
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 65536) { await reader.cancel(); throw Error('Feed exceeds 64 KiB'); }
          chunks.push(value);
        }
      } finally { reader.releaseLock(); }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw Error('Feed must be a JSON object');
      if (request.signal.aborted || !this.active || this.request !== request) return;
      this.data = data;
      this.status = { status: 'ready', receivedAt: Date.now(), checkedAt: Date.now(), error: '' };
    } catch (error) {
      if (!this.active || this.request !== request) return;
      this.status = { ...this.status, status: 'error', checkedAt: Date.now(), error: String(error instanceof Error ? error.message : error).slice(0, 240) };
    } finally {
      clearTimeout(timeout);
      if (this.request === request) this.request = null;
    }
  }
}
