# Contributing

ngram has a Python Entity runtime and a TypeScript WebXR runtime. Keep changes
focused and update both sides when a Presence Protocol contract changes.

## Setup

```bash
uv sync --extra dev
npm run ar:install
```

## Checks

```bash
uv run pytest
uv run ruff check ngram tests
npm run ar:lint
npm run ar:build
```

Do not commit `.env`, Entity runtime state, chat transcripts, credentials, or
licensed private avatar assets. New configuration keys must be implemented in
code and documented. New shells should keep identity and conversational memory
in the Entity rather than duplicating them in `shell.yaml`.

See [ngramAR/CONTRIBUTING.md](ngramAR/CONTRIBUTING.md) for spatial-runtime details.
