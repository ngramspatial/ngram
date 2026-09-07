# ngram AR

**Replaceable bodies for persistent ngram Entities.**

ngram AR is the WebXR surface and shell format inside the ngram repository. The
Python Entity remains the mind: identity, relationships, memory, judgment, and
tools stay canonical while the shell supplies a body, voice, animation, spatial
behavior, and presentation surfaces.

> The Entity is the intelligence. The shell is the experience.

## Quick start

From the repository root:

```bash
uv sync --extra dev
npm run ar:install
npm run ar:build
uv run ngram ar setup <entity>
npm run ar:dev -- shells/<entity>
```

Open `http://localhost:3000`.

For passthrough AR on Meta Quest:

```bash
npm run ar:dev -- shells/<entity> --host 0.0.0.0
```

Open the printed network URL in Meta Quest Browser, accept the local
certificate, choose **Enter AR**, and place the body.
Only use the network bind on a trusted LAN.

You can run the same commands directly here:

```bash
npm install
npm run build
npm run ngram-ar -- setup <entity>
npm run ngram-ar -- dev shells/<entity>
```

## How it works

```text
Python Entity             Node gateway             WebXR surface
identity + memory   <──>  Presence Protocol  <──>  model + voice + motion
Telegram/Discord          authenticated bridge      desktop / headset
```

The gateway sends speech and spatial events to the Python bridge. The Entity
processes them through the same serialized `perceive` path used by other
surfaces, then returns semantic actions such as `speak`, `gesture`,
`look_at`, `move_to`, and `show_panel`.

The Entity chooses what it means to do. The shell decides how that intent is
performed by a particular body.

## Pairing an Entity

`ngram ar setup` is the product setup path. It:

1. selects or creates a shell;
2. connects to a local Entity or a Railway worker;
3. generates a dedicated bridge token;
4. stores credentials in ignored environment files and deployment secrets;
5. optionally reuses your Telegram or Discord person key;
6. verifies health and an authenticated WebSocket handshake.

For Telegram relationship continuity, send `/whoami` to the Entity and enter
that value when prompted. AR and Telegram will then update the same relationship
record.

Manual configuration is documented in [docs/NGRAM.md](docs/NGRAM.md).

## Switching brains

Open **Settings → Choose the brain** in the web workspace. The switch has three
modes:

- **Local** uses an OpenAI-compatible runtime on the same machine as the Entity;
- **Private GPU** restores the deployment's private inference gateway;
- **Frontier API** connects OpenAI, Anthropic, Gemini, OpenRouter, xAI, Groq,
  Together, Fireworks, Mistral, DeepSeek, Venice, or a custom compatible API.

The switch is live and Entity-wide. The body, identity, history, tools, and
relationships do not change, and every connected surface uses the selected
chat model. In a hybrid deployment, embeddings stay on the private inference
route so semantic memory continues working while frontier cognition is active.

Browser-entered credentials are sent through the authenticated Entity bridge,
never added to chat history, and never returned by the settings API. The local
gateway persists the active choice in the git-ignored `.runtime/brain.json`
file with owner-only permissions where the platform supports them, then
restores it when the bridge reconnects.

## Shell definition

A shell is a portable directory with a `shell.yaml` file and optional model
assets:

```yaml
name: My Entity
description: A calm spatial presence.

model: models/character.fbx
scale: 0.01

animationPack:
  idle: models/idle.fbx
  talking: models/talking.fbx
  waving: models/waving.fbx
  walking: models/walking.fbx

behaviorPack:
  - look-at-user
  - idle-breathe
  - anchor-to-surface
  - proximity-greet
  - gesture-respond

voice:
  provider: edge
  voice: en-US-JennyNeural

toolSurfaces:
  - floating-card

binding:
  type: ngram_entity
  options: {}
  system: |
    Use spatial actions when they add meaning. Do not narrate body actions.
    Identity and memory remain those of the running Entity.
```

