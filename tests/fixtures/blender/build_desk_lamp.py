"""Abnahme E — eine stilisierte Tischlampe mit prüfbaren Einzelteilen.

`blender --background --python build_desk_lamp.py -- <outdir>`

Erzeugt eine Szene mit **benannten Einzelteilen**, damit die Abnahme nicht nur sagen kann
"es gibt ein Objekt", sondern prüfen kann, welche Teile vorhanden sind:

    lamp.base      Fuß (Zylinder)
    lamp.stem      Stiel (schlanker Zylinder)
    lamp.shade     Schirm (Kegelstumpf, geöffnet)
    lamp.bulb      Leuchtmittel (Kugel, leuchtend)
    lamp.light     die tatsächliche Lichtquelle
    lamp.camera    die Kamera, die auf die Lampe zeigt
    lamp.ground    die Fläche, auf der sie steht

Materialien: Metall für Fuß/Stiel, Glasfaser-Optik für den Schirm, Emission für die
Birne. Gespeichert wird die .blend, gerendert wird ein PNG — beides wird von der Abnahme
verlangt ("gespeicherter Szene und Render").

Das Skript ist bewusst ohne Add-ons und ohne Interaktion: es läuft im --background-Modus
und ist damit automatisierbar, statt auf Mausklicks zu hoffen.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


def _clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _material(name: str, *, metallic: float, roughness: float,
              base: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = base
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def _emit_material(name: str, *, strength: float) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = strength
    emission.inputs["Color"].default_value = (1.0, 0.93, 0.8, 1.0)
    out = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return mat


def build(outdir: Path) -> dict:
    _clear()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 48
    scene.render.resolution_x = 640
    scene.render.resolution_y = 480

    metal = _material("lamp.metal", metallic=1.0, roughness=0.28,
                      base=(0.62, 0.60, 0.58, 1.0))
    shade_mat = _material("lamp.shade", metallic=0.0, roughness=0.55,
                          base=(0.86, 0.36, 0.16, 1.0))
    ground_mat = _material("lamp.ground", metallic=0.0, roughness=0.9,
                           base=(0.16, 0.16, 0.19, 1.0))
    bulb_mat = _emit_material("lamp.bulb", strength=18.0)

    # -- lamp.base ---------------------------------------------------------
    bpy.ops.mesh.primitive_cylinder_add(radius=0.45, depth=0.12, location=(0, 0, 0.06))
    base = bpy.context.active_object
    base.name = "lamp.base"
    base.data.materials.append(metal)

    # -- lamp.stem ---------------------------------------------------------
    bpy.ops.mesh.primitive_cylinder_add(radius=0.07, depth=1.25, location=(0, 0, 0.74))
    stem = bpy.context.active_object
    stem.name = "lamp.stem"
    stem.data.materials.append(metal)

    # -- lamp.shade -------------------------------------------------------
    bpy.ops.mesh.primitive_cone_add(
        radius1=0.52, radius2=0.20, depth=0.52,
        location=(0, 0, 1.55), vertices=48,
    )
    shade = bpy.context.active_object
    shade.name = "lamp.shade"
    shade.data.materials.append(shade_mat)

    # -- lamp.bulb --------------------------------------------------------
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.16, location=(0, 0, 1.42), segments=24)
    bulb = bpy.context.active_object
    bulb.name = "lamp.bulb"
    bulb.data.materials.append(bulb_mat)

    # -- lamp.light -------------------------------------------------------
    light_data = bpy.data.lights.new("lamp.light", type="POINT")
    light_data.energy = 260.0
    light_data.color = (1.0, 0.93, 0.8)
    light_obj = bpy.data.objects.new("lamp.light", light_data)
    light_obj.location = (0.0, -0.25, 1.42)
    scene.collection.objects.link(light_obj)

    # -- lamp.ground ------------------------------------------------------
    bpy.ops.mesh.primitive_plane_add(size=12.0, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "lamp.ground"
    ground.data.materials.append(ground_mat)

    # -- lamp.camera ------------------------------------------------------
    cam_data = bpy.data.cameras.new("lamp.camera")
    cam = bpy.data.objects.new("lamp.camera", cam_data)
    cam.location = (3.4, -3.4, 2.3)
    cam.rotation_euler = (math.radians(68), 0.0, math.radians(45))
    scene.collection.objects.link(cam)
    scene.camera = cam

    # key light so the render is not only the bulb
    key_data = bpy.data.lights.new("lamp.key", type="AREA")
    key_data.energy = 220.0
    key_data.size = 3.0
    key = bpy.data.objects.new("lamp.key", key_data)
    key.location = (2.6, -2.2, 3.4)
    scene.collection.objects.link(key)

    outdir.mkdir(parents=True, exist_ok=True)
    blend = outdir / "desk_lamp.blend"
    render = outdir / "desk_lamp.png"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    scene.render.filepath = str(render)
    bpy.ops.render.render(write_still=True)

    parts = sorted(obj.name for obj in scene.objects)
    return {"blend": str(blend), "render": str(render), "parts": parts}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    outdir = Path(argv[0]) if argv else Path("out")
    result = build(outdir)
    # printed for the acceptance to read; Blender's own output is noisy
    print("SKALDIR_E_RESULT", result)
    missing = [p for p in ("lamp.base", "lamp.stem", "lamp.shade", "lamp.bulb",
                           "lamp.light", "lamp.camera", "lamp.ground") if p not in result["parts"]]
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
