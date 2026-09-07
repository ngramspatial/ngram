/** Confirm renderer work without claiming that asynchronous playback has finished. */
const synchronousActions = new Set([
  'action:spawn_object', 'action:spawn_toy', 'action:spawn_text',
  'action:remove_object', 'action:clear_objects', 'action:set_environment',
  'action:draw_annotation', 'action:emote', 'action:look_at', 'action:go_idle',
]);

export function dispatchWithReceipt(
  action: { type: string; actionId?: string; timestamp?: number; sessionId?: string },
  dispatch: () => void,
  send: (event: Record<string, unknown>) => void,
): void {
  let status = synchronousActions.has(action.type) ? 'completed' : 'accepted';
  let error: string | undefined;
  try {
    dispatch();
  } catch (cause) {
    if (!action.actionId) throw cause;
    status = 'failed';
    error = cause instanceof Error ? cause.message : String(cause);
  }
  if (action.actionId) {
    send({
      type: 'event:action_completed',
      action: action.type,
      completedActionId: action.actionId,
      actionTimestamp: action.timestamp,
      sessionId: action.sessionId,
      timestamp: Date.now(),
      status,
      ...(error ? { error: error.slice(0, 300) } : {}),
    });
  }
}
