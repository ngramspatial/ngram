import assert from "node:assert/strict";
import test from "node:test";
import { OpenAIBinding } from "../packages/bindings/dist/openai-binding.js";
import { toOpenAITools } from "@ngram-ar/core";

test("direct binding uses completed world data for its next tool round, then stops", async () => {
  const binding = new OpenAIBinding({
    baseUrl: "http://unused",
    apiKey: "test",
    model: "test",
  });
  await binding.start("Create a world");
  let calls = 0,
    delivered = [];
  binding.callApi = async (messages) => {
    calls++;
    if (calls === 1)
      return {
        choices: [
          {
            message: {
              role: "assistant",
              content: null,
              tool_calls: [
                {
                  id: "tool1",
                  type: "function",
                  function: {
                    name: "world",
                    arguments: JSON.stringify({ command: "observe" }),
                  },
                },
              ],
            },
          },
        ],
      };
    assert.equal(
      JSON.parse(messages.at(-1).content).result.entities[0].id,
      "pendulum",
    );
    return {
      choices: [
        { message: { role: "assistant", content: "The pendulum is ready." } },
      ],
    };
  };
  binding.onProactiveAction((actions) => {
    delivered.push(...actions);
    queueMicrotask(() =>
      binding.handleEvent({
        type: "event:action_completed",
        completedActionId: actions[0].actionId,
        status: "completed",
        result: { entities: [{ id: "pendulum" }] },
        sessionId: "test",
      }),
    );
  });
  const result = await binding.handleSpeech("Inspect my creation", "test");
  assert.equal(calls, 2);
  assert.equal(delivered[0].type, "action:world");
  assert.equal(result[0].type, "action:speak");
  assert.equal(binding.worldPending.size, 0);
  await binding.handleEvent({
    type: "event:action_completed",
    completedActionId: "unknown",
    status: "completed",
    result: {},
    sessionId: "test",
  });
  assert.equal(calls, 2);
  assert.ok(toOpenAITools().some((t) => t.function.name === "world"));
});

test("cancel settles a waiting world request without another inference call", async () => {
  const binding = new OpenAIBinding({
    baseUrl: "http://unused",
    apiKey: "test",
    model: "test",
  });
  await binding.start("Test");
  let calls = 0,
    received;
  const sent = new Promise((resolve) => (received = resolve));
  binding.callApi = async () => {
    calls++;
    return {
      choices: [
        {
          message: {
            role: "assistant",
            content: null,
            tool_calls: [
              {
                id: "tool1",
                type: "function",
                function: { name: "world", arguments: '{"command":"observe"}' },
              },
            ],
          },
        },
      ],
    };
  };
  binding.onProactiveAction(() => received());
  const pending = binding.handleSpeech("Inspect", "test");
  await sent;
  await binding.handleEvent({ type: "event:cancel_turn", sessionId: "test" });
  assert.deepEqual(await pending, []);
  assert.equal(calls, 1);
  assert.equal(binding.worldPending.size, 0);
});

test("direct Figment tools wait for correlated renderer results, then end the turn", async () => {
  const binding = new OpenAIBinding({ baseUrl: "http://unused", apiKey: "test", model: "test" });
  await binding.start("Build a Figment");
  let calls = 0;
  binding.callApi = async messages => {
    if (++calls === 1) return { choices: [{ message: { role: "assistant", content: null, tool_calls: [{ id: "figment1", type: "function", function: { name: "figment_physics", arguments: JSON.stringify({ command: "physics", payload: { id: "lamp", physics: { mass: 2 } } }) } }] } }] };
    assert.equal(JSON.parse(messages.at(-1).content).result.mass, 2);
    return { choices: [{ message: { role: "assistant", content: "The lamp now weighs two kilograms." } }] };
  };
  binding.onProactiveAction(actions => {
    assert.equal(actions[0].command, "figment");
    assert.equal(actions[0].payload.command, "physics");
    queueMicrotask(() => binding.handleEvent({ type: "event:action_completed", completedActionId: actions[0].actionId, status: "completed", result: { mass: 2 }, sessionId: "test" }));
  });
  const result = await binding.handleSpeech("Make the lamp heavier", "test");
  assert.equal(calls, 2);
  assert.equal(result[0].type, "action:speak");
  assert.equal(binding.worldPending.size, 0);
  assert.equal(toOpenAITools().filter(t => t.function.name.startsWith("figment")).length, 5);
  assert.throws(() => binding.resolveToolCall({ function: { name: "figment_physics", arguments: '{"command":"place"}' } }, "test"), /Invalid Figment/);
});
