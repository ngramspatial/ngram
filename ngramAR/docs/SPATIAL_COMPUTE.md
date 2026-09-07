# Spatial compute providers

ngram keeps cognition and motion generation separate:

```text
persistent Entity (identity, memory, tools)
        | spatial intentions
        v
ngram AR gateway (policy, credentials, voice, provider routing)
        | authenticated motion request
        v
external GPU adapter (ARDY/other model, retargeting, clip export)
        | signed FBX/GLB URL
        v
WebXR surface (load, retarget, blend, render)
```

The Entity requests motion with `ar_generate_motion`. The gateway is the only
component that talks to the GPU service. This keeps provider credentials out of
the browser and lets deployments replace ARDY without changing an agent or
shell.

## Gateway configuration

```dotenv
NGRAM_AR_MOTION_PROVIDER_URL=https://motion.example
NGRAM_AR_MOTION_PROVIDER_TOKEN=replace-me
NGRAM_AR_MOTION_PROVIDER_TIMEOUT_MS=120000
```

With no provider URL, the rest of spatial ngram works normally. A generated
motion request returns a visible configuration error.

## Provider request

The gateway calls `POST /v1/motion`:

```json
{
  "protocolVersion": "1.0",
  "requestId": "unique-id",
  "prompt": "take a careful step forward and wave",
  "durationSeconds": 4,
  "constraints": { "rootTarget": "forward" },
  "output": {
    "skeleton": "mixamo-humanoid",
    "formats": ["glb", "fbx"],
    "delivery": "url"
  }
}
```

The adapter should translate these high-level constraints into model-native
inputs. An ARDY adapter can add path waypoints, body keyframes, joint
constraints, or motion history internally without changing the public contract.

## Provider response

```json
{
  "requestId": "unique-id",
  "clip": {
    "url": "https://signed-object-url.example/motion.glb",
    "format": "glb",
    "name": "careful-step-wave",
    "loop": false
  }
}
```

The clip URL must be HTTP(S), browser-readable with CORS, short-lived when
signed, and contain one retargetable humanoid animation. The current surface
retargets Mixamo bone namespaces and cross-fades the generated clip onto the
loaded body.

## RunPod/ARDY adapter responsibilities

The GPU service should:

1. keep ARDY and its text encoder warm;
2. validate duration, prompt, and constraints;
3. run inference and retarget the generated skeleton to Mixamo humanoid bones;
4. export a compact FBX or GLB animation clip;
5. upload it to short-lived object storage and return a signed URL;
6. expose its own readiness and metrics endpoints.

Raw ARDY tensors, checkpoints, gated-model credentials, and RunPod API keys do
not belong in the ngram repository or browser. Model and data licenses remain
the deployer's responsibility.