The shell owns presentation, not personality. Keep identity, beliefs,
relationships, and conversational memory in the Entity.

Create a shell:

```bash
npm run ngram-ar -- init shells/my-entity
npm run ngram-ar -- setup my-entity --shell shells/my-entity
npm run ngram-ar -- dev shells/my-entity
```

The workspace's **New Shell** dialog does the same body-only scaffolding. It
never creates a second LLM persona.

## Models and animation

Models can be FBX, GLB/glTF, or `default` for the procedural body. Paths are
relative to the shell directory. Animation keys map semantic states to clips.

See [the Mixamo guide](docs/mixamo.md). Keep licensed assets that cannot be
redistributed under `models/private/`; that path is ignored by Git.

## Behaviors and spatial tools

Built-in behavior packs include gaze tracking, idle breathing, surface
anchoring, proximity greeting, gesture response, and spatial awareness.

The Presence Protocol supports:

- speech, emotion, gaze, gesture, locomotion, and mode changes;
- floating cards, markdown, code, images, video, charts, and app panels;
- scene objects, drawing, physics, music, browser, and terminal surfaces;
- desktop pointer, controller, and hand-tracking input.

Use the in-app DevTools panel to inspect messages and fire actions without
waiting for the Entity.

## Packages

| Package | Responsibility |
|---|---|
| `@ngram-ar/core` | Shell and Presence Protocol types |
| `@ngram-ar/runtime` | Shell loading, voice, and optional shell-local memory |
| `@ngram-ar/bindings` | Authenticated Entity bridge and compatibility inference binding |
| `@ngram-ar/embodiment` | Behaviors, animation state, and visemes |
| `@ngram-ar/gateway` | WebSocket/HTTP gateway and shell management |
| `@ngram-ar/surface-webxr` | Three.js desktop and immersive-AR surface |
| `@ngram-ar/cli` | Shell initialization, setup, and development server |

## Hardware support

Desktop browsers and Meta Quest Browser are the maintained targets. Other
headsets or glasses work only when their browser exposes the WebXR features the
surface uses. Android XR is therefore device/browser dependent rather than a
blanket compatibility claim. Meta AI glasses are not a WebXR display target.

## Environment

The guided setup writes these values for you:

| Variable | Where | Purpose |
|---|---|---|
| `NGRAM_AR_ENTITY_BRIDGE_URL` | shell environment | Python bridge URL |
| `NGRAM_AR_ENTITY_BRIDGE_TOKEN` | both processes | dedicated bearer credential |
| `NGRAM_AR_ENTITY_BRIDGE_PORT` | Python process | bridge listener port |
| `NGRAM_AR_PERSON_ID` | Python process | cross-surface relationship key |
| `NGRAM_AR_PERSON_NAME` | Python process | display name for that person |
| `NGRAM_AR_PORT` / `NGRAM_AR_HOST` | Node process | local surface bind |
| `NGRAM_AR_MOTION_PROVIDER_URL` | Node process | optional external GPU motion adapter |
| `NGRAM_AR_MOTION_PROVIDER_TOKEN` | Node process | server-side motion adapter credential |

Every final user turn includes a bounded `spatialContext` snapshot: surface
mode and capabilities, user pose and gaze, agent pose and animation, proximity,
the last intentional gesture, and scene counts. The persistent Entity receives
that observation as turn context. Harness tool activity and redacted results are
streamed to the spatial state indicator and terminal while the turn runs.

Generated motion is an optional compute provider, not part of the Entity. See
[`docs/SPATIAL_COMPUTE.md`](docs/SPATIAL_COMPUTE.md) for the adapter contract.

The legacy `openai` wire-format binding remains as an advanced stateless
compatibility mode. New shells and the browser workspace use
`ngram_entity`.

## Development

```bash
npm run lint
npm run build
npm run ngram-ar -- dev shells/canary
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [the documentation index](docs/README.md),
and [the protocol](docs/protocol.md).

## License

MIT. See [LICENSE](LICENSE).
