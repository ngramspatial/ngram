import assert from 'node:assert/strict';
import test from 'node:test';
import { updateContextDisplay } from '../packages/surface-webxr/src/context-status.ts';

test('context display labels estimates and keeps compaction visible through the next usage update', () => {
  let display = updateContextDisplay({}, { phase: 'usage', estimatedTokens: 28000, inputBudgetTokens: 256000 }, 1000);
  assert.equal(display.text, 'Context ≈28K / 256K');
  assert.match(display.title, /not billing usage/);
  display = updateContextDisplay(display.state, { phase: 'compacting' }, 2000);
  assert.equal(display.text, 'Compacting context…');
  display = updateContextDisplay(display.state, { phase: 'compacted', estimatedTokens: 12000 }, 3000);
  assert.equal(display.text, 'Context compacted');
  display = updateContextDisplay(display.state, { phase: 'usage', estimatedTokens: 13000 }, 4000);
  assert.equal(display.text, 'Context compacted');
  display = updateContextDisplay(display.state, { phase: 'usage' }, 10000);
  assert.equal(display.text, 'Context ≈13K / 256K');
  assert.match(display.title, /Last compacted/);
});

test('failed, skipped, and cancelled compactions clear the busy indicator truthfully', () => {
  const active = updateContextDisplay({}, { phase: 'compacting' }, 1000).state;
  for (const [phase, text] of [
    ['failed', 'Compaction failed · history retained'],
    ['unchanged', 'Nothing to compact yet'],
    ['stopped', 'Compaction stopped'],
  ]) {
    const display = updateContextDisplay(active, { phase }, 2000);
    assert.equal(display.state.compacting, false);
    assert.equal(display.text, text);
    assert.equal(display.state.lastCompactedAt, undefined);
  }
});
