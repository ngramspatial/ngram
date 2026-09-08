# ngram

**The same agent, everywhere.**

ngram is an open engine for persistent agents with memory, relationships,
tools, an always-on interior, and replaceable bodies. One Entity can talk with
you in Telegram, Discord, or a terminal, then meet you in the room through a
WebXR shell without becoming a second character.

<p align="center">
  <img src="assets/branding/ngram.png" alt="ngram" width="320">
</p>

## What makes it different

Most agent frameworks organize work around a task or session. ngram organizes
the system around an individual.

- **Continuity:** identity, relationships, memory, and body state survive sessions.
- **Many surfaces:** CLI, Telegram, Discord, HTTP, and AR reach the same Entity.
- **Replaceable inference:** The recommended cloud setup uses a provider API for chat and memory embeddings, with the persistent Entity on Railway. Local and hybrid inference remain available.
- **Replaceable bodies:** a shell owns the model, animation, voice, and spatial behavior—not identity.
- **Portable state:** inspect, verify, export, recover, and move an Entity as an open `.ngram` container.
- **Bounded agency:** tools and autonomous behavior remain explicit and configurable.

```text
 Telegram ─┐
 Discord  ─┼──> persistent Entity ───> memory / soma / tools / judgment
 CLI      ─┤            │
 WebXR   ──┘            └──> spatial intent ───> replaceable AR shell
```

## Quick start

Requirements:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) only if you choose fully local inference
- Node.js 22+ only when you add the browser/AR body

```bash
git clone https://github.com/ngramspatial/ngram.git
cd ngram
uv sync --extra dev
uv run ngram setup
```

The wizard defaults to **Cloud**: hosted thinking and embeddings, a Railway worker, a dedicated Postgres memory database, and a persistent Linux workspace. It verifies the provider and pairs your AR body.

| Mode | What runs where | Cloudflare required? |
|---|---|---|
| **Cloud** (recommended) | API thinking + embeddings; Railway worker, Postgres, and persistent Linux files | No |
| **Hosted** (advanced) | Complete Entity runtime locally; only chat and embedding requests use your chosen API | No |
| **Local** | Entity runtime and Ollama both run on this machine | No |
| **Hybrid** (advanced) | Home inference gateway plus an always-on Railway worker | Yes, when exposing the home gateway publicly |

Hosted setup supports OpenAI, Venice, Anthropic, Gemini, OpenRouter, xAI, Groq,
Together, Fireworks, Mistral, DeepSeek, and custom OpenAI-compatible endpoints.
The provider credential stays in the gitignored local `.env`; it is never sent
to the browser. Venice includes working chat and 768-dimensional embedding
defaults, so it can be selected without manually composing a base URL.

Cloudflare is not part of normal first-run setup. It appears only when you
explicitly select `hybrid` or run `uv run ngram setup --profile hybrid`.

`uv run ngram` is the guaranteed source-checkout command. To install the bare
`ngram` launcher into your active Python environment instead, run
`python -m pip install --editable .` once.

When cloud setup finishes, open the AR app with the command it prints. **New ngram** guides you through worker pairing, memory settings, body, and voice. Creating another body for the same worker preserves the same identity and memories. Use a separate worker and database for a separate Entity.

For the optional local-runtime profiles:

```bash
uv run ngram talk <entity>            # hosted API
uv run ngram talk <entity> --ollama   # fully local
```

Run its configured messaging surfaces and autonomous heartbeat:

```bash
uv run ngram run <entity>             # hosted API
uv run ngram run <entity> --ollama    # fully local
```

## Give the Entity a body

For a thin Ubuntu host plus Meta Quest on the same trusted Wi-Fi, the guarded
lab launcher is the shortest path:

```bash
uv run ngram lab up <entity> --lan
```

On first run it prompts for the hosted chat model and API key, installs the
pinned WebXR dependencies, validates chat and 768-dimensional memory
embeddings, pairs an authenticated loopback Entity bridge, builds the surface,
and starts both processes. It does not start Ollama, Cloudflare, or any tunnel.
Only the HTTPS WebXR surface is exposed to the LAN. Ctrl+C stops both process
trees.

To pair the AR session with an existing Telegram relationship:

```bash
uv run ngram lab up <entity> --lan --person-id <telegram-user-id>
```

The manual body workflow remains available:

Install and build the WebXR workspace once:

```bash
npm run ar:install
npm run ar:build
```

