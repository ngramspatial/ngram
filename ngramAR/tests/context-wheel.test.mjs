import assert from 'node:assert/strict';
import test from 'node:test';
import { contextFraction, contextPricing } from '../packages/surface-webxr/src/context-pricing.ts';
import { updateContextDisplay } from '../packages/surface-webxr/src/context-status.ts';

test('context meter distinguishes unknown, empty, and over-budget usage', () => {
  assert.equal(contextFraction(undefined, 256000), null);
  assert.equal(contextFraction(0, 256000), 0);
  assert.equal(contextFraction(128000, 256000), 0.5);
  assert.equal(contextFraction(300000, 256000), 1.171875);
  for (const value of [-1, NaN, Infinity]) assert.equal(contextFraction(value, 256000), null);
  assert.equal(contextFraction(100, 0), null);
});

test('pricing uses exact provider/model and applies Astra long-context rates to the whole prompt', () => {
  const standard = contextPricing('openai', 'gpt-6-astra', 256000);
  assert.equal(standard.input, 10);
  assert.equal(standard.cached, 1);
  assert.equal(standard.output, 50);
  assert.equal(standard.estimatedInputCost, 2.56);
  assert.equal(contextPricing('openai', 'gpt-6-astra', 272000).longContext, false);
  const long = contextPricing('openai', 'gpt-6-astra', 300000);
  assert.equal(long.input, 20);
  assert.equal(long.output, 75);
  assert.equal(long.estimatedInputCost, 6);
  assert.equal(contextPricing('openai', 'gpt-6-astra', 0).estimatedInputCost, 0);
  assert.equal(contextPricing('openai', 'gpt-6-astra').estimatedInputCost, null);
  for (const [provider, model] of [['custom', 'gpt-6-astra'], ['openrouter', 'gpt-6-astra'], ['openai', 'unlisted'], ['openai', '__proto__']]) {
    assert.equal(contextPricing(provider, model, 10000), null);
  }
});

test('model or provider changes clear stale usage and malformed counters are ignored', () => {
  const state = updateContextDisplay({}, { phase: 'usage', model: 'first', provider: 'openai', estimatedTokens: 100, inputBudgetTokens: 1000 }).state;
  assert.equal(updateContextDisplay(state, { phase: 'usage', model: 'second' }).state.estimatedTokens, undefined);
  assert.equal(updateContextDisplay(state, { phase: 'usage', provider: 'custom' }).state.estimatedTokens, undefined);
  assert.equal(updateContextDisplay(state, { phase: 'usage', estimatedTokens: -10 }).state.estimatedTokens, 100);
});
