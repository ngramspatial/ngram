// @ts-nocheck
// ─── EventBus ────────────────────────────────────────────────────────────────
// Lightweight publish/subscribe for decoupled manager communication.
// Replaces direct callback wiring between managers and the main orchestrator.

type Listener<T = unknown> = (data: T) => void;

export class EventBus {
  private listeners = new Map<string, Set<Listener>>();

  /** Subscribe to an event. Returns an unsubscribe function. */
  on<T = unknown>(event: string, listener: Listener<T>): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    const set = this.listeners.get(event)!;
    set.add(listener as Listener);
    return () => set.delete(listener as Listener);
  }

  /** Subscribe to an event, but only fire once. */
  once<T = unknown>(event: string, listener: Listener<T>): () => void {
    const unsub = this.on<T>(event, (data) => {
      unsub();
      listener(data);
    });
    return unsub;
  }

  /** Emit an event to all listeners. */
  emit<T = unknown>(event: string, data?: T): void {
    const set = this.listeners.get(event);
    if (!set) return;
    for (const listener of set) {
      try {
        listener(data);
      } catch (e) {
        console.error(`[event-bus] Error in listener for "${event}":`, e);
      }
    }
  }

  /** Remove all listeners for an event, or all listeners entirely. */
  clear(event?: string): void {
    if (event) {
      this.listeners.delete(event);
    } else {
      this.listeners.clear();
    }
  }
}
