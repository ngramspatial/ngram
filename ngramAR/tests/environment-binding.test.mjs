import assert from 'node:assert/strict';
import test from 'node:test';
import { OpenAIBinding } from '../packages/bindings/dist/openai-binding.js';
import { toOpenAITools } from '@ngram-ar/core';

test('native environment tool waits for the renderer, supplies its receipt and ends the turn', async () => {
  const binding = new OpenAIBinding({ baseUrl: 'http://unused', apiKey: 'test', model: 'test' });
  await binding.start('Create a sky');
  let calls = 0;
  binding.callApi = async messages => {
    if (++calls === 1) return { choices: [{ message: { role: 'assistant', tool_calls: [{ id: 'sky', type: 'function', function: { name: 'environment', arguments: JSON.stringify({ command: 'configure', payload: { sky: { elevation: 8 } } }) } }] } }] };
    assert.equal(JSON.parse(messages.at(-1).content).result.revision, 3);
    return { choices: [{ message: { role: 'assistant', content: 'The sunset is ready.' } }] };
  };
  binding.onProactiveAction(actions => {
    const action = actions[0];
    assert.equal(action.type, 'action:world'); assert.equal(action.command, 'environment');
    assert.deepEqual(action.payload, { command: 'configure', sky: { elevation: 8 } });
    queueMicrotask(() => binding.handleEvent({ type: 'event:action_completed', sessionId: 'test', completedActionId: action.actionId, status: 'completed', result: { revision: 3 } }));
  });
  const actions = await binding.handleSpeech('Make a sunset', 'test');
  assert.equal(calls, 2); assert.equal(binding.worldPending.size, 0); assert.equal(actions[0].type, 'action:speak');
  assert.ok(toOpenAITools().some(tool => tool.function.name === 'environment'));
  await binding.stop();
});
