# Copyright (c) 2026 Gabrial McCauley (GDME LLC)
# SPDX-License-Identifier: GPL-3.0-or-later (for Blender bpy compatibility)
"""Dual-mode Blender TCP socket server bootstrap (GUI timer + headless daemon + cad helper)."""
import base64
import io
import json
import math
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
import types
import urllib.request

PORT = int(os.environ.get("BLENDER_SERVER_PORT", "9999"))
HOST = os.environ.get("BLENDER_SERVER_HOST", "0.0.0.0")
STUDIO_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(STUDIO_DIR, "uploads")
LAST_EXEC_LOG = os.path.join(STUDIO_DIR, "last_exec.log")
SNAPSHOT_BLEND = os.path.join(STUDIO_DIR, "_undo_snapshot.blend")

task_queue = queue.Queue()
result_queue = queue.Queue()
server_running = True
server_thread = None
timer_registered = False
_shims_installed = False

try:
    import bpy
except ImportError:
    bpy = None
try:
    import bmesh
except ImportError:
    bmesh = None
try:
    import mathutils
except ImportError:
    mathutils = None

_PRINTABLE_TYPES = ("MESH", "CURVE", "FONT", "SURFACE", "META")
_MOCKUP_PREFIXES = ("M_", "Mockup_", "Vis_", "Plug_", "Ground_Shadow", "Drawing_Paper", "Floor")


class _OpsSubProxy:
    """Transparent proxy overriding selected Blender/bmesh operators with safe wrappers."""

    def __init__(self, target, overrides):
        self._target = target
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._target, name)


def _ensure_collection(name, parent=None):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(col)
    elif col.name not in (parent or bpy.context.scene.collection).children:
        try:
            (parent or bpy.context.scene.collection).children.link(col)
        except Exception:
            pass
    return col


def _link_only(obj, collection):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)


def _resolve_obj(obj):
    if isinstance(obj, str) and bpy is not None:
        return bpy.data.objects.get(obj)
    return obj


def _cad_color(obj, rgba=(0.8, 0.8, 0.8, 1.0), name=None, roughness=0.35, metallic=0.0):
    obj = _resolve_obj(obj)
    if obj is None or not hasattr(obj, "data") or obj.data is None:
        return None
    rgba_tuple = tuple(float(v) for v in rgba)
    if len(rgba_tuple) == 3:
        rgba_tuple = rgba_tuple + (1.0,)
    mat_name = name or f"Mat_{obj.name}"
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    if getattr(mat, "node_tree", None) is None and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        if "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = rgba_tuple
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = float(roughness)
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = float(metallic)
    if hasattr(obj.data, "materials"):
        obj.data.materials.clear()
        obj.data.materials.append(mat)
    return mat


def _cad_clear():
    """Reset workspace to 1 BU = 1 mm with collections and aimed 3-point lights."""
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    for o in list(bpy.data.objects):
        if o.type in _PRINTABLE_TYPES or o.name.startswith("_"):
            bpy.data.objects.remove(o, do_unlink=True)
    try:
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    except Exception:
        pass
    _ensure_collection("CAD_Printable_Parts")
    _ensure_collection("Hardware_Mockups")
    for lname, energy, loc in [
        ("KeyLight", 4.5, (110.0, -120.0, 140.0)),
        ("FillLight", 1.8, (-120.0, -70.0, 75.0)),
        ("RimLight", 2.5, (0.0, 120.0, 100.0)),
    ]:
        lobj = bpy.data.objects.get(lname)
        if lobj is None or lobj.type != "LIGHT":
            ldata = bpy.data.lights.new(lname, type="SUN")
            lobj = bpy.data.objects.new(lname, ldata)
            scene.collection.objects.link(lobj)
        lobj.data.energy = energy
        lobj.location = loc
        lobj.rotation_euler = (mathutils.Vector((0, 0, 0)) - lobj.location).to_track_quat("-Z", "Y").to_euler()
    return scene


def _cad_box(
    name="Box",
    width=20.0,
    depth=20.0,
    height=10.0,
    location=(0.0, 0.0, 0.0),
    corner_radius=0.0,
    bevel=0.0,
    centered_z=False,
    color=None,
    mockup=False,
):
    """Create a watertight millimeter box resting on Z=location[2] (or centered if centered_z)."""
    w, d, h = max(0.05, float(width)), max(0.05, float(depth)), max(0.05, float(height))
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= w
        v.co.y *= d
        v.co.z = v.co.z * h if centered_z else (v.co.z + 0.5) * h
    bm.edges.ensure_lookup_table()
    if corner_radius and float(corner_radius) > 0.01:
        rad = min(float(corner_radius), min(w, d) * 0.48)
        vedges = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > h * 0.5]
        if vedges:
            bmesh.ops.bevel(bm, geom=vedges, offset=rad, segments=6, profile=0.5, affect="EDGES", clamp_overlap=True)
    if bevel and float(bevel) > 0.01:
        bmesh.ops.bevel(
            bm, geom=list(bm.edges), offset=min(float(bevel), min(w, d, h) * 0.25),
            segments=2, profile=0.5, affect="EDGES", clamp_overlap=True,
        )
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = tuple(float(c) for c in location)
    col = _ensure_collection("Hardware_Mockups" if mockup else "CAD_Printable_Parts")
    _link_only(obj, col)
    if color is not None:
        _cad_color(obj, color)
    return obj


