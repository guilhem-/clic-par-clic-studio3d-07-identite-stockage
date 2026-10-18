"""Run by headless Blender: blender -b --factory-startup --python render.py -- <folder>.

Empties the scene, runs scene.py (written by the model), adds a camera and a light if needed, renders
with Cycles on CPU (1280 x 720, denoising) and exports the 3D model (GLB) without the decor. Writes
result.json ({"error": …} on failure).

Module 5: the model's code has two functions from the asset library (ASSETS folder, mounted read-only):
load_asset("name") and environment("name"). Without a library, the render behaves as before.
Module 6: if SCENE_BLEND=1, the whole scene is also saved as is (raw.blend); the worker has it cleaned,
marked and packed by a fresh Blender (package.py).
Module 7: this script runs in the worker's "blender" container, which holds no secret.
"""
import json
import os
import sys
import textwrap
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

folder = Path(sys.argv[sys.argv.index("--") + 1])
result = folder / "result.json"


def write(**info):
    result.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def frame(cam, objects):
    """Places the camera to see all the objects (three-quarter view, slightly from above)."""
    points = [o.matrix_world @ v.co for o in objects for v in o.data.vertices]
    if not points:
        return
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (low + high) / 2
    direction = Vector((1.0, -1.3, 0.8)).normalized()
    rotation = (-direction).to_track_quat("-Z", "Y")
    cam.rotation_euler = rotation.to_euler()
    # Just enough distance for every vertex to fit in the frame (16:9), with a margin.
    tx = 18 / cam.data.lens                      # half sensor width (36 mm) / focal length
    ty = tx * 720 / 1280
    inverse = rotation.inverted()
    local = [inverse @ (p - center) for p in points]  # camera space: it looks towards -z
    sx = sy = 0.0                                # camera shift in its plane, to center
    for _ in range(4):
        back = max(max(z + abs(x - sx) / tx, z + abs(y - sy) / ty) for x, y, z in local) * 1.1
        us = [(x - sx) / (back - z) for x, y, z in local]
        vs = [(y - sy) / (back - z) for x, y, z in local]
        sx += (max(us) + min(us)) / 2 * back
        sy += (max(vs) + min(vs)) / 2 * back
    cam.location = center + rotation @ Vector((sx, sy, back))


# ---------------------------------------------------------------- Asset library (module 5)

ASSETS = Path(os.environ.get("ASSETS", "/assets"))
_sheet = ASSETS / "catalogue.json"  # built by the module 5 repository (original keys kept)
CATALOG = json.loads(_sheet.read_text(encoding="utf-8")) if _sheet.exists() else {"modeles": {}, "environnements": {}}
ASSETS_USED: list[str] = []  # the trace: stored with the request, then in the marking
_loaded_models: dict = {}
_environment = False


def load_asset(name: str, position=(0, 0, 0), rotation_z: float = 0.0, scale: float = 1.0, decor: bool = False):
    """Adds to the scene a copy of a library object, placed on the floor; returns its root object."""
    sheet = CATALOG["modeles"].get(name)
    if sheet is None:  # the message goes back to the model, which fixes and resubmits
        raise ValueError(f"unknown asset: {name!r}. Valid names: {', '.join(sorted(CATALOG['modeles'])) or 'none'}")
    if name not in _loaded_models:  # the file is read once, even for ten chairs
        path = ASSETS / sheet["fichier"]  # built by the studio, never by the model
        # Appended (link=False), not linked: a copy the model may move and recolor.
        with bpy.data.libraries.load(str(path), link=False, assets_only=True) as (source, target):
            target.collections = [sheet["collection"]]
        _loaded_models[name] = target.collections[0]
    model = _loaded_models[name]
    first = name not in ASSETS_USED  # first time: the appended objects themselves
    copies = {o: o if first else o.copy() for o in model.all_objects}  # then: copies, shared meshes
    for original, copy in copies.items():
        if not first:  # a child's copy follows its parent's copy
            copy.parent = copies.get(original.parent)
        if decor:  # rendered, but not exported in the 3D model
            copy.name = f"Decor_{original.name}"
        bpy.context.scene.collection.objects.link(copy)
    root = next(cp for o, cp in copies.items() if o.parent is None)
    root.location = position
    root.rotation_euler.z = rotation_z
    root.scale = (scale,) * 3
    root["studio_asset"] = name  # marker for the export and the trace
    ASSETS_USED.append(name)
    return root


