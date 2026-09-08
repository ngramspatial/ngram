"""Image inspection inside Blender; importing this module does not launch Blender."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time
import uuid


def render_options(value: dict | None = None) -> dict:
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise ValueError("render options must be an object")
    allowed = {
        "camera",
        "objects",
        "position",
        "look_at",
        "orbit",
        "distance",
        "size",
        "samples",
        "style",
        "projection",
    }
    if set(value) - allowed:
        raise ValueError(f"Unknown render options: {sorted(set(value) - allowed)}")
    out = {"size": 1024, "samples": 32, "style": "studio", "projection": "perspective", **value}
    for key, lo, hi in [("size", 256, 1536), ("samples", 1, 256)]:
        if type(out[key]) is not int or not lo <= out[key] <= hi:
            raise ValueError(f"{key} must be an integer in {lo}..{hi}")
    if out["style"] not in {"scene", "studio", "clay"} or out["projection"] not in {
        "perspective",
        "orthographic",
    }:
        raise ValueError("Unknown render style or projection")
    if "camera" in out and (
        not isinstance(out["camera"], str) or not out["camera"] or len(out["camera"]) > 160
    ):
        raise ValueError("camera must name an authored camera")
    if "objects" in out and (
        not isinstance(out["objects"], list)
        or not 1 <= len(out["objects"]) <= 64
        or any(not isinstance(n, str) or not n or len(n) > 160 for n in out["objects"])
    ):
        raise ValueError("objects needs 1..64 Blender object names")
    for key in ("position", "look_at", "orbit"):
        if key in out:
            v = out[key]
            if (
                not isinstance(v, (list, tuple))
                or len(v) != (2 if key == "orbit" else 3)
                or any(
                    type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 10000 for n in v
                )
            ):
                raise ValueError(f"Invalid {key} vector")
    if "orbit" in out and abs(out["orbit"][1]) > 90:
        raise ValueError("Orbit elevation must be -90..90 degrees")
    if "distance" in out and (
        type(out["distance"]) not in (int, float)
        or not math.isfinite(out["distance"])
        or not 0.01 <= out["distance"] <= 10000
    ):
        raise ValueError("distance must be .01..10000 metres")
    return out


def render_view(directory: Path, revision: int, **kwargs) -> dict:
    """Render the current in-memory scene and restore every temporary review setting."""
    import bpy
    from mathutils import Vector

    options = render_options(kwargs)
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    authored = list(scene.objects)
    objects = (
        [scene.objects.get(name) for name in options["objects"]]
        if "objects" in options
        else [
            o
            for o in authored
            if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT", "VOLUME"}
            and not o.hide_render
        ]
    )
    if not objects or any(o is None for o in objects):
        raise ValueError("Render target is empty or an object name is missing")
    points = [o.matrix_world @ Vector(corner) for o in objects for corner in o.bound_box]
    low = Vector([min(p[i] for p in points) for i in range(3)])
    high = Vector([max(p[i] for p in points) for i in range(3)])
    center = Vector(options.get("look_at", (low + high) / 2))
    radius = max(0.01, (high - low).length / 2)
    camera_source = scene.objects.get(options["camera"]) if options.get("camera") else None
    if options.get("camera") and (not camera_source or camera_source.type != "CAMERA"):
        raise ValueError("Authored camera was not found")
    temporary, datablocks, restore = [], [], []
    previous_camera, previous_world = scene.camera, scene.world
    previous_override = bpy.context.view_layer.material_override
    image_settings = scene.render.image_settings
    previous_image_settings = {
        key: getattr(image_settings, key)
        for key in ("file_format", "color_mode", "color_depth", "quality")
    }

    def set_value(owner, key, value):
        restore.append((owner, key, getattr(owner, key)))
        setattr(owner, key, value)

    def add_object(name, data):
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        temporary.append(obj)
        return obj

    ident = uuid.uuid4().hex
    output = directory / "renders" / ident
    output.mkdir(parents=True)
    try:
        if "objects" in options:
            keep = set(objects)
            for obj in authored:
                if obj.type not in {"LIGHT", "CAMERA"} and obj not in keep:
                    set_value(obj, "hide_render", True)
        data = (
            camera_source.data.copy()
            if camera_source
            else bpy.data.cameras.new("ngram-review-camera")
        )
        datablocks.append((bpy.data.cameras, data))
        camera = add_object("ngram-review-camera", data)
        if camera_source and not any(
            key in options for key in ("position", "look_at", "orbit", "distance")
        ):
            camera.matrix_world = camera_source.matrix_world.copy()
        else:
            azimuth, elevation = map(math.radians, options.get("orbit", [35, 20]))
            direction = Vector(
                (
                    math.sin(azimuth) * math.cos(elevation),
                    -math.cos(azimuth) * math.cos(elevation),
                    math.sin(elevation),
                )
            )
            distance = options.get("distance", radius / math.sin(math.radians(20)) * 1.15)
            camera.location = (
                Vector(options["position"])
                if "position" in options
                else center + direction * distance
            )
            if (center - camera.location).length < 0.000001:
                raise ValueError("Camera position must differ from look_at")
            camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
            data.lens = 45
        data.type = "ORTHO" if options["projection"] == "orthographic" else "PERSP"
        data.ortho_scale = radius * 2.3
        data.clip_start = 0.001
        data.clip_end = max(100, (camera.location - center).length + radius * 4)
        scene.camera = camera
        if options["style"] != "scene":
            for obj in authored:
                if obj.type == "LIGHT":
                    set_value(obj, "hide_render", True)
            world = bpy.data.worlds.new("ngram-review-world")
            world.use_nodes = True
            datablocks.append((bpy.data.worlds, world))
            background = world.node_tree.nodes.get("Background")
            background.inputs["Color"].default_value = (0.18, 0.20, 0.25, 1)
            background.inputs["Strength"].default_value = 0.35
            scene.world = world
            for index, (direction, energy, size) in enumerate(
                [((2, -3, 3), 650, 3), ((-3, -1, 1), 400, 2), ((0, 3, 2), 800, 2)]
            ):
                light_data = bpy.data.lights.new(f"ngram-review-light-{index}", "AREA")
                datablocks.append((bpy.data.lights, light_data))
                light_data.energy = energy * max(0.01, radius * radius)
                light_data.shape = "DISK"
                light_data.size = size * radius
                light = add_object(light_data.name, light_data)
                light.location = center + Vector(direction) * radius
                light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
        if options["style"] == "clay":
            material = bpy.data.materials.new("ngram-review-clay")
            material.use_nodes = True
            datablocks.append((bpy.data.materials, material))
            shader = material.node_tree.nodes.get("Principled BSDF")
            shader.inputs["Base Color"].default_value = (0.45, 0.47, 0.51, 1)
            shader.inputs["Roughness"].default_value = 0.65
            bpy.context.view_layer.material_override = material
        for key, value in {
            "engine": "CYCLES",
            "resolution_x": options["size"],
            "resolution_y": options["size"],
            "resolution_percentage": 100,
            "film_transparent": False,
            "use_compositing": False,
            "use_sequencer": False,
            "use_stamp": False,
            "filepath": str(output / "view.jpg"),
        }.items():
            set_value(scene.render, key, value)
        for key, value in {"file_format": "JPEG", "color_mode": "RGB", "quality": 90}.items():
            setattr(image_settings, key, value)
        set_value(scene.cycles, "device", "CPU")
        set_value(scene.cycles, "samples", options["samples"])
        set_value(scene.cycles, "use_denoising", True)
        bpy.ops.render.render(write_still=True)
        image = output / "view.jpg"
        if not image.is_file() or image.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Rendered image exceeds 4 MB")
        result = {
            "render_id": ident,
            "source_revision": revision,
            "source": "blender-render",
            "style": options["style"],
            "size": options["size"],
            "objects": [o.name for o in objects],
            "camera_position": list(camera.location),
            "coordinates": "Blender Z-up; metres",
            "captured_at": time.time(),
            "bytes": image.stat().st_size,
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        }
        (output / "render.json").write_text(json.dumps(result), encoding="utf-8")
        return result
    finally:
        # Changing the format can coerce color mode/depth. Restore format first.
        for key, value in previous_image_settings.items():
            setattr(image_settings, key, value)
        for owner, key, value in reversed(restore):
            setattr(owner, key, value)
        scene.camera = previous_camera
        scene.world = previous_world
        bpy.context.view_layer.material_override = previous_override
        for obj in reversed(temporary):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection, data in reversed(datablocks):
            collection.remove(data)