def _cad_bevel(target, offset=1.0, segments=4, profile=0.5):
    """Apply a convex bmesh bevel (profile=0.5) to all edges of target."""
    target = _resolve_obj(target)
    if target is None or target.type != "MESH":
        return target
    bm = bmesh.new()
    bm.from_mesh(target.data)
    bmesh.ops.bevel(
        bm,
        geom=list(bm.edges),
        offset=float(offset),
        segments=max(1, int(segments)),
        profile=float(profile),
        affect="EDGES",
        clamp_overlap=True,
    )
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(target.data)
    bm.free()
    return target


def _cad_cut(target, cutter, delete_cutter=True):
    return _cad_boolean(target, cutter, "DIFFERENCE", delete_tool=delete_cutter)


def _cad_union(target, tool, delete_tool=True):
    return _cad_boolean(target, tool, "UNION", delete_tool=delete_tool)


def _cad_intersect(target, tool, delete_tool=True):
    return _cad_boolean(target, tool, "INTERSECT", delete_tool=delete_tool)


def _cad_boolean(target, tool, operation="DIFFERENCE", delete_tool=True):
    target = _resolve_obj(target)
    tool = _resolve_obj(tool)
    if target is None or tool is None:
        return target
    mod = target.modifiers.new(name=f"Bool_{operation}", type="BOOLEAN")
    mod.operation = operation
    if hasattr(mod, "solver"):
        mod.solver = "EXACT"
    mod.object = tool
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception:
        if hasattr(mod, "solver"):
            mod.solver = "FLOAT"
            bpy.ops.object.modifier_apply(modifier=mod.name)
    if delete_tool and tool.name in bpy.data.objects:
        td = tool.data
        bpy.data.objects.remove(tool, do_unlink=True)
        if td and td.users == 0 and isinstance(td, bpy.types.Mesh):
            bpy.data.meshes.remove(td)
    return target


def _cad_rounded_plate(
    name="Card_Base",
    width=85.6,
    depth=54.0,
    thickness=2.0,
    corner_radius=4.0,
    rim_width=0.0,
    rim_height=0.0,
    location=(0.0, 0.0, 0.0),
    color=(0.10, 0.12, 0.16, 1.0),
    rim_color=(0.91, 0.49, 0.05, 1.0),
):
    """Create a rounded rectangular plate and optional watertight raised border frame."""
    base = _cad_box(
        name=name, width=width, depth=depth, height=thickness,
        location=location, corner_radius=corner_radius, color=color,
    )
    if rim_width and float(rim_width) > 0.05 and rim_height and float(rim_height) > 0.05:
        rw = float(rim_width)
        rh = float(rim_height)
        lx, ly, lz = (float(c) for c in location)
        rim = _cad_box(
            name=f"{name}_Frame",
            width=max(2.0, float(width) - 1.0),
            depth=max(2.0, float(depth) - 1.0),
            height=rh,
            location=(lx, ly, lz + float(thickness)),
            corner_radius=max(0.0, float(corner_radius) - 0.5),
            color=rim_color,
        )
        cutter = _cad_box(
            name="_rim_cut",
            width=max(1.0, float(width) - 1.0 - 2.0 * rw),
            depth=max(1.0, float(depth) - 1.0 - 2.0 * rw),
            height=rh + 2.0,
            location=(lx, ly, lz + float(thickness) - 1.0),
            corner_radius=max(0.0, float(corner_radius) - 0.5 - rw * 0.5),
        )
        _cad_cut(rim, cutter, delete_cutter=True)
        return base, rim
    return base


def _cad_cylinder(
    name="Cylinder",
    radius=None,
    diameter=None,
    height=10.0,
    location=(0.0, 0.0, 0.0),
    segments=64,
    centered_z=False,
    color=None,
    mockup=False,
):
    rad = float(radius) if radius is not None else (float(diameter) * 0.5 if diameter is not None else 5.0)
    h = max(0.05, float(height))
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=max(8, int(segments)),
        radius1=max(0.05, rad), radius2=max(0.05, rad), depth=h,
    )
    if not centered_z:
        for v in bm.verts:
            v.co.z += h * 0.5
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = tuple(float(c) for c in location)
    col = _ensure_collection("Hardware_Mockups" if mockup else "CAD_Printable_Parts")
    _link_only(obj, col)
    if color is not None:
        _cad_color(obj, color)
    return obj


