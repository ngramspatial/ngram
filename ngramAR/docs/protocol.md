# Spatial Action API & Presence Protocol

Version: 1.1.0

The Spatial Action API is the structured vocabulary through which agents express intent in space. The Presence Protocol is the transport layer carrying these messages between agent bindings and shell surfaces.

## Core Principle

**The agent decides WHAT it wants to do. The shell decides HOW.**

The agent says "greet the user." The shell chooses the animation, gaze target, voice timing, and movement. The agent never directly controls bones, transforms, or rendering. It issues spatial intentions through a structured API, and the shell runtime translates those into safe, beautiful physical behavior.

## Transport

- **WebSocket** connection at path `/ws` on the gateway server
- Messages are UTF-8 JSON strings
- Every message includes `type`, `timestamp` (Unix ms), and `sessionId`

## Session Lifecycle

1. Surface connects via WebSocket
2. Gateway sends `shell:config` with shell name, model, scale, and animation pack
3. Surface sends `event:shell_ready` with capabilities
4. Gateway creates an agent binding and sends `action:spawn`
5. Bidirectional message flow begins
6. On disconnect, binding is stopped and memory is flushed

## Tool Calling

The OpenAI binding registers spatial actions as **native LLM tools** via the function calling API. This means:

- The agent's text response is pure speech — no action tags mixed in
- Spatial actions arrive as structured tool calls with typed parameters
- The LLM naturally decides when to move, gesture, or emote as part of its reasoning
- New spatial tools can be added without changing the system prompt

