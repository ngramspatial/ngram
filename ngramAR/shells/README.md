# Shells

A shell is a replaceable body for a persistent ngram Entity. It owns the model,
animations, voice, spatial behaviors, and presentation surfaces. It does not own
identity or conversational memory.

- `canary/` is the full-body example paired with `configs/entities/canary.example.yaml`.
- `rook/` is a second character example using a lightweight Mixamo body.

Create another shell with:

```bash
npm run ngram-ar -- init shells/my-entity
npm run ngram-ar -- setup my-entity --shell shells/my-entity
```

Place licensed, non-redistributable assets under `models/private/`; that directory
is ignored by Git. Reference the private path from your local `shell.yaml` only if
you do not intend to commit the shell configuration.