def _cad_tube(
    name="Tube",
    outer_diameter=12.0,
    inner_diameter=8.0,
    height=10.0,
    location=(0.0, 0.0, 0.0),
    segments=64,
    centered_z=False,
    color=None,
):
    outer = _cad_cylinder(
        name=name, diameter=outer_diameter, height=height,
        location=location, segments=segments, centered_z=centered_z, color=color,
    )
    lx, ly, lz = (float(c) for c in location)
    dz = 0.0 if centered_z else -1.0
    cutter = _cad_cylinder(
        name="_tube_bore", diameter=inner_diameter, height=float(height) + 4.0,
        location=(lx, ly, lz + dz), segments=segments, centered_z=centered_z,
    )
    return _cad_cut(outer, cutter, delete_cutter=True)


def _cad_text(
    name="Embossed_Text",
    text="STUDIO",
    size=10.0,
    height=1.2,
    location=(0.0, 0.0, 2.0),
    align_x="CENTER",
    align_y="CENTER",
    bevel=0.05,
    color=(0.91, 0.49, 0.05, 1.0),
    font_path=None,
):
    bpy.ops.object.text_add(location=tuple(float(c) for c in location))
    obj = bpy.context.active_object
    obj.name = name
    obj.data.body = str(text)
    obj.data.align_x = align_x
    obj.data.align_y = align_y if align_y in ("TOP_BASELINE", "TOP", "CENTER", "BOTTOM", "BOTTOM_BASELINE") else "CENTER"
    obj.data.size = float(size)
    obj.data.extrude = max(0.05, float(height) * 0.5)
    obj.data.bevel_depth = max(0.0, float(bevel))
    if font_path and os.path.isfile(font_path):
        try:
            obj.data.font = bpy.data.fonts.load(font_path, check_existing=True)
        except Exception:
            pass
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target="MESH", keep_original=False)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01)
    min_z = min((v.co.z for v in bm.verts), default=0.0)
    for v in bm.verts:
        v.co.z -= min_z
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    _link_only(obj, _ensure_collection("CAD_Printable_Parts"))
    if color is not None:
        _cad_color(obj, color)
    return obj


def _resolve_upload_image(image_path):
    if image_path and os.path.isfile(image_path):
        return image_path
    if os.path.isdir(UPLOADS_DIR):
        exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        cands = [
            os.path.join(UPLOADS_DIR, fn)
            for fn in os.listdir(UPLOADS_DIR)
            if fn.lower().endswith(exts) and os.path.isfile(os.path.join(UPLOADS_DIR, fn))
        ]
        if cands:
            cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return cands[0]
    return ""


