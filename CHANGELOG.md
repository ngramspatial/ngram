# Changelog

## Unreleased

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