Pair a shell with the same local or Railway Entity:

```bash
uv run ngram ar setup <entity>
npm run ar:dev -- shells/<entity>
```

Open `http://localhost:3000`. For Meta Quest passthrough AR, start with HTTPS
and open the printed network URL in Meta Quest Browser:

```bash
npm run ar:dev -- shells/<entity> --host 0.0.0.0
```

This exposes the AR surface to your LAN; use it only on a trusted network.

The setup flow creates a dedicated bridge credential, writes it only to ignored
environment files and deployment secrets, optionally pairs the same person ID
used by Telegram or Discord, and verifies the connection without generating a
conversation turn.

If you use Telegram, send `/whoami` to the Entity and enter that ID during AR
setup. The relationship is then the same on both surfaces.

## Shells are the shareable format

An AR shell is a directory centered on `shell.yaml`:

```yaml
name: My Entity
description: A grounded spatial presence.

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

voice:
  provider: edge
  voice: en-US-JennyNeural

binding:
  type: ngram_entity
  options: {}
  system: |
    Use spatial actions when they add meaning. Do not narrate body actions.
    Identity and memory remain those of the running Entity.
```

Create one from the terminal or the browser workspace:

```bash
npm --prefix ngramAR run ngram-ar -- init shells/my-entity
uv run ngram ar setup my-entity --shell ngramAR/shells/my-entity
```