def environment(name: str, strength: float = 1.0, rotation_z: float = 0.0, background_visible: bool = True):
    """Lights the scene with an HDRI image from the library: a panoramic photo of a real place."""
    global _environment
    sheet = CATALOG["environnements"].get(name)
    if sheet is None:
        raise ValueError(f"unknown environment: {name!r}. Valid names: {', '.join(sorted(CATALOG['environnements'])) or 'none'}")
    world = bpy.data.worlds.new(f"World {name}")
    bpy.context.scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    coord, rotation = tree.nodes.new("ShaderNodeTexCoord"), tree.nodes.new("ShaderNodeMapping")
    texture, background = tree.nodes.new("ShaderNodeTexEnvironment"), tree.nodes.new("ShaderNodeBackground")
    output = tree.nodes.new("ShaderNodeOutputWorld")
    texture.image = bpy.data.images.load(str(ASSETS / sheet["fichier"]), check_existing=True)
    rotation.inputs["Rotation"].default_value[2] = rotation_z
    background.inputs["Strength"].default_value = strength
    tree.links.new(coord.outputs["Generated"], rotation.inputs["Vector"])
    tree.links.new(rotation.outputs["Vector"], texture.inputs["Vector"])
    tree.links.new(texture.outputs["Color"], background.inputs["Color"])
    if background_visible:
        tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    else:  # the camera sees a neutral grey; the scene stays lit by the HDRI
        grey, mix = tree.nodes.new("ShaderNodeBackground"), tree.nodes.new("ShaderNodeMixShader")
        light_path = tree.nodes.new("ShaderNodeLightPath")
        grey.inputs["Color"].default_value = (0.8, 0.82, 0.85, 1)
        tree.links.new(light_path.outputs["Is Camera Ray"], mix.inputs["Fac"])
        tree.links.new(background.outputs["Background"], mix.inputs[1])
        tree.links.new(grey.outputs["Background"], mix.inputs[2])
        tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    _environment = True
    ASSETS_USED.append(name)


# ---------------------------------------------------------------- Render

try:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    # Tolerates a BOM and a common indentation (code copied from a block indented by the model).
    code = textwrap.dedent((folder / "scene.py").read_text(encoding="utf-8-sig"))
    # The two library functions are simply placed in the namespace of the model's code.
    # A model sometimes writes "from studio import load_asset": so this module exists too.
    import types
    sys.modules["studio"] = types.ModuleType("studio")
    sys.modules["studio"].load_asset, sys.modules["studio"].environment = load_asset, environment
    exec(compile(code, "scene.py", "exec"), {"bpy": bpy, "__name__": "__scene__",
                                            "load_asset": load_asset, "environment": environment})

    scene = bpy.context.scene
    bpy.context.view_layer.update()  # up-to-date positions (objects moved by their root) before framing
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("the scene contains no object")
    if scene.camera is None:
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        scene.collection.objects.link(cam)
        scene.camera = cam
        frame(cam, [o for o in meshes if not o.name.startswith("Decor")] or meshes)
    if not _environment and not any(o.type == "LIGHT" for o in scene.objects):
        sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
        sun.data.energy = 3
        sun.rotation_euler = (0.8, 0.2, 0.6)
        scene.collection.objects.link(sun)

    # Basic ambient light (light grey background): a scene poorly lit by the model stays readable.
    # With a library HDRI, it does the lighting: left untouched.
    if not _environment:
        if scene.world is None:
            scene.world = bpy.data.worlds.new("World")
        scene.world.use_nodes = True
        background = next((n for n in scene.world.node_tree.nodes if n.type == "BACKGROUND"), None)
        if background is not None:
            background.inputs[0].default_value = (0.8, 0.82, 0.85, 1)
            background.inputs[1].default_value = max(background.inputs[1].default_value, 0.7)

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 720, 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(folder / "render.png")
    bpy.ops.render.render(write_still=True)

    # Export of the subject: non-decor meshes, and asset roots (to keep their hierarchy).
    bpy.ops.object.select_all(action="DESELECT")
    for o in scene.objects:
        o.select_set((o.type == "MESH" and not o.name.startswith("Decor"))
                     or (o.type == "EMPTY" and "studio_asset" in o and not any(e.name.startswith("Decor") for e in o.children)))
    bpy.ops.export_scene.gltf(filepath=str(folder / "model.glb"), export_format="GLB", use_selection=True,
                              export_image_format="WEBP")  # asset textures: a lighter GLB
    if os.environ.get("SCENE_BLEND") == "1":  # module 6: the whole scene, decor included, still raw
        bpy.ops.wm.save_as_mainfile(filepath=str(folder / "raw.blend"), copy=True)
    write(ok=True, objects=len(meshes), assets=sorted(set(ASSETS_USED)), library=CATALOG.get("version"))
except Exception:
    write(error=traceback.format_exc()[-1500:])