### Registered Tools

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "move_to",
        "description": "Walk or move to a target.",
        "parameters": {
          "properties": {
            "target": { "type": "string", "enum": ["user", "left", "right", "forward", "away", "random"] },
            "speed": { "type": "string", "enum": ["walk", "fast"] }
          },
          "required": ["target"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "gesture",
        "description": "Perform a physical gesture.",
        "parameters": {
          "properties": {
            "gesture": { "type": "string", "enum": ["wave", "nod", "point", "shrug", "celebrate"] }
          },
          "required": ["gesture"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "emote",
        "description": "Express an emotion through body language.",
        "parameters": {
          "properties": {
            "emotion": { "type": "string", "enum": ["happy", "excited", "curious", "thoughtful", "concerned", "attentive", "calm"] },
            "intensity": { "type": "number", "minimum": 0, "maximum": 1 }
          },
          "required": ["emotion"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "look_at",
        "description": "Turn to look at something.",
        "parameters": {
          "properties": {
            "target": { "type": "string", "enum": ["user", "away"] }
          },
          "required": ["target"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "go_idle",
        "description": "Return to a relaxed idle stance.",
        "parameters": {}
      }
    }
  ]
}
```

ngram AR uses only the ngram inference gateway (`POST /v1/chat/completions` with tools). The `ActionTranslator` class can still map plain model text to spatial actions if a response arrives without tool calls.

## Spatial Actions (Agent → Shell)

These are intentions, not puppet commands.

### `action:speak`
The agent wants to say something.
```json
{
  "type": "action:speak",
  "text": "Hey, good to see you.",
  "audioData": "base64-mp3...",
  "visemes": [[0, 10, 0.8], [200, 11, 0.6]]
}
```
The shell handles voice playback, lip sync, talking animation, and gaze toward user. `audioData` is pre-synthesized by the gateway's voice engine. If absent, the shell uses Web Speech API as a fallback.

### `action:emote`
The agent wants to express an emotion.
```json
{
  "type": "action:emote",
  "emotion": "happy",
  "intensity": 0.8,
  "duration": 3000
}
```
Supported emotions: `happy`, `excited`, `curious`, `thoughtful`, `concerned`, `attentive`, `calm`.

### `action:move_to`
The agent wants to be somewhere.
```json
{
  "type": "action:move_to",
  "target": "user",
  "speed": "walk"
}
```
Target can be:
- `"user"` — walk toward the user's camera position
- `{ x, y, z }` — relative offset from current position (used for left/right/forward/away)
- A named anchor string (future: desk, window, etc.)

Speed: `"walk"` or `"fast"`.

### `action:look_at`
The agent wants to look at something.
```json
{
  "type": "action:look_at",
  "target": "user",
  "weight": 1.0
}
```
Target: `"user"`, `"forward"`, `"away"`, named anchor, or `Vec3`.

### `action:gesture`
The agent wants to make a physical gesture.
```json
{
  "type": "action:gesture",
  "gesture": "wave"
}
```
Gestures: `wave`, `nod`, `point`, `shrug`, `celebrate`, or custom strings mapped in the animation pack.

### `action:show_panel`
The agent wants to display information spatially.
```json
{
  "type": "action:show_panel",
  "panel": {
    "id": "code-1",
    "type": "code",
    "title": "Example",
    "content": "console.log('hello')",
    "position": "auto"
  }
}
```
The shell positions, renders, and manages the panel lifecycle.

### `action:hide_panel`
Remove a displayed panel by ID.

### `action:set_mode`
Change the shell's operating mode.
```json
{
  "type": "action:set_mode",
  "mode": "ambient"
}
```
Modes: `active` (full presence), `ambient` (dim, minimal), `sleep` (nearly invisible).

### `action:follow`
Follow a target at a distance.

### `action:highlight`
Highlight a spatial anchor with a pulsing marker.

### `action:go_idle`
Return to resting state. Clears gaze targets, stops following.

### `action:spawn`
Place the shell in the scene. On desktop, spawns at origin. In AR, triggers hit-test placement mode.

## Shell Events (Shell → Agent)

### `event:shell_ready`
Shell surface connected and ready.
```json
{
  "type": "event:shell_ready",
  "shellName": "webxr",
  "capabilities": {
    "ar": true,
    "handTracking": true,
    "spatialAudio": true,
    "hitTest": true
  }
}
```

### `event:user_speech`
User spoke. Sent for final transcriptions only.
```json
{
  "type": "event:user_speech",
  "text": "What can you see?",
  "isFinal": true,
  "spatialContext": {
    "version": "1.0",
    "surface": { "mode": "ar", "capabilities": {} },
    "user": { "position": { "x": 0, "y": 1.6, "z": 0 }, "lookingAtAgent": true },
    "agent": { "position": { "x": 0, "y": 0, "z": -1.2 }, "distanceMeters": 1.2 },
    "scene": { "anchors": [], "anchorCount": 0, "objectCount": 0 }
  }
}
```

`spatialContext` is a bounded, point-in-time observation. It is environmental
context, never an identity or instruction channel.

### Generated motion

`action:generate_motion` is an internal agent-to-gateway request. The gateway
calls the configured server-side motion provider and sends
`action:play_motion_clip` to the surface with an FBX, GLB, or glTF clip URL.
Provider credentials are never sent to the surface.

### `event:user_proximity`
Distance between user and shell changed.
```json
{
  "type": "event:user_proximity",
  "distance": 1.5,
  "approaching": true
}
```

### `event:user_gesture`
User made a hand gesture (via hand tracking or controller).
```json
{
  "type": "event:user_gesture",
  "gesture": "wave",
  "hand": "right",
  "position": { "x": 0, "y": 1.2, "z": -0.5 }
}
```

### `event:user_gaze`
Whether the user is looking at the shell (raycasted from camera direction).

### `event:scene_ready`
Initial scene with detected anchors.

### `event:scene_update`
Scene anchors changed.

### `event:action_completed`
A spatial action finished executing (e.g., speech playback ended, walk completed).
```json
{
  "type": "event:action_completed",
  "action": "speak",
  "actionTimestamp": 1711234567890
}
```

## HTTP API

### `POST /api/transcribe`
Forward recorded immersive-XR audio to the optional server transcription provider. Accepts multipart form data with an `audio` field. Desktop voice input uses the browser's native speech-recognition path and does not call this endpoint or require a separate Whisper key.

### `POST /api/shells`
Create a new shell on disk. Request body:
```json
{
  "name": "Nova",
  "model": "gpt-4o",
  "voice": "nova",
  "personality": "A curious and enthusiastic spatial presence."
}
```
Returns the created shell's slug, path, and configuration.

### `GET /api/shells`
List all available shells in the `shells/` directory.

### `GET /api/shell-config`
Returns the active shell's `shell.yaml` content as YAML text.

## Building a Surface

Implement the Spatial Action API:

1. Connect to the gateway WebSocket at `/ws`
2. Handle the incoming `shell:config` message to configure your renderer
3. Send `event:shell_ready` with your capabilities
4. Handle all `action:*` messages — translate intentions into rendering
5. Emit `event:*` messages when spatial events occur

## Inference binding

The only supported LLM path is the **ngram inference gateway** (OpenAI-compatible). The `OpenAIBinding` in `@ngram-ar/bindings` registers spatial tools, calls `POST /v1/chat/completions`, and parses tool calls. Plain-text replies can be passed through `ActionTranslator` when needed.
