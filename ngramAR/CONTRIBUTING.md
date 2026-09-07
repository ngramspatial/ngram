# Contributing to ngram AR

Thank you for your interest in contributing to ngram AR. This guide covers development setup, architecture, and how to make meaningful contributions.

## Development Setup

### Prerequisites

- **Node.js** >= 22 ([download](https://nodejs.org/))
- **npm** >= 10
- A local or Railway **ngram Entity** to use for end-to-end testing

### Getting Started

```bash
cd ngram/ngramAR
npm install
npm run build
```

### Running Locally

```bash
# Pair the example shell, then start the dev server
npm run ngram-ar -- setup canary
npm run ngram-ar -- dev shells/canary
```

Open `http://localhost:3000` in your browser. Open DevTools with `Ctrl+Shift+D` to see the Protocol Inspector.

### Build Commands

```bash
npm run build     # Build all packages (via Turborepo)
npm run clean     # Remove all dist/ directories
npm run lint      # Lint all packages
```

Individual packages can be built from their directory:

```bash
cd packages/core
npm run build
```

## Architecture

ngram AR is a monorepo using npm workspaces and Turborepo. The codebase is organized into seven packages, each with a single responsibility.

### Package Dependency Graph

```
                    @ngram-ar/core
                    (types, protocol)
                     /      |      \
                    /       |       \
   @ngram-ar/bindings  @ngram-ar/runtime  @ngram-ar/embodiment
   (Entity bridge)       (shell loader,       (behaviors,
                          voice, memory)       animations)
          \                 |                 /
           \                |                /
            @ngram-ar/gateway
            (WebSocket server, HTTP API,
             routes between bindings and surfaces)
                            |
            @ngram-ar/surface-webxr
            (Three.js WebXR client)
                            |
                @ngram-ar/cli
                (developer CLI)
```

### `@ngram-ar/core`
Shared TypeScript types and the Spatial Action API definition. All other packages depend on this. Contains:
- `protocol.ts` — All message types (SpatialAction, ShellEvent, etc.)
- `types.ts` — ShellDefinition, BindingConfig, VoiceProfile, etc.

### `@ngram-ar/bindings`
Authenticated persistent-Entity bridge plus a stateless inference compatibility binding.

### `@ngram-ar/runtime`
Shell loading and runtime services:
- `shell-loader.ts` — Parses `shell.yaml` into a `ShellDefinition`
- `voice.ts` — shell voice synthesis
- `memory.ts` — optional shell-local memory for compatibility mode

### `@ngram-ar/gateway`
The server that ties everything together:
- WebSocket server at `/ws` for the Presence Protocol
- HTTP API for transcription, shell creation, and configuration
- Routes events from surfaces to bindings and actions back to surfaces
- Voice synthesis integration

### `@ngram-ar/surface-webxr`
The Three.js client application:
- `main.ts` — Application entry point and action dispatch
- `scene-setup.ts` — Scene, camera, lighting, environment map, theme-aware rendering
- `avatar.ts` — 3D model loading, animation mixer, gaze IK, walking
- `ui.ts` — DOM UI management, devtools panel, modal system
- `xr-manager.ts` — WebXR session, hit-testing, AR placement
- `connection.ts` — WebSocket connection manager
- `speech.ts` — Audio playback, Web Speech API, microphone recording
- `hand-tracker.ts` — Hand tracking gesture detection
- `spatial-ui.ts` — 3D canvas-based UI for AR mode

### `@ngram-ar/cli`
Developer-facing command-line tool:
- `init` — Scaffold a new shell directory
- `dev` — Start the gateway + surface server
- `shell list` — List available shells

## How to Contribute

### Build a Shell
The most immediate contribution. Design a new spatial presence:

1. Run `npm run ngram-ar -- init my-shell`
2. Customize `shell.yaml` — model, voice, animations, and behaviors
3. Add a 3D model (FBX or GLB) and animation clips
4. Test with `npm run ngram-ar -- dev my-shell`
5. Submit a PR only when every included asset is redistributable

### Build a Behavior
Implement spatial rules for how agents exist in space:
- Look-at behaviors, idle animations, proximity responses
- Implement the `Behavior` interface in `packages/embodiment/`

### Improve the Workspace UI
The desktop experience lives in `packages/surface-webxr/`:
- `public/index.html` — All CSS and HTML structure
- `src/ui.ts` — DOM interaction logic, panels, modals, devtools

### Improve the Protocol
Propose new spatial actions or events:
1. Add the type to `packages/core/src/protocol.ts`
2. Handle it in the gateway (`packages/gateway/src/server.ts`)
3. Implement rendering in the surface (`packages/surface-webxr/src/main.ts`)
4. Update `docs/protocol.md`

## Code Style

- **TypeScript** throughout. Strict mode enabled.
- **No comments that narrate code.** Comments should explain non-obvious intent only.
- **Monospace font for code, Lora for display, DM Sans for body** in the UI.
- CSS uses custom properties for theming. All colors go through `--var` tokens.
- Prefer `const` over `let`. Avoid `any` where possible.

## Testing

Currently manual testing via the workspace UI and DevTools Protocol Inspector. Run the dev server and use the Action Tester to verify spatial behaviors.

## Pull Requests

- Keep PRs focused on a single concern
- Include a clear description of what changed and why
- Test in both light and dark mode
- If you change the protocol, update `docs/protocol.md`
- If you add a binding, include a note in the README

## Questions?

Open an issue on GitHub. We're happy to help.
