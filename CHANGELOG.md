# Changelog

## Unreleased

- Preserve complete tool-call groups when trimming conversation history. Recover orphaned or replayed results before sending Responses API input, keep their evidence as historical context, and mark missing execution outcomes explicitly instead of failing subsequent turns with a 400 after compaction.
- Shape the whole scene with `ar_environment`: procedural skies, panoramic image/HDR/EXR backgrounds, material reflections, directional and ambient lighting, fog, exposure, and ground visibility. Failed sky loads preserve the last working environment; saved scenes retain their settings. Native Settings exposes the same sky and lighting controls, with passthrough preserved by default in XR.
- Render 360-degree panoramas from persistent Blender projects on the existing execution host. Agents receive the image and a skybox payload they can apply directly in Spatial through the authenticated gateway, without external image storage or model polling.
- Keep Figments clear of visual clutter: grip guides start hidden, can be enabled per object, and use fine periwinkle outlines. Clicking empty space or outside the Objects inspector deselects creations; inspector edits and active grabs retain their selection.
- Recover durable coding tasks when the presence daemon starts and allow cloud workers to use their own Railway execution workspace. Keep local coin reference assets out of source control.
- Keep the work-status panel out of ordinary chat and quick tool calls. Show coding goals, execution lasting at least 15 seconds, or multiple authoring steps spanning 30 seconds; retain status across subsequent work phases and hide it immediately on completion. Place speech captions and activity in one vertical layout so expanded details cannot cover messages.
- Show observed model requests, retries, tool execution and Blender rendering above the Spatial message box, with elapsed time, request limits and recent stages. Heartbeats identify a live worker without claiming new progress; disconnected or silent telemetry is marked explicitly. Active work is replayed on reconnect, and coding goals have separate activity from chat.
- Replace the blocking multi-phase coding tool with durable background goals. Goals recover saved work after restarts, support status/resume/cancel, pause when their runtime budget expires (six hours by default), and require tool evidence plus a fresh verification phase before completion.
- Give agents visual feedback from Blender renders and independent Spatial inspection cameras, and support private message attachments stored on the existing execution worker.

## 1.0.1 - 2026-09-08

### The Figment Update

Objects can carry their own behavior. Attach a Figment to a Blender model or Spatial assembly, name its parts and grips, give it adjustable physics, and let people operate it directly. Publish a complete version and share an editable copy.

- Added a five-tool Figment bundle for authoring, physics, local behavior, human-compatible interactions, and portable publishing. Native Spatial now exposes 28 tools.
- Added typed object properties, named actions, stable mesh-node anchors, one- and two-hand grips, and scoped JavaScript using named part and joint bindings.
- Extended physics to Blender assets and compound assemblies: mass, gravity, friction, bounce, linear/angular damping, axis locks, collision masks, convex shapes and sensors. Contact events include measured impact data and ground contacts.
- Added Figment controls to the existing Objects drawer, including editable physical properties, grips, motor/constraint settings, behavior source and version publishing. Whole assemblies duplicate with independent IDs; imports and restored behavior remain paused.
- Added self-contained `.figment.json` packages, SHA-256 integrity checks, an IndexedDB library, and editable Blender source retention. Publishing creates a local immutable version; sharing uses exported files.
- Kept simulation local: actions, physics and behavior do not trigger model turns. Routine observations omit source, package export returns a small receipt, and Stop cancels pending package downloads and local programs.

Update the Python worker and Spatial client together. Headset grip paths have automated pose/input coverage; headset feel and scanned-room collision are not claimed. See the separate [Figment guide](https://docs.ngram.space/spatial/figments).

### Spatial creations and Blender

Agents can build objects that people can pick up, reshape, and operate. Blender projects publish real geometry into Spatial as the agent works; local programs give those creations behavior after the conversation ends.

- Added `ar_world`: persistent creation worlds with validated batches, custom meshes and instancing, groups, materials, lights, physics bodies, joints, controls, events, and structured results.
- Added `ar_blender`: persistent headless Blender projects on the configured execution host, intermediate GLB previews, editable `.blend` checkpoints, authenticated artifact delivery, and cancellable work.
- Preserve human position, rotation, and scale across Blender revisions; queue previews while an object is held and keep the old mesh visible during replacement.
- Added scoped local JavaScript tick/event programs with pause/resume, persisted state, watchdogs, and no per-frame model calls.
- Added the native Objects drawer, desktop/touch and XR ray interaction, two-pointer manipulation, scene buttons/sliders, transform editing, undo/redo, and local world import/export.
- Added kinetic workshop and resonance garden examples built through the public creation API.
- Preserve saved worlds when restoration fails and retain orphaned program source for repair. Restored programs remain paused.
- Fixed the retry guard so distinct operations through one tool can continue; repeated identical requests remain bounded. Agent Blender stops also update the surface's project status.

Update the Python worker, execution backend, and Spatial gateway together. Blender must be installed on the execution host. World exports reference external assets; back up Blender artifacts separately. The new workflow is documented in the separate [Spatial guides](https://docs.ngram.space/spatial/creations).

### Runtime

- Added an antialiased ground grid that adapts to the scene background and fades unresolved lines near the horizon.
- Aligned application, CLI, API and workspace package versions at 1.0.1.

- Changed the Python runtime and root project license to MIT, matching `ngramAR`.

- Added a persistent, shared inference pause with Telegram operator commands and a spatial pause/resume control. Pausing cancels active model work and blocks new chat, embedding, and background requests.
- Fixed Telegram `/compact` to summarize older turns instead of clearing history, without blocking incoming control commands.
- Made Telegram context displays follow the active provider's working budget and report automatic compaction progress.

- Added a one-command, trusted-LAN Quest lab bootstrap and process supervisor.
- Made hosted inference self-contained across chat and memory embeddings, with live readiness probes and memory-width validation.
- Added server-side provider profiles and a fully hosted memory selector to the WebXR settings UI.
- Preserved legacy hosted profiles as an explicit existing-embedding route.

## 1.0.0 - 2026-09-03

The first cohesive ngram release.

- Unified the persistent Python Entity runtime and WebXR embodiment stack.
- Added first-class local and Railway AR pairing with dedicated bearer credentials.
- Preserved one relationship identity across Telegram, Discord, CLI, and AR.
- Serialized cross-surface turns to protect canonical memory and body state.
- Made `ngram_entity` the default shell binding.
- Changed browser creation from a second agent runtime into a body-only shell flow.
- Added portable Entity containers, Studio, Entity Factory, and governed change proposals.
- Consolidated documentation, examples, branding, release metadata, and CI.
