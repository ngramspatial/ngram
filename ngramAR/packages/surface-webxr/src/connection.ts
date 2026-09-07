// @ts-nocheck
type MessageHandler = (msg: any) => void;
type StatusHandler = (status: string) => void;

export class ConnectionManager {
  private ws: WebSocket | null = null;
  private messageHandlers: MessageHandler[] = [];
  private statusHandlers: StatusHandler[] = [];
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private shouldReconnect = true;

  sessionId: string;
  shell: string | null = null;

  constructor(autoConnect = true) {
    this.sessionId = this.generateSessionId();
    this.shell = localStorage.getItem('ngram_ar:shell') ?? null;
    if (autoConnect) this.connect();
  }

  start(): void {
    if (!this.ws) this.connect();
  }

  switchShell(slug: string): void {
    this.shell = slug;
    localStorage.setItem('ngram_ar:shell', slug);
    this.shouldReconnect = true;
    this.reconnectDelay = 1000;
    this.ws?.close();
  }

  reconnect(): void {
    this.shouldReconnect = true;
    this.reconnectDelay = 1000;
    this.ws?.close();
  }

  getShell(): string | null {
    return this.shell;
  }

  private generateSessionId(): string {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  private connect(): void {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let url = `${protocol}//${window.location.host}/ws`;
    if (this.shell) {
      url += `?shell=${encodeURIComponent(this.shell)}`;
    }
    this.setStatus('connecting');

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      this.setStatus('connected');
    };

    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        for (const handler of this.messageHandlers) handler(msg);
      } catch {
        /* ignore malformed JSON */
      }
    };

    this.ws.onclose = () => {
      this.setStatus('disconnected');
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect(): void {
    if (!this.shouldReconnect) return;
    setTimeout(() => this.connect(), this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
  }

  private setStatus(status: string): void {
    for (const handler of this.statusHandlers) handler(status);
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  send(event: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(event));
    }
  }

  onMessage(handler: MessageHandler): void {
    this.messageHandlers.push(handler);
  }

  onStatusChange(handler: StatusHandler): void {
    this.statusHandlers.push(handler);
  }

  dispose(): void {
    this.shouldReconnect = false;
    this.ws?.close();
  }
}
