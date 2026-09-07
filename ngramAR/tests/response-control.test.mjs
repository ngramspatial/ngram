import assert from 'node:assert/strict';
import test from 'node:test';
import { setupResponseControl } from '../packages/surface-webxr/src/response-control.ts';

function fixture() {
  const button = new EventTarget();
  button.dataset = {};
  button.setAttribute = (key, value) => { button[key] = value; };
  let sent = 0;
  let stopped = 0;
  const control = setupResponseControl(button, () => sent++, () => stopped++);
  return { button, control, click: () => button.dispatchEvent(new Event('click')),
    calls: () => ({ sent, stopped }) };
}

test('one button sends at rest and stops each active work state', () => {
  const { button, control, click, calls } = fixture();
  assert.equal(button.dataset.mode, 'send');
  click();
  for (const agentState of ['thinking', 'planning', 'tool_running', 'messaging', 'speaking']) {
    control.update({ agentState });
    assert.equal(button['aria-label'], 'Stop response');
    click();
  }
  assert.deepEqual(calls(), { sent: 1, stopped: 5 });
  for (const agentState of ['idle', 'error', 'listening']) {
    control.update({ agentState });
    assert.equal(button.dataset.mode, 'send');
    assert.equal(button['aria-label'], 'Send');
  }
});

test('completion waits for both model work and queued speech, in either order', () => {
  const { button, control } = fixture();
  control.update({ agentState: 'thinking', playing: true });
  control.update({ agentState: 'idle' });
  assert.equal(button.dataset.mode, 'stop');
  control.update({ playing: false });
  assert.equal(button.dataset.mode, 'send');
  control.update({ agentState: 'tool_running', playing: true });
  control.update({ playing: false });
  assert.equal(button.dataset.mode, 'stop');
  control.update({ agentState: 'idle' });
  assert.equal(button.dataset.mode, 'send');
});

test('compaction is stoppable and cancellation or disconnect resets every source', () => {
  const { button, control, click, calls } = fixture();
  control.update({ compacting: true });
  click();
  assert.deepEqual(calls(), { sent: 0, stopped: 1 });
  control.update({ compacting: false });
  assert.equal(button.dataset.mode, 'send');
  control.update({ agentState: 'thinking', playing: true, compacting: true });
  control.reset();
  assert.equal(control.isActive(), false);
  assert.equal(button.dataset.mode, 'send');
});
