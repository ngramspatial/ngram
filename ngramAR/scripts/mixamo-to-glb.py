"""
Mixamo FBX → GLB Converter (Blender Script)

Usage:
  blender --background --python scripts/mixamo-to-glb.py -- <input_dir> <output.glb>

Example:
  blender --background --python scripts/mixamo-to-glb.py -- ./downloads ./shells/my-entity/models/character.glb

Input directory should contain:
  - One FBX with the character mesh (e.g., "Y Bot.fbx" from Mixamo with skin)
  - Additional FBX files for each animation (e.g., "Idle.fbx", "Talking.fbx", etc.)
    downloaded from Mixamo with "Without Skin" option

The script imports the character mesh from the first FBX, then imports each
animation clip from the remaining FBX files, naming them by filename.
Finally, it exports everything as a single GLB.
"""

import bpy
import sys
import os
from pathlib import Path

def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    for action in bpy.data.actions:
        bpy.data.actions.remove(action)

def import_fbx(filepath):
    bpy.ops.import_scene.fbx(filepath=filepath)

def get_armature():
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            return obj
    return None

def main():
    argv = sys.argv
    separator = argv.index("--") if "--" in argv else -1
    if separator == -1 or len(argv) < separator + 3:
        print("Usage: blender --background --python mixamo-to-glb.py -- <input_dir> <output.glb>")
        sys.exit(1)

    input_dir = Path(argv[separator + 1])
    output_path = Path(argv[separator + 2])

    if not input_dir.is_dir():
        print(f"Error: {input_dir} is not a directory")
        sys.exit(1)

    fbx_files = sorted(input_dir.glob("*.fbx"))
    if not fbx_files:
        print(f"Error: No FBX files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(fbx_files)} FBX files:")
    for f in fbx_files:
        print(f"  {f.name}")

    clear_scene()

    # Import the first FBX (should be the one with the character mesh)
    mesh_fbx = fbx_files[0]
    print(f"\nImporting character mesh from: {mesh_fbx.name}")
    import_fbx(str(mesh_fbx))

    armature = get_armature()
    if not armature:
        print("Error: No armature found after importing character")
        sys.exit(1)

    # Rename the first action to the filename (minus extension)
    if bpy.data.actions:
        first_action = bpy.data.actions[0]
        clip_name = mesh_fbx.stem.replace(" ", "_")
        first_action.name = clip_name
        print(f"  Renamed action to: {clip_name}")

    # Import additional animation FBX files
    for fbx_file in fbx_files[1:]:
        clip_name = fbx_file.stem.replace(" ", "_")
        print(f"\nImporting animation: {fbx_file.name} → clip '{clip_name}'")

        existing_actions = set(bpy.data.actions.keys())
        import_fbx(str(fbx_file))

        # Find the newly created action
        new_actions = set(bpy.data.actions.keys()) - existing_actions
        if new_actions:
            new_action_name = new_actions.pop()
            action = bpy.data.actions[new_action_name]
            action.name = clip_name
            print(f"  Renamed action '{new_action_name}' → '{clip_name}'")

        # Remove duplicate armatures/meshes from the animation import
        for obj in bpy.context.selected_objects:
            if obj != armature and obj.type in ('ARMATURE', 'MESH'):
                # Keep meshes that are part of the original character
                if obj.type == 'ARMATURE':
                    bpy.data.objects.remove(obj, do_unlink=True)

    # Make sure all actions are associated with the armature
    print(f"\nFinal actions ({len(bpy.data.actions)}):")
    for action in bpy.data.actions:
        print(f"  {action.name} ({action.frame_range[0]:.0f}-{action.frame_range[1]:.0f})")
        action.use_fake_user = True

    # Ensure NLA tracks exist for all actions so they export
    if armature.animation_data is None:
        armature.animation_data_create()

    for action in bpy.data.actions:
        track = armature.animation_data.nla_tracks.new()
        track.name = action.name
        track.strips.new(action.name, int(action.frame_range[0]), action)

    # Export as GLB
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nExporting GLB to: {output_path}")

    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format='GLB',
        export_animations=True,
        export_nla_strips=True,
        export_skins=True,
        export_morph=True,
    )

    print(f"\nDone! GLB saved to: {output_path}")
    print(f"File size: {output_path.stat().st_size / 1024:.1f} KB")

if __name__ == "__main__":
    main()