def _build_grid_solid_bmesh(mask, width, depth, height):
    """Build a manifold watertight extruded voxel mesh from a 2D boolean grid."""
    gy = len(mask)
    gx = len(mask[0]) if gy else 0
    # Eliminate diagonal 2x2 pinches so every corner vertex has strictly manifold degree-2 edges.
    for _ in range(4):
        changed = False
        for y in range(gy - 1):
            for x in range(gx - 1):
                a, b, c, d = mask[y][x], mask[y][x + 1], mask[y + 1][x], mask[y + 1][x + 1]
                if a and d and not b and not c:
                    mask[y + 1][x] = True
                    changed = True
                elif b and c and not a and not d:
                    mask[y][x] = True
                    changed = True
        if not changed:
            break

    bm = bmesh.new()
    cw, cd = width / max(1, gx), depth / max(1, gy)
    ox, oy = -width * 0.5, -depth * 0.5
    verts = {}

    def v(ix, iy, iz):
        key = (ix, iy, iz)
        vv = verts.get(key)
        if vv is None:
            vv = bm.verts.new((ox + ix * cw, oy + iy * cd, 0.0 if iz == 0 else height))
            verts[key] = vv
        return vv

    for y in range(gy):
        for x in range(gx):
            if not mask[y][x]:
                continue
            v00b, v10b, v11b, v01b = v(x, y, 0), v(x + 1, y, 0), v(x + 1, y + 1, 0), v(x, y + 1, 0)
            v00t, v10t, v11t, v01t = v(x, y, 1), v(x + 1, y, 1), v(x + 1, y + 1, 1), v(x, y + 1, 1)
            bm.faces.new((v00t, v10t, v11t, v01t))
            bm.faces.new((v01b, v11b, v10b, v00b))
            if y == 0 or not mask[y - 1][x]:
                bm.faces.new((v00b, v10b, v10t, v00t))
            if y == gy - 1 or not mask[y + 1][x]:
                bm.faces.new((v11b, v01b, v01t, v11t))
            if x == 0 or not mask[y][x - 1]:
                bm.faces.new((v01b, v00b, v00t, v01t))
            if x == gx - 1 or not mask[y][x + 1]:
                bm.faces.new((v10b, v11b, v11t, v10t))

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def _cad_image_relief(
    name="Logo_Relief",
    image_path=None,
    width=42.0,
    depth=28.0,
    height=1.2,
    location=(0.0, 0.0, 2.0),
    threshold=0.5,
    invert=False,
    grid=80,
    color=(0.91, 0.49, 0.05, 1.0),
):
    """Extrude foreground shapes from an uploaded PNG/JPG into a manifold 3D relief mesh."""
    resolved = _resolve_upload_image(image_path)
    if not resolved:
        raise FileNotFoundError("No uploaded image found for cad.image_relief().")
    img = bpy.data.images.load(resolved, check_existing=True)
    w, h = img.size[0], img.size[1]
    if w <= 0 or h <= 0:
        raise RuntimeError(f"Could not decode image dimensions: {resolved}")
    px = list(img.pixels[:])
    gx = max(8, min(140, int(grid)))
    gy = max(8, min(140, int(round(gx * (h / max(1, w))))))
    if depth is None:
        depth = float(width) * (h / max(1, w))

    samples = [[(0.0, 1.0) for _ in range(gx)] for _ in range(gy)]
    border_lums = []
    min_a, max_a = 1.0, 0.0
    for iy in range(gy):
        sy = min(h - 1, int((iy + 0.5) * h / gy))
        row_off = sy * w * 4
        for ix in range(gx):
            sx = min(w - 1, int((ix + 0.5) * w / gx))
            idx = row_off + sx * 4
            lum = 0.299 * px[idx] + 0.587 * px[idx + 1] + 0.114 * px[idx + 2]
            alpha = px[idx + 3]
            samples[iy][ix] = (lum, alpha)
            min_a = min(min_a, alpha)
            max_a = max(max_a, alpha)
            if iy in (0, gy - 1) or ix in (0, gx - 1):
                border_lums.append(lum)

    use_alpha = (min_a < 0.8 and max_a > 0.25)
    bg_lum = sum(border_lums) / max(1, len(border_lums))
    lum_diff = max(0.10, 0.32 * (1.0 - float(threshold)) + 0.12)
    mask = []
    any_on = False
    for iy in range(gy):
        row = []
        for ix in range(gx):
            lum, alpha = samples[iy][ix]
            on = (alpha >= float(threshold)) if use_alpha else (abs(lum - bg_lum) > lum_diff)
            if invert:
                on = not on
            if on:
                any_on = True
            row.append(on)
        mask.append(row)

    if not any_on:
        return _cad_box(
            name=name, width=float(width) * 0.6, depth=float(depth) * 0.6,
            height=float(height), location=location, corner_radius=2.0, color=color,
        )

    bm = _build_grid_solid_bmesh(mask, float(width), float(depth), max(0.1, float(height)))
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = tuple(float(c) for c in location)
    _link_only(obj, _ensure_collection("CAD_Printable_Parts"))
    if color is not None:
        _cad_color(obj, color)
    return obj


def _cad_hole(target, diameter=3.4, depth=100.0, location=(0.0, 0.0, 0.0)):
    lx, ly, lz = (float(c) for c in location)
    cutter = _cad_cylinder(
        name="_hole_cut", diameter=diameter, height=depth,
        location=(lx, ly, lz), centered_z=True,
    )
    return _cad_cut(target, cutter, delete_cutter=True)


def _cad_magnet_pocket(target, diameter=6.25, depth=2.20, location=(0.0, 0.0, 0.0), show_mockup=True):
    lx, ly, lz = (float(c) for c in location)
    cutter = _cad_cylinder(
        name="_mag_cut", diameter=diameter, height=depth,
        location=(lx, ly, lz - 0.05),
    )
    _cad_cut(target, cutter, delete_cutter=True)
    if show_mockup:
        _cad_cylinder(
            name="Mockup_Mag", diameter=max(1.0, float(diameter) - 0.25), height=max(0.5, float(depth) - 0.20),
            location=(lx, ly, lz), color=(0.75, 0.78, 0.85, 1.0), mockup=True,
        )
    return target


def _cad_m3_hole(target, depth=20.0, location=(0.0, 0.0, 0.0), nut_trap=True, show_mockup=True):
    lx, ly, lz = (float(c) for c in location)
    _cad_hole(target, diameter=3.4, depth=depth, location=(lx, ly, lz + float(depth) * 0.5 - 1.0))
    if nut_trap:
        nut = _cad_cylinder(name="_nut_cut", diameter=6.5, height=3.0, location=(lx, ly, lz - 0.05), segments=6)
        _cad_cut(target, nut, delete_cutter=True)
    if show_mockup:
        _cad_cylinder(
            name="M_Screw_M3", diameter=3.0, height=min(float(depth), 12.0),
            location=(lx, ly, lz), color=(0.60, 0.63, 0.68, 1.0), mockup=True,
        )
    return target


