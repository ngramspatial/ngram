# Changelog

## Unreleased

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
