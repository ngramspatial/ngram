import assert from 'node:assert/strict';
import test from 'node:test';
import { parseSlashCommand, matchSlashCommands } from '../packages/surface-webxr/src/slash-commands.ts';

test('slash commands are exact, case insensitive and complete by prefix', () => {
  assert.equal(parseSlashCommand('please compact this'), null);
  assert.deepEqual(parseSlashCommand(' /COMPACT '), { name: 'compact', valid: true });
  assert.equal(parseSlashCommand('/compact accidentally extra arguments').valid, false);
  assert.equal(parseSlashCommand('/invented').valid, false);
  assert.deepEqual(matchSlashCommands('/co').map(item => item.name), ['compact', 'context']);
  assert.deepEqual(matchSlashCommands('/v').map(item => item.name), ['voice']);
  assert.ok(matchSlashCommands('/').length >= 7);
});
