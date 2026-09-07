// @ts-nocheck
import type { ConnectionManager } from './connection.js';
import type { BehaviorDefinition, BehaviorTriggerType } from '@ngram-ar/core';

// ─── Sensor Context ─────────────────────────────────────────────────────────
// Built from existing scene state in main.ts each frame.

export interface SensorContext {
  userPosition: { x: number; y: number; z: number } | null;
  userGaze: { x: number; y: number; z: number } | null;
  agentPosition: { x: number; y: number; z: number } | null;
  agentState: string;
  isSpeaking: boolean;
  timeSinceLastInteraction: number;
  timeSinceLastSpeech: number;
  sceneAnchorCount: number;
  prevSceneAnchorCount: number;
}

// ─── Per-behavior sensor state ──────────────────────────────────────────────

interface SensorState {
  behavior: BehaviorDefinition;
  conditionMetSince: number | null;
  lastEmitted: number | null;
  fired: boolean;
}

// ─── Sensor Manager ─────────────────────────────────────────────────────────

export class BehaviorSensorManager {
  private readonly connection: ConnectionManager;
  private sensors: SensorState[] = [];

  constructor(connection: ConnectionManager) {
    this.connection = connection;
  }

  configure(behaviors: BehaviorDefinition[]): void {
    this.sensors = behaviors.map((b) => ({
      behavior: b,
      conditionMetSince: null,
      lastEmitted: null,
      fired: false,
    }));
  }

  update(ctx: SensorContext, now: number): void {
    for (const sensor of this.sensors) {
      const b = sensor.behavior;

      const met = this.checkTrigger(b.trigger.type as BehaviorTriggerType, b.trigger.params, ctx);
      // Observe departures even during cooldown so a later encounter can rearm.
      if (!met) {
        sensor.conditionMetSince = null;
        sensor.fired = false;
        continue;
      }

      sensor.conditionMetSince ??= now;
      if (sensor.fired) continue;

      // Cooldown spaces actual emissions; it must not delay the first encounter.
      const cooldownMs = (b.cooldown ?? 0) * 1000;
      if (sensor.lastEmitted !== null && now - sensor.lastEmitted < cooldownMs) continue;

      const duration = this.getDurationParam(b.trigger.params);
      const elapsed = (now - sensor.conditionMetSince) / 1000;
      if (elapsed >= duration) this.emit(sensor, ctx, now);
    }
  }

  // ─── Trigger checks ─────────────────────────────────────────────────────

  private checkTrigger(
    type: BehaviorTriggerType,
    params: Record<string, unknown>,
    ctx: SensorContext,
  ): boolean {
    switch (type) {
      case 'proximity':
        return this.checkProximity(params, ctx);
      case 'gaze':
        return this.checkGaze(params, ctx);
      case 'idle_timeout':
        return this.checkIdleTimeout(params, ctx);
      case 'gesture_detected':
        return false;
      case 'scene_change':
        return this.checkSceneChange(ctx);
      case 'silence':
        return this.checkSilence(params, ctx);
      case 'schedule':
        return false;
      default:
        return false;
    }
  }

  private checkProximity(params: Record<string, unknown>, ctx: SensorContext): boolean {
    if (!ctx.userPosition || !ctx.agentPosition) return false;
    const dx = ctx.userPosition.x - ctx.agentPosition.x;
    const dy = ctx.userPosition.y - ctx.agentPosition.y;
    const dz = ctx.userPosition.z - ctx.agentPosition.z;
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
    const threshold = Number(params['distance'] ?? 2.0);
    return dist <= threshold;
  }

  private checkGaze(params: Record<string, unknown>, ctx: SensorContext): boolean {
    if (!ctx.userGaze || !ctx.agentPosition || !ctx.userPosition) return false;
    const toAgent = {
      x: ctx.agentPosition.x - ctx.userPosition.x,
      y: ctx.agentPosition.y - ctx.userPosition.y,
      z: ctx.agentPosition.z - ctx.userPosition.z,
    };
    const mag = Math.sqrt(toAgent.x ** 2 + toAgent.y ** 2 + toAgent.z ** 2);
    if (mag < 0.01) return false;
    const dot =
      (ctx.userGaze.x * toAgent.x + ctx.userGaze.y * toAgent.y + ctx.userGaze.z * toAgent.z) / mag;
    const angleThreshold = Number(params['angle'] ?? 0.85);
    return dot > angleThreshold;
  }

  private checkIdleTimeout(params: Record<string, unknown>, ctx: SensorContext): boolean {
    const seconds = Number(params['seconds'] ?? 30);
    return ctx.timeSinceLastInteraction >= seconds;
  }

  private checkSceneChange(ctx: SensorContext): boolean {
    return ctx.sceneAnchorCount !== ctx.prevSceneAnchorCount;
  }

  private checkSilence(params: Record<string, unknown>, ctx: SensorContext): boolean {
    if (ctx.isSpeaking) return false;
    const seconds = Number(params['seconds'] ?? 15);
    return ctx.timeSinceLastSpeech >= seconds && ctx.timeSinceLastInteraction < seconds * 3;
  }

  // ─── Helpers ────────────────────────────────────────────────────────────

  private getDurationParam(params: Record<string, unknown>): number {
    return Number(params['duration'] ?? 0);
  }

  private emit(sensor: SensorState, ctx: SensorContext, now: number): void {
    sensor.lastEmitted = now;
    sensor.fired = true;

    const context: Record<string, unknown> = {};
    if (ctx.userPosition && ctx.agentPosition) {
      context['userDistance'] = this.distance(ctx.userPosition, ctx.agentPosition);
    }
    if (ctx.timeSinceLastInteraction > 0) context['idleSeconds'] = ctx.timeSinceLastInteraction;
    if (ctx.timeSinceLastSpeech > 0) context['silenceSeconds'] = ctx.timeSinceLastSpeech;

    this.connection.send({
      type: 'event:behavior_trigger',
      behaviorId: sensor.behavior.id,
      triggerType: sensor.behavior.trigger.type,
      context,
    });
  }

  private distance(
    a: { x: number; y: number; z: number },
    b: { x: number; y: number; z: number },
  ): number {
    const dx = a.x - b.x;
    const dy = a.y - b.y;
    const dz = a.z - b.z;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }
}
