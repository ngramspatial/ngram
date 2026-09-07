// @ts-nocheck
// ─── MessageRouter ───────────────────────────────────────────────────────────
// Replaces the monolithic switch statement in main.ts. Handlers register by
// action type, and the router dispatches incoming WebSocket messages.
// Unhandled action types are logged rather than silently dropped.

import type { EventBus } from './event-bus.js';

type ActionHandler = (msg: Record<string, any>) => void | Promise<void>;

export class MessageRouter {
  private handlers = new Map<string, ActionHandler>();
  private bus: EventBus;

  constructor(bus: EventBus) {
    this.bus = bus;
  }

  /** Register a handler for a specific action type. */
  handle(actionType: string, handler: ActionHandler): void {
    this.handlers.set(actionType, handler);
  }

  /** Register handlers for multiple action types at once. */
  handleMany(handlers: Record<string, ActionHandler>): void {
    for (const [type, handler] of Object.entries(handlers)) {
      this.handlers.set(type, handler);
    }
  }

  /** Dispatch an incoming message. Returns true if handled. */
  dispatch(msg: Record<string, any>): boolean {
    const type = msg.type as string;
    if (!type) return false;

    const handler = this.handlers.get(type);
    if (handler) {
      try {
        const result = handler(msg);
        // If handler returns a promise, catch async errors
        if (result && typeof (result as Promise<void>).catch === 'function') {
          (result as Promise<void>).catch((e) => {
            console.error(`[message-router] Async error handling "${type}":`, e);
          });
        }
      } catch (e) {
        console.error(`[message-router] Error handling "${type}":`, e);
      }
      // Emit on the bus so other listeners can observe
      this.bus.emit(`action:${type}`, msg);
      return true;
    }

    // Not a registered handler — emit on bus anyway for optional listeners
    this.bus.emit(`action:${type}`, msg);
    return false;
  }
}
