# Using Mixamo Characters with ngram AR

This guide walks you through downloading a Mixamo character and animations, converting them to GLB, and using them as a spatial shell in ngram AR.

## Step 1: Download from Mixamo

1. Go to [mixamo.com](https://www.mixamo.com) and sign in with a free Adobe account
2. **Character**: Select **Y Bot** (or any character you like)
3. **Download the character with skin**:
   - Click "Download"
   - Format: **FBX Binary (.fbx)**
   - Pose: **T-Pose**
   - Save as e.g. `Y_Bot.fbx`
4. **Download animations** (each as a separate FBX):

   | Animation | Mixamo search term | Download setting |
   |---|---|---|
   | Idle | "Breathing Idle" or "Idle" | Without Skin |
   | Talking | "Talking" or "Gesture" | Without Skin |
   | Waving | "Waving" | Without Skin |
   | Thinking | "Thinking" | Without Skin |
   | Walking | "Walking" | Without Skin |

   For each: click Download → Format: FBX Binary → **Without Skin** → Download

5. Put all FBX files in a single folder, e.g. `downloads/mixamo/`

## Step 2: Convert to GLB

### Option A: Blender Script (Recommended)

Requires [Blender](https://www.blender.org/download/) installed (3.x or 4.x).

```bash
blender --background --python scripts/mixamo-to-glb.py -- ./downloads/mixamo ./shells/my-entity/models/character.glb
```

The script imports the character mesh from the first FBX alphabetically, imports each additional FBX as a named animation clip, and exports a single GLB with all clips embedded.

**Important**: Make sure the character FBX (with skin) sorts first alphabetically. Rename it if needed (e.g. prefix with `00_`).

### Option B: Manual Blender Workflow

1. Open Blender, delete default objects
2. File → Import → FBX → select your character FBX (with skin)
3. For each animation FBX:
   - File → Import → FBX → select the animation FBX
   - In the NLA Editor, rename the action strip to something meaningful (e.g. "Idle", "Talking")
4. File → Export → glTF 2.0 (.glb)
   - Format: GLB
   - Enable: Animations, Skinning
   - Export

### Option C: Online Converter

Use [gltf.report](https://gltf.report/) or [glTF-Transform](https://gltf-transform.dev/) to merge and convert.

## Step 3: Configure shell.yaml

Edit `shells/my-entity/shell.yaml`:

```yaml
model: models/character.glb
scale: 0.01

animationPack:
  idle: Idle
  talking: Talking
  waving: Waving
  thinking: Thinking
  walking: Walking
```

The `animationPack` maps ngram AR animation states to the clip names in your GLB file. The clip names must match exactly what's in the GLB (case-sensitive). The script names clips after the FBX filename (spaces replaced with underscores).

Set `model: default` to switch back to the procedural holographic avatar.

## Step 4: Run

```bash
npm run ngram-ar -- dev shells/my-entity
```

The avatar will load the GLB model and play the mapped animations in response to spatial actions from the agent.

## Troubleshooting

- **Model too big/small**: Adjust `scale` in shell.yaml. Mixamo models are ~180 units tall, so a scale of 0.008–0.01 usually works.
- **Animations not playing**: Check that clip names in `animationPack` match the names in the GLB. Open the GLB in [gltf.report](https://gltf.report/) to inspect clip names.
- **Model loads but is invisible**: Check your browser console for WebGL errors. Ensure the GLB file is under 20MB.
- **Falls back to procedural avatar**: The client logs a warning if GLB loading fails. Check the browser console.
