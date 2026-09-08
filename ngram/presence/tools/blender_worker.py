"""Runs inside headless Blender. All bpy access stays on Blender's main thread."""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

MARKER = "NGRAM_BLENDER:"
directory = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
latest_file = directory / "latest.json"
revision = json.loads(latest_file.read_text())["revision"] if latest_file.is_file() else 0
source_file = directory / "revisions" / str(revision) / "project.blend" if revision else directory / "source.blend"
if source_file.is_file():
    bpy.ops.wm.open_mainfile(filepath=str(source_file), load_ui=False, use_scripts=False)
else:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
bpy.context.preferences.filepaths.save_version = 0


def emit(message):
    print(MARKER + json.dumps(message, ensure_ascii=True, allow_nan=False), flush=True)


def publish():
    """Explicit checkpoints also stream during a single long agent script."""
    global revision
    bpy.context.view_layer.update()
    revision += 1
    output = directory / "revisions" / str(revision)
    # An interrupted export may leave an unpublished directory. Never reuse it.
    while output.exists():
        revision += 1
        output = directory / "revisions" / str(revision)
    output.mkdir(parents=True)
    bpy.ops.export_scene.gltf(
        filepath=str(output / "preview.glb"), export_format="GLB", export_yup=True,
        export_apply=True, use_visible=True, export_extras=True,
        export_animations=True, export_cameras=False, export_lights=False,
    )
    preview = output / "preview.glb"
    if preview.stat().st_size > 32 * 1024 * 1024:
        raise RuntimeError("Preview exceeds 32 MB; simplify geometry or textures before publishing")
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "project.blend"), check_existing=False)
    snapshot = {"revision": revision, "published_at": time.time(), "bytes": preview.stat().st_size,
                "objects": [{"name": obj.name, "type": obj.type,
                             "location": list(obj.location), "dimensions": list(obj.dimensions)}
                            for obj in list(bpy.context.scene.objects)[:256]],
                "object_count": len(bpy.context.scene.objects), "coordinates": "Blender Z-up; GLB preview Y-up; metres"}
    text = json.dumps(snapshot)
    (output / "snapshot.json").write_text(text, encoding="utf-8")
    temporary = directory / "latest.tmp"
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, latest_file)
    emit({"type": "preview", **snapshot})
    return snapshot


scope = {"bpy": bpy, "publish": publish, "__name__": "__ngram_blender__"}
for line in sys.stdin:
    try:
        command = json.loads(line)
        scope.pop("result", None)
        exec(compile(command["source"], "<agent-blender-edit>", "exec"), scope)
        # Publish the final state even if the script emitted earlier checkpoints.
        publish()
        result = scope.get("result")
        encoded = json.dumps(result, allow_nan=False)
        if len(encoded.encode()) > 32000:
            raise RuntimeError("Inspection result exceeds 32 KB; return a smaller selection")
        emit({"type": "result", "ok": True, "result": result})
    except BaseException:
        emit({"type": "result", "ok": False, "error": traceback.format_exc()[-4000:]})