def _build_cad_module():
    mod = types.ModuleType("cad")
    mod.clear = _cad_clear
    mod.color = _cad_color
    mod.box = _cad_box
    mod.rounded_plate = _cad_rounded_plate
    mod.cylinder = _cad_cylinder
    mod.tube = _cad_tube
    mod.text = _cad_text
    mod.image_relief = _cad_image_relief
    mod.cut = _cad_cut
    mod.union = _cad_union
    mod.intersect = _cad_intersect
    mod.bevel = _cad_bevel
    mod.hole = _cad_hole
    mod.magnet_pocket = _cad_magnet_pocket
    mod.m3_hole = _cad_m3_hole
    return mod


cad = _build_cad_module()
sys.modules["cad"] = cad


def _install_bpy_shims():
    """Install Blender 4.x/5.x compatibility shims for AI-generated bpy/bmesh code."""
    global _shims_installed
    if _shims_installed or bpy is None:
        return
    _shims_installed = True

    if not hasattr(bpy.types.Mesh, "use_auto_smooth"):
        try:
            bpy.types.Mesh.use_auto_smooth = property(lambda self: True, lambda self, v: None)
            bpy.types.Mesh.auto_smooth_angle = property(lambda self: 0.523599, lambda self, v: None)
        except Exception:
            pass
    if not hasattr(bpy.types.Scene, "matrix_world") and mathutils is not None:
        try:
            bpy.types.Scene.matrix_world = property(lambda self: mathutils.Matrix.Identity(4))
        except Exception:
            pass

    if bmesh is not None and hasattr(bmesh, "ops"):
        orig_bm_ops = bmesh.ops
        orig_bm_bevel = orig_bm_ops.bevel

        def _safe_bm_bevel(bm, **kwargs):
            kw = dict(kwargs)
            if "geom" not in kw:
                geom = []
                if "verts" in kw:
                    geom.extend(kw.pop("verts") or [])
                if "edges" in kw:
                    geom.extend(kw.pop("edges") or [])
                kw["geom"] = geom
            else:
                kw.pop("verts", None)
                kw.pop("edges", None)
            if "profile" not in kw:
                kw["profile"] = 0.5
            if "affect" not in kw:
                g = kw.get("geom") or []
                is_verts = bool(g) and isinstance(g[0], bmesh.types.BMVert)
                kw["affect"] = "VERTICES" if is_verts else "EDGES"
            allowed = {"bm", "geom", "offset", "offset_type", "segments", "profile", "affect", "clamp_overlap", "loop_slide"}
            clean = {k: v for k, v in kw.items() if k in allowed}
            return orig_bm_bevel(bm, **clean)

        def _safe_bm_extrude_region_move(bm, **kwargs):
            geom = kwargs.get("geom") or kwargs.get("E") or []
            depth_val = float(kwargs.get("depth") or kwargs.get("DEPTH") or 0.0)
            res = orig_bm_ops.extrude_face_region(bm, geom=geom)
            if depth_val:
                verts = [g for g in res.get("geom", []) if isinstance(g, bmesh.types.BMVert)]
                if verts:
                    orig_bm_ops.translate(bm, verts=verts, vec=(0.0, 0.0, depth_val))
            return res

        bmesh.ops = _OpsSubProxy(orig_bm_ops, {
            "bevel": _safe_bm_bevel,
            "extrude_region_move": _safe_bm_extrude_region_move,
        })

    orig_ops = bpy.ops
    orig_mesh = orig_ops.mesh
    orig_obj = orig_ops.object
    orig_wm = orig_ops.wm

    def _safe_cube_add(*args, **kwargs):
        kw = dict(kwargs)
        scale_vec = None
        if isinstance(kw.get("size"), (tuple, list)) and len(kw["size"]) == 3:
            scale_vec = tuple(float(v) for v in kw["size"])
            kw["size"] = 1.0
        elif any(k in kw for k in ("width", "height", "depth")):
            sx = float(kw.pop("width", kw.get("size", 2.0)))
            sy = float(kw.pop("height", kw.get("size", 2.0)))
            sz = float(kw.pop("depth", kw.get("size", 2.0)))
            scale_vec = (sx, sy, sz)
            kw["size"] = 1.0
        allowed = {"size", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        clean = {k: v for k, v in kw.items() if k in allowed}
        if scale_vec is not None and "scale" not in clean:
            clean["scale"] = scale_vec
        return orig_mesh.primitive_cube_add(*args, **clean)

    def _safe_cylinder_add(*args, **kwargs):
        kw = dict(kwargs)
        if "height" in kw and "depth" not in kw:
            kw["depth"] = kw.pop("height")
        if "segments" in kw and "vertices" not in kw:
            kw["vertices"] = int(kw.pop("segments"))
        allowed = {"vertices", "radius", "depth", "end_fill_type", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        return orig_mesh.primitive_cylinder_add(*args, **{k: v for k, v in kw.items() if k in allowed})

    def _safe_uv_sphere_add(*args, **kwargs):
        kw = dict(kwargs)
        if "subdivisions" in kw and "segments" not in kw:
            kw["segments"] = int(kw.pop("subdivisions"))
        allowed = {"segments", "ring_count", "radius", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        return orig_mesh.primitive_uv_sphere_add(*args, **{k: v for k, v in kw.items() if k in allowed})

    def _safe_ico_sphere_add(*args, **kwargs):
        allowed = {"subdivisions", "radius", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        return orig_mesh.primitive_ico_sphere_add(*args, **{k: v for k, v in kwargs.items() if k in allowed})

    def _safe_cone_add(*args, **kwargs):
        kw = dict(kwargs)
        if "height" in kw and "depth" not in kw:
            kw["depth"] = kw.pop("height")
        if "segments" in kw and "vertices" not in kw:
            kw["vertices"] = int(kw.pop("segments"))
        allowed = {"vertices", "radius1", "radius2", "depth", "end_fill_type", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        return orig_mesh.primitive_cone_add(*args, **{k: v for k, v in kw.items() if k in allowed})

    mesh_proxy = _OpsSubProxy(
        orig_mesh,
        {
            "primitive_cube_add": _safe_cube_add,
            "primitive_cube": _safe_cube_add,
            "primitive_cylinder_add": _safe_cylinder_add,
            "primitive_cylinder": _safe_cylinder_add,
            "primitive_uv_sphere_add": _safe_uv_sphere_add,
            "primitive_uv_sphere": _safe_uv_sphere_add,
            "primitive_ico_sphere_add": _safe_ico_sphere_add,
            "primitive_ico_sphere": _safe_ico_sphere_add,
            "primitive_cone_add": _safe_cone_add,
            "primitive_cone": _safe_cone_add,
            "primitive_plane": orig_mesh.primitive_plane_add,
            "primitive_torus": orig_mesh.primitive_torus_add,
        },
    )

    def _safe_convert(*args, **kwargs):
        act = bpy.context.view_layer.objects.active
        if act is not None and not act.select_get():
            act.select_set(True)
        return orig_obj.convert(*args, **kwargs)

    obj_proxy = _OpsSubProxy(
        orig_obj,
        {
            "shade_convert": lambda *a, **k: {"FINISHED"},
            "convert": _safe_convert,
        },
    )

    def _safe_stl_export(*args, **kwargs):
        kw = dict(kwargs)
        if "use_binary" in kw and "ascii_format" not in kw:
            kw["ascii_format"] = not bool(kw.pop("use_binary"))
        if "ascii" in kw and "ascii_format" not in kw:
            kw["ascii_format"] = bool(kw.pop("ascii"))
        if "apply_scale_factor" in kw and "global_scale" not in kw:
            kw["global_scale"] = float(kw.pop("apply_scale_factor"))
        if "use_selection" in kw and "export_selected_objects" not in kw:
            kw["export_selected_objects"] = bool(kw.pop("use_selection"))
        if "filepath" in kw and not os.path.isabs(str(kw["filepath"])):
            base_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else STUDIO_DIR
            kw["filepath"] = os.path.join(base_dir or STUDIO_DIR, os.path.basename(str(kw["filepath"])))
        allowed = {"filepath", "check_existing", "filter_glob", "ascii_format", "use_batch", "use_scene_unit", "global_scale", "use_mesh_modifiers", "apply_modifiers", "export_selected_objects"}
        clean = {k: v for k, v in kw.items() if k in allowed}
        return orig_wm.stl_export(*args, **clean)

    ops_overrides = {"mesh": mesh_proxy, "object": obj_proxy}
    if hasattr(orig_wm, "stl_export"):
        ops_overrides["wm"] = _OpsSubProxy(orig_wm, {"stl_export": _safe_stl_export})
        orig_export_mesh = getattr(orig_ops, "export_mesh", None)
        if orig_export_mesh is not None:
            ops_overrides["export_mesh"] = _OpsSubProxy(orig_export_mesh, {"stl": _safe_stl_export})

    bpy.ops = _OpsSubProxy(orig_ops, ops_overrides)


_BSDF_ALIASES = (
    (r"""inputs\[\s*(["'])Specular\1\s*\]""", r"""inputs[\1Specular IOR Level\1]"""),
    (r"""inputs\[\s*(["'])Transmission\1\s*\]""", r"""inputs[\1Transmission Weight\1]"""),
    (r"""inputs\[\s*(["'])Emission\1\s*\]""", r"""inputs[\1Emission Color\1]"""),
    (r"""inputs\[\s*(["'])Subsurface\1\s*\]""", r"""inputs[\1Subsurface Weight\1]"""),
    (r"""inputs\[\s*(["'])Coat\1\s*\]""", r"""inputs[\1Coat Weight\1]"""),
)


def _normalize_bpy_code(code_str):
    for pat, repl in _BSDF_ALIASES:
        code_str = re.sub(pat, repl, code_str)
    return code_str


def _log_last_exec(code_str, status, stdout_txt, stderr_txt):
    try:
        os.makedirs(STUDIO_DIR, exist_ok=True)
        with open(LAST_EXEC_LOG, "w", encoding="utf-8") as fh:
            fh.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} | status={status} ===\n")
            fh.write("--- CODE ---\n" + code_str[:4000] + "\n")
            if stdout_txt:
                fh.write("--- STDOUT ---\n" + stdout_txt[:2000] + "\n")
            if stderr_txt:
                fh.write("--- STDERR ---\n" + stderr_txt[:4000] + "\n")
    except Exception:
        pass


def _self_heal_with_ollama(broken_code, tb_text):
    """Ask local Ollama coder to rewrite a failing script using the built-in `cad` module."""
    uploads = []
    if os.path.isdir(UPLOADS_DIR):
        uploads = sorted(os.listdir(UPLOADS_DIR))
    sys_msg = (
        "You repair broken Blender Python scripts using ONLY the pre-loaded `cad` module (1 BU = 1 mm).\n"
        "Available `cad` functions (NO other cad functions exist):\n"
        "- cad.clear()\n"
        "- cad.rounded_plate(name, width, depth, thickness, corner_radius=4.0, rim_width=0.0, rim_height=0.0, location=(0,0,0), color=(0.1,0.12,0.16,1.0), rim_color=(0.91,0.49,0.05,1.0))\n"
        "- cad.box(name, width, depth, height, location=(0,0,0), corner_radius=0.0, bevel=0.0, color=(0.7,0.7,0.7,1.0))\n"
        "- cad.cylinder(name, diameter=10.0, height=5.0, location=(0,0,0), color=(0.7,0.7,0.7,1.0))\n"
        "- cad.tube(name, outer_diameter=12.0, inner_diameter=8.0, height=10.0, location=(0,0,0), color=None)\n"
        "- cad.text(name, text='HELLO', size=10.0, height=1.2, location=(0,0,2.0), color=(0.91,0.49,0.05,1.0))\n"
        "- cad.image_relief(name='Logo_Relief', image_path=None, width=42.0, depth=28.0, height=1.2, location=(0,0,2.0), color=(0.91,0.49,0.05,1.0))\n"
        "- cad.bevel(target, offset=1.0, segments=4) / cad.color(target, rgba)\n"
        "- cad.cut(target, cutter) / cad.union(target, tool) / cad.hole(target, diameter=3.4, depth=100.0, location=(0,0,0))\n"
        "- cad.magnet_pocket(target, diameter=6.25, depth=2.20, location=(0,0,0))\n"
        "- cad.m3_hole(target, depth=20.0, location=(0,0,0))\n"
        "NEVER use `bpy` or `bmesh` directly unless strictly needed; prefer `cad.clear()`, `cad.rounded_plate(...)`, `cad.box(...)`, `cad.text(...)`.\n"
        "Preserve the intended dimensions, text, borders, holes, and uploaded images from the broken script.\n"
        "Output ONLY ONE ```python``` block starting with `import cad` and `cad.clear()`."
    )
    user_msg = (
        f"Uploaded files in studio uploads dir: {uploads or 'none'}\n\n"
        f"Broken script:\n```python\n{broken_code[:5000]}\n```\n\n"
        f"Error:\n{tb_text[-1500:]}\n\n"
        "Rewrite this cleanly using the `cad` module so it runs with zero errors."
    )
    payload = json.dumps({
        "model": "qwen2.5-coder:7b",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
    }).encode("utf-8")
    for base in ("http://127.0.0.1:11435", "http://127.0.0.1:11434"):
        try:
            req = urllib.request.Request(
                base + "/api/chat", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            txt = (data.get("message") or {}).get("content", "")
            m = re.search(r"```(?:python|py)?\s*\n(.*?)(?:```|$)", txt, re.DOTALL | re.IGNORECASE)
            if m and ("cad." in m.group(1) or "bpy." in m.group(1)):
                return m.group(1).strip()
        except Exception:
            continue
    return ""


def _exec_snippet(code_str):
    out_buf, err_buf = io.StringIO(), io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf
    status, error_msg, file_data = "success", "", None
    should_stop = False
    try:
        exec_globals = {
            "bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "cad": cad,
            "math": math, "os": os, "sys": sys, "json": json,
            "base64": base64, "__builtins__": __builtins__,
        }
        exec(_normalize_bpy_code(code_str), exec_globals)
        if "__file_data__" in exec_globals:
            file_data = exec_globals["__file_data__"]
        if exec_globals.get("__stop_server__") is True:
            should_stop = True
            print("[Server] Stop command received from client.")
    except Exception:
        status = "error"
        error_msg = traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return status, out_buf.getvalue(), (err_buf.getvalue() or error_msg), file_data, should_stop


def execute_code_in_main_thread(code_str):
    global server_running
    _install_bpy_shims()
    is_internal = ("__file_data__" in code_str) or ("__stop_server__" in code_str) or ("write_still=True" in code_str)
    snap_names = []
    snap_ok = False
    if not is_internal and bpy is not None:
        snap_names = [o.name for o in bpy.data.objects if o.type in _PRINTABLE_TYPES]
        if snap_names:
            try:
                os.makedirs(STUDIO_DIR, exist_ok=True)
                bpy.ops.wm.save_as_mainfile(filepath=SNAPSHOT_BLEND, copy=True)
                snap_ok = True
            except Exception:
                snap_ok = False

    status, stdout_txt, stderr_txt, file_data, should_stop = _exec_snippet(code_str)
    if should_stop:
        server_running = False

    if not is_internal and status == "error" and bpy is not None:
        healed_code = _self_heal_with_ollama(code_str, stderr_txt)
        if healed_code:
            h_status, h_out, h_err, h_fd, _ = _exec_snippet(healed_code)
            if h_status == "success":
                status = "success"
                stdout_txt = "[Auto-repaired using built-in `cad` module]\n" + h_out
                stderr_txt = ""
                file_data = h_fd
            else:
                stderr_txt += "\n--- Auto-repair attempt also failed ---\n" + h_err

        rem = [o for o in bpy.data.objects if o.type in _PRINTABLE_TYPES]
        if status == "error" and snap_ok and snap_names and not rem and os.path.isfile(SNAPSHOT_BLEND):
            try:
                with bpy.data.libraries.load(SNAPSHOT_BLEND, link=False) as (data_from, data_to):
                    data_to.objects = list(snap_names)
                col = _ensure_collection("CAD_Printable_Parts")
                for obj in data_to.objects:
                    if obj is not None:
                        col.objects.link(obj)
                stdout_txt += "\n[Restored previous printable scene objects after script failure.]\n"
            except Exception:
                pass

    if not is_internal:
        _log_last_exec(code_str, status, stdout_txt, stderr_txt)

    return {
        "status": status,
        "stdout": stdout_txt,
        "stderr": stderr_txt,
        "file_data": file_data,
    }


def process_queue_timer():
    if not server_running:
        return None
    try:
        while True:
            task = task_queue.get_nowait()
            result_queue.put(execute_code_in_main_thread(task))
            task_queue.task_done()
    except queue.Empty:
        pass
    return 0.05


def recv_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        pkt = sock.recv(length - len(data))
        if not pkt:
            return None
        data.extend(pkt)
    return bytes(data)


def socket_listener_loop(direct_main_thread=False):
    global server_running
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((HOST, PORT))
        server.listen(4)
        mode = "headless-main" if direct_main_thread else "gui-timer"
        print(f"[Server] Blender socket server started on {HOST}:{PORT} ({mode})", flush=True)
    except Exception as exc:
        print(f"[Server] Bind failed: {exc}", flush=True)
        server_running = False
        server.close()
        return
    server.settimeout(1.0)
    while server_running:
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        except Exception:
            if not server_running:
                break
            continue
        conn.settimeout(120.0)
        try:
            while server_running:
                len_bytes = recv_exact(conn, 4)
                if not len_bytes:
                    break
                payload_len = int.from_bytes(len_bytes, "big")
                if payload_len <= 0 or payload_len > 32 * 1024 * 1024:
                    break
                code_bytes = recv_exact(conn, payload_len)
                if not code_bytes:
                    break
                code_str = code_bytes.decode("utf-8")
                if direct_main_thread:
                    result = execute_code_in_main_thread(code_str)
                else:
                    task_queue.put(code_str)
                    result = result_queue.get()
                raw = json.dumps(result).encode("utf-8")
                conn.sendall(len(raw).to_bytes(4, "big") + raw)
                if not server_running:
                    break
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    server.close()


def _init_millimeter_workspace():
    if bpy is not None and not bpy.data.filepath:
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 0.001


def start_server():
    global server_running, server_thread, timer_registered
    if bpy is None:
        return
    _init_millimeter_workspace()
    server_running = True
    if getattr(bpy.app, "background", False):
        socket_listener_loop(direct_main_thread=True)
        return
    if hasattr(bpy.app, "timers") and not timer_registered:
        bpy.app.timers.register(process_queue_timer, persistent=True)
        timer_registered = True
    if server_thread is None or not server_thread.is_alive():
        server_thread = threading.Thread(
            target=socket_listener_loop, kwargs={"direct_main_thread": False},
            name="BlenderSocketServer", daemon=True,
        )
        server_thread.start()


if __name__ == "__main__":
    start_server()
