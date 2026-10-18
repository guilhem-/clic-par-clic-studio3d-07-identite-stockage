"""Module 6: the scene to deliver, prepared by a fresh Blender that never ran the model's code:
    blender -b --factory-startup --disable-autoexec raw.blend --python package.py -- <folder>

The model's code built the scene in another Blender (render.py). This one opens the file without running
its scripts, cleans it (nothing must run on the user's machine), writes the marking into it
(marking.json, written by the worker), packs textures and lighting, then saves scene.blend, compressed.
Summary in packaging.json.
"""
import json
import sys
from pathlib import Path

import bpy

folder = Path(sys.argv[sys.argv.index("--") + 1])
i = json.loads((folder / "marking.json").read_text(encoding="utf-8"))
summary = {"texts": 0, "drivers": 0}

# 1. No script: no text block (a "Register" text would run on opening, if the user allows scripts),
#    no driver with a Python expression.
for t in list(bpy.data.texts):
    bpy.data.texts.remove(t)
    summary["texts"] += 1
for family in ("objects", "meshes", "materials", "worlds", "node_groups", "scenes", "lights", "cameras", "shape_keys"):
    for block in getattr(bpy.data, family):
        for holder in (block, getattr(block, "node_tree", None)):
            ad = getattr(holder, "animation_data", None)
            for driver in list(ad.drivers if ad else []):
                if driver.driver.type == "SCRIPTED":
                    ad.drivers.remove(driver)
                    summary["drivers"] += 1

# 2. Everything local: no linked library (the user's machine does not have it).
for family in ("objects", "meshes", "materials", "collections", "node_groups", "images", "worlds"):
    for block in getattr(bpy.data, family):
        if block.library:
            block.make_local()
for lib in list(bpy.data.libraries):
    bpy.data.libraries.remove(lib)

# 3. The module 4 marking, in the file itself.
bpy.context.scene["ai_studio3d"] = {
    "digitalSourceType": "trainedAlgorithmicMedia", "generator": "AI studio3D",
    "id": f"urn:uuid:{i['id']}", "date": i["date"],
    "assets": ", ".join(f"{a['name']} ({a['source']}, {a['license']})" for a in i.get("assets", [])),
}

# 4. Textures and HDRI lighting in the file: it opens anywhere, without the library.
bpy.ops.file.pack_all()
images = [im for im in bpy.data.images if im.source in ("FILE", "SEQUENCE", "TILED")]
summary["images"] = len(images)
summary["not_packed"] = [im.name for im in images if not im.packed_file]
bpy.ops.wm.save_as_mainfile(filepath=str(folder / "scene.blend"), compress=True, copy=True)
(folder / "packaging.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
