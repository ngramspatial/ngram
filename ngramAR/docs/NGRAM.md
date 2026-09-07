# Connecting ngram AR to an Entity

The default architecture gives an existing Python `Entity` another surface.
It does not instantiate a second agent in Node.

## Guided setup

From the repository root:

```bash
uv run ngram ar setup <entity>
```

Or from `ngramAR/`:

```bash
npm run ngram-ar -- setup <entity>
```

The wizard supports a local Entity or a linked Railway worker. It preserves
creative shell fields, creates a dedicated credential, configures the bridge,
and performs a non-conversational verification handshake.

## Local connection

Set the Python process environment:

```dotenv
NGRAM_AR_ENTITY_BRIDGE_HOST=127.0.0.1
NGRAM_AR_ENTITY_BRIDGE_PORT=7878
NGRAM_AR_ENTITY_BRIDGE_TOKEN=replace-with-a-random-secret
NGRAM_AR_PERSON_ID=your-canonical-person-id
NGRAM_AR_PERSON_NAME=You
```

Start the Entity:

```bash
uv run ngram run <entity> --ollama
```

Set the shell environment in `shells/<name>/.env`:

```dotenv
NGRAM_AR_ENTITY_BRIDGE_URL=ws://127.0.0.1:7878/
NGRAM_AR_ENTITY_BRIDGE_TOKEN=replace-with-the-same-random-secret
```

The shell binding is:

```yaml
binding:
  type: ngram_entity
  options: {}
  system: |
    Use spatial actions when they add meaning.
    Identity and memory remain those of the running Entity.
```

Then run:

```bash
npm run ngram-ar -- dev shells/<name>
```

## Railway connection

The wizard can auto-detect the worker in the currently linked Railway project.
It configures:

- bridge host and port on the Entity worker;
- a unique bridge token through stdin;
- the canonical person ID and display name;
- a public service domain when needed;
- the local shell's `wss://` URL and token.

Only the authenticated WebSocket route handles sessions. `/health` is public
and non-identifying. The token is sent in an `Authorization: Bearer` header,
not in a URL.

The WebXR app still runs locally; the Python Railway image intentionally
excludes `ngramAR/`.

## Relationship continuity

Every surface supplies a person key to `Entity.perceive`. Use the same key in
AR to share the relationship already built on a messaging platform.

For Telegram, send `/whoami`. For Discord, use the canonical platform user ID.
If no value is provided, AR uses a separate `ar_user` relationship.

## Turn consistency

All full Entity turns share one asynchronous lock. Telegram, Discord, AR,
autonomy, and CLI cannot mutate history, relationships, soma, or routes at the
same time. This keeps the Entity canonical even when several surfaces are
active.

## Advanced stateless mode

`binding.type: openai` calls the ngram OpenAI-compatible inference gateway
without loading the full Entity. It is retained for compatibility and isolated
model/shell testing. It does not provide the same identity, memory,
relationships, or tools and is not the default product path.

## Troubleshooting

- **Connection refused:** the Entity bridge is not running or the URL/port is wrong.
- **403:** the shell and Entity bridge tokens do not match.
- **No shared relationship:** set `NGRAM_AR_PERSON_ID` to the messaging user key.
- **Quest cannot connect:** use HTTPS/WSS and a network-reachable host or domain.
- **Shell does not load:** run `npm run build` and check model paths relative to the shell.