Models can be FBX, GLB/glTF, or the built-in procedural avatar. See the
[shell guide](ngramAR/README.md#shell-definition) and
[Mixamo guide](ngramAR/docs/mixamo.md).

## Spatial tools and capabilities

During an `ngram_ar` turn, the Entity receives 28 native spatial function
tools. They are not a separate character or agent: the same Entity, memory,
relationships, and tool loop that answer on text surfaces also choose the body
and scene actions below. Actions are delivered to the connected surface while
the turn is running when possible, then safely buffered for end-of-turn
delivery if the live connection is interrupted.

### Body, expression, and speech

| Tool | Arguments | What it does |
|---|---|---|
| `ar_inspect_surface` | none | Returns the current surface, available spatial tools, and bounded live spatial context. |
| `ar_world` | `command`, `payload` | Build and program persistent spatial objects, physics, controls, and interactions. Start with `capabilities`. |
| `ar_blender` | `command`, `payload` | Edit Blender projects on the configured execution host and publish live Spatial previews. Blender must be installed on that host. |
| `ar_figment` | `command`, `payload` | Attach and configure portable objects with named parts, grips, properties and actions. Start with `capabilities`. |
| `ar_figment_physics` | `command`, `payload` | Tune mass, gravity, friction, bounce, damping, axis locks, compound collision shapes and sensors. |
| `ar_figment_behavior` | `command`, `payload` | Write local JavaScript behavior against named parts, properties, anchors and joints; pause, resume or reset it. |
| `ar_figment_interact` | `command`, `payload` | Set exposed properties and invoke the same actions available to people in the Objects panel. |
| `ar_figment_library` | `command`, `payload` | Publish immutable local versions, export files to share, import packages and place editable copies. |
| `ar_move_to` | `target`, `speed="walk"` | Moves relative to the user or current scene. Targets: `user`, `away`, `forward`, `left`, `right`, `random`. Speeds: `walk`, `fast`. |
| `ar_gesture` | `gesture` | Plays a semantic body gesture from the shell's animation pack, with a built-in fallback when no matching clip exists. |
| `ar_emote` | `emotion`, `intensity=0.6` | Expresses `attentive`, `calm`, `concerned`, `curious`, `excited`, `happy`, or `thoughtful`; intensity is clamped to `0..1`. |
| `ar_look_at` | `target` | Turns attention toward `user` or `away`. |
| `ar_go_idle` | none | Returns the body to its idle behavior. |
| `ar_speak` | `text` | Speaks visibly during a tool turn with text, TTS, and lip-sync; the Entity can still send its normal final reply afterward. |

`ar_gesture` accepts these exact semantic names: `wave`, `nod`, `point`,
`shrug`, `celebrate`, `explain`, `dance`, `texting`, `coding`, `enteringCode`,
`thinking`, `no`, `handRaising`, `terrified`, `drunkWalk`, `breakdancing`,
`twerking`, `macarena`, `hipHop`, `twistDance`, `cheering`, and `clapping`.

### Panels, terminal, browser, and media

| Tool | Arguments | What it does |
|---|---|---|
| `ar_show_panel` | `panel_id`, `content`, `title=""`, `panel_type="markdown"` | Opens or updates a persistent spatial panel. Types: `card`, `markdown`, `code`, `image`, `chart`, `html`. Content is limited to 20,000 characters. |
| `ar_hide_panel` | `panel_id` | Hides the identified spatial panel. |
| `ar_terminal` | `output`, `tool=""`, `error=false`, `clear=false` | Streams bounded command/tool output to the spatial terminal; it can append, mark an error, or clear the terminal. |
| `ar_open_browser` | `url`, `title=""` | Opens an `http` or `https` URL on the browser surface. |
| `ar_play_youtube` | `video`, `title=""`, `volume=50`, `start_at=0` | Plays a known YouTube video ID or watch, short, embed, or live URL. Volume is clamped to `0..100`; start time cannot be negative. |

`ar_play_youtube` is a player, not a search tool. The Entity must obtain a
specific video ID or URL first. General sites may refuse to render inside a
panel through `X-Frame-Options` or Content Security Policy; the dedicated
YouTube player and external-tab fallback handle the common media case.

### Objects, physics, and annotations

| Tool | Arguments | What it does |
|---|---|---|
| `ar_spawn_object` | `object_id`, `shape`, `position="front"`, `color`, `size=0.15`, `label=""`, `physics=true` | Spawns a primitive `cone`, `cube`, `cylinder`, `plane`, `sphere`, or `torus`. Size is clamped to `0.03..2` meters. |
| `ar_spawn_toy` | `object_id`, `toy_type="ball"`, `position="front"`, `color=""`, `impulse=null` | Spawns a physics-ready `ball`, `bouncy_ball`, `beach_ball`, `dice`, or `marble`. Optional impulse `{x,y,z}` components are clamped to `-20..20`. |
| `ar_spawn_text` | `object_id`, `text`, `position="front"`, `color="#fff"`, `size=1` | Places spatial text, up to 2,000 characters; size is clamped to `0.2..4`. |
| `ar_remove_object` | `object_id` | Removes a spawned primitive, toy, text item, image, or model by ID. |
| `ar_clear_objects` | none | Removes all spawned scene objects. |
| `ar_draw_annotation` | `drawing_id`, `text`, `x`, `y`, `z`, `color="#fff"` | Places a labeled callout at explicit scene coordinates. |

Named object positions are `above`, `front`, `here`, `left`, and `right`.
Objects use Rapier physics where requested and can be grabbed, moved, thrown,
and settled in desktop or XR interaction. Spawned objects, the selected
environment, placement, and pinned panels persist in the local browser.

### Environment, perception, and generated motion

| Tool | Arguments | What it does |
|---|---|---|
| `ar_set_environment` | `preset` | Selects `default`, `workshop`, `cozy`, `nature`, `space`, `party`, `focus`, or `night`. |
| `ar_request_capture` | `prompt=""` | Requests one user-controlled camera/view capture for visual understanding. It fails closed when vision sharing is disabled. |
| `ar_generate_motion` | `prompt`, `duration_seconds=4`, `root_target="stationary"`, `loop=false` | Requests a motion clip from the configured external motion provider, then plays it on the body. Duration is clamped to `0.5..30` seconds; root target is `stationary`, `user`, `forward`, `left`, or `right`. |

Capture is explicitly opt-in on the surface. Image data is bounded and
sanitized by the bridge, and provider credentials remain server-side.
Generated motion likewise requires a configured GPU-backed motion service;
without one, the tool returns a useful failure instead of inventing a clip.

### Spatial context available to the Entity

The surface sends a fresh, bounded spatial snapshot into the same Entity turn
as the user's message. It contains:

- surface mode (`desktop` or `ar`) and detected WebXR capabilities;
- user position, orientation, gaze direction, whether they face the Entity,
  and a recent intentional gesture;
- Entity visibility, position, distance, scale, animation, and speaking state;
- proximity and whether the user is approaching;
- scene object and anchor counts.

This is environmental context, not identity or system instruction. Image and
audio payloads are stripped from ordinary context; a visual frame is included
only through an approved capture. The browser currently reports AR hit-test,
controller/hand input, and spatial audio where supported. Eye tracking,
semantic plane/mesh detection, and persistent WebXR anchors are not currently
claimed capabilities.

### Presence Protocol surface capabilities

The native tools above are the stable model-facing API. The WebXR surface also
implements a broader action vocabulary for shell bindings, DevTools, and
future native clients:

| System | Supported surface actions |
|---|---|
| Presence | spawn/despawn, follow, face user, highlight, set mode, set agent state, and streaming speech start/delta/end |
| Body | move, look, gesture, emote, idle, and play a supplied motion clip |
| Panels/apps | show, update, hide, pin, resize, and spatially manipulate panels and app surfaces |
| Audio/media | play, pause, stop, and set audio volume; play and control YouTube |
| Scene | spawn primitives, text, images, toys, and models; remove or clear objects |
| Drawing | draw lines, arrows, and annotations; clear drawings |
| World | set environments, lighting, custom backgrounds, and particle effects; clear environment additions |
| Browser | open, close, navigate, go back, and go forward |
| Perception | request an opt-in view capture and report gaze, proximity, gesture, and scene changes |

These protocol actions are renderer capabilities, not automatically available
as first-class model tools. A binding may expose more of them deliberately,
but the Entity receives only the 21 `ar_*` tools listed above by default. All
`ar_*` tools fail as harmless no-ops on non-spatial surfaces, so the same
Entity can continue a relationship through Telegram, Discord, the terminal,
the web, and AR without trying to move a body that is not present.

## Surfaces and hardware

| Surface | Status |
|---|---|
| Desktop browser | Supported |
| Meta Quest Browser passthrough AR | Supported |
| Mobile WebXR | Browser/device dependent |
| Android XR glasses | Browser and WebXR feature dependent; not universal |
| Meta AI glasses | Not supported; they do not expose a general WebXR display surface |

ngram AR is web-native. A device works when its browser exposes the required
WebXR immersive-AR, hit-test, audio, and input capabilities. Native Quest,
Android XR, and visionOS surfaces can implement the same Presence Protocol in
the future without changing the Entity.

## Architecture

The Python runtime has five cooperating pillars:

- `cognition` routes turns, builds context, and runs bounded tool loops.
- `identity` holds personality, drives, emotions, voice, and evolution.
- `memory` stores episodes, relationships, beliefs, knowledge, and projects.
- `soma` maintains tonic body state and autonomous internal signals.
- `presence` connects platforms, tools, scheduled work, and autonomous wake.

ngram AR adds three spatial layers:

- the **binding** connects the WebXR gateway to the canonical Python Entity;
- the **shell** maps semantic intent into voice, animation, and behavior;
- the **surface** renders the shell in a browser or headset.

The wire format is documented in the
[Presence Protocol](ngramAR/docs/protocol.md).

## Portable Entities and Studio

```bash
uv run ngram init <entity> ./entity.ngram
uv run ngram verify ./entity.ngram
uv run ngram export <entity> ./backups
uv run ngram open ./backups/entity.ngram ./open-entity.ngram
uv run ngram studio ./open-entity.ngram
```

The current container profile is inspectable and integrity-checked, but it is
not encrypted or cryptographically signed. Credentials are excluded.

## Development

```bash
uv run pytest
uv run ruff check ngram tests
npm run ar:lint
npm run ar:build
```

Repository layout:

```text
ngram/          Python Entity runtime and CLI
ngramAR/        WebXR surface, gateway, shell runtime, and examples
configs/        Runtime defaults and example Entities
tests/          Offline-first Python regression suite
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[v1.0.0 changelog](CHANGELOG.md).

## Runtime controls

Use **Pause inference** in the spatial surface, or `/pause` from a configured
Telegram operator, to cancel active model work and block new chat, embedding,
and background inference for the shared Entity. `/resume` or the spatial
**Resume** control restores model use. The pause marker is stored beside the
Entity's journal and survives restarts when that storage persists.

Closing a surface leaves the Entity available to other surfaces. **Stop response**
cancels the current spatial work; **Pause inference** also stops future background
model requests. Requests already processed by a provider can still be billed.

`/compact` summarizes older conversation turns while retaining recent context.
`/reset` clears the live conversation. Neither command deletes durable memory.
`/context` reports the active model's effective working budget and approximate
usage; automatic compaction reports its progress in Telegram and spatial.

## License

ngram, including the Python runtime and `ngramAR`, is licensed under the
[MIT License](LICENSE). Third-party dependencies and model assets retain
their own licenses and notices.
