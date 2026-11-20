# /// script
# requires-python = ">=3.10"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Blender Studio"
# description = "Live Blender (:9999) procedural bpy CAD, hardware mockups, diagnostic EEVEE render studio, AI CAD assistant, and one-click Send to Plate."
# author = "Gabrial McCauley (GDME LLC)"
# version = "1.2.4"
# license = "MIT"
# ///
"""Blender Studio — OrcaSlicer Pages plugin for live procedural Blender CAD.

Bridges Blender 4.x/5.x and OrcaSlicer in a single top-bar workspace tab:
  * Start/stop/attach to Blender GUI or headless background daemon on port 9999
  * Browse, open, and create .blend projects in your 3D designs folder
  * Inspect printable CAD meshes vs. hardware/cable mockups with mm bounding boxes
  * Run interactive bpy code & built-in CAD macros (Studio Rig, Hardware Mockups, Print Audit)
  * Prompt local Ollama, Google Gemini, or any OpenAI-compatible LLM to write & run bpy code
  * Render multi-angle diagnostic views (Hero, Top, Front, Isometric, Exploded, Active Cam)
  * Export binary STL and Send to Plate directly in the running OrcaSlicer window
"""
from __future__ import annotations

import base64
import ctypes
import glob
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Any

try:
    import orca  # type: ignore[import-not-found]
except ImportError:
    orca = None


# --------------------------------------------------------------------------- #
# Paths & Cross-Platform Auto-Discovery (avoid audit keywords: conf/config/secret)
# --------------------------------------------------------------------------- #
PLUGIN_VERSION = "1.2.4"
PLUGIN_DIR = Path(__file__).resolve().parent


def _resolve_data_dir() -> Path:
    for parent in PLUGIN_DIR.parents:
        if parent.name.lower() == "orca_plugins":
            return parent.parent
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "OrcaSlicer"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OrcaSlicer"
    return Path.home() / ".config" / "OrcaSlicer"


def detect_blender_executable() -> str:
    """Locate Blender across Linux, Windows, and macOS without hardcoded usernames."""
    env_bin = (os.environ.get("BLENDER_BIN") or "").strip()
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    candidates: list[str] = []
    if sys.platform.startswith("win"):
        for env_key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env_key)
            if base:
                candidates.extend(sorted(
                    glob.glob(os.path.join(base, "Blender Foundation", "Blender *", "blender.exe")),
                    reverse=True,
                ))
        candidates.append(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")
    elif sys.platform == "darwin":
        candidates.extend([
            "/Applications/Blender.app/Contents/MacOS/Blender",
            str(Path.home() / "Applications" / "Blender.app" / "Contents" / "MacOS" / "Blender"),
        ])
    else:
        candidates.extend(sorted(
            glob.glob(str(Path.home() / "Blender" / "blender-*" / "blender")),
            reverse=True,
        ))
        candidates.extend([
            "/usr/local/bin/blender",
            "/usr/bin/blender",
            "/snap/bin/blender",
            "/var/lib/flatpak/exports/bin/org.blender.Blender",
        ])

    which_bin = shutil.which("blender")
    if which_bin:
        candidates.append(which_bin)

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return "blender.exe" if sys.platform.startswith("win") else "blender"


def detect_projects_dir() -> str:
    """Resolve default 3D designs folder across user setups."""
    home = Path.home()
    for cand in (home / "projects" / "3D_Designs", home / "3D_Designs", home / "Documents" / "3D_Designs"):
        if cand.is_dir():
            return str(cand)
    return str(STUDIO_DIR / "projects")


def detect_template_blend(projects_dir: str) -> str:
    cand = Path(projects_dir) / "Server_Template.blend"
    return str(cand) if cand.is_file() else ""


ORCA_DATA_DIR = _resolve_data_dir()
STUDIO_DIR = ORCA_DATA_DIR / "blender_studio"
BOOTSTRAP_SCRIPT = STUDIO_DIR / "server_bootstrap.py"
ICON_FILE = STUDIO_DIR / "tab_icon.svg"
RENDERS_DIR = STUDIO_DIR / "renders"
UPLOADS_DIR = STUDIO_DIR / "uploads"
SETTINGS_FILE = STUDIO_DIR / "studio_settings.json"

DEFAULT_BLENDER_BIN = detect_blender_executable()
DEFAULT_PROJECTS_DIR = detect_projects_dir()
DEFAULT_TEMPLATE_BLEND = detect_template_blend(DEFAULT_PROJECTS_DIR)

IMPORTABLE_EXT = {".stl", ".3mf", ".obj", ".amf", ".step", ".stp"}
MEDIA_EXT = {".png", ".webp", ".gif", ".jpg", ".jpeg"}
MOCKUP_PREFIXES = ("M_", "Mockup_", "Vis_", "Plug_", "Ground_Shadow", "Drawing_Paper")

DEFAULT_SETTINGS: dict[str, Any] = {
    "blender_bin": DEFAULT_BLENDER_BIN,
    "projects_dir": DEFAULT_PROJECTS_DIR,
    "template_blend": DEFAULT_TEMPLATE_BLEND,
    "server_host": "127.0.0.1",
    "server_port": 9999,
    "ollama_url": "http://127.0.0.1:11435",
    "ollama_fallback_url": "http://127.0.0.1:11434",
    "ai_provider": "ollama",
    "ai_model": "qwen2.5-coder:7b",
    "gemini_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "render_samples": 16,
    "render_res_x": 1280,
    "render_res_y": 720,
    "single_instance_handoff": True,
    "auto_plate_after_export": True,
}

TAB_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" fill="none">
  <path d="M12 2.5L20 7.1V16.9L12 21.5L4 16.9V7.1L12 2.5Z" stroke="#E87D0D" stroke-width="1.8" stroke-linejoin="round"/>
  <path d="M12 12L20 7.1M12 12L4 7.1M12 12V21.5" stroke="#26A69A" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="12" cy="12" r="2.3" fill="#E87D0D" stroke="#1E1F22" stroke-width="0.9"/>
</svg>
"""

BOOTSTRAP_SOURCE = r'''"""Dual-mode Blender TCP socket server bootstrap (GUI timer + headless daemon + cad helper)."""
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
    rotation=(0.0, 0.0, 0.0),
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
    obj.rotation_euler = tuple(float(r) for r in rotation)
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
    if isinstance(cutter, (list, tuple)):
        base = target
        for c in cutter:
            base = _cad_boolean(base, c, "DIFFERENCE", delete_tool=delete_cutter)
        return base
    return _cad_boolean(target, cutter, "DIFFERENCE", delete_tool=delete_cutter)


def _cad_union(target, tool=None, delete_tool=True):
    if tool is None and isinstance(target, (list, tuple)) and target:
        base = _resolve_obj(target[0])
        for t in target[1:]:
            base = _cad_boolean(base, t, "UNION", delete_tool=delete_tool)
        return base
    if isinstance(target, str) and (bpy is None or bpy.data.objects.get(target) is None) and isinstance(tool, (list, tuple)) and tool:
        base = _resolve_obj(tool[0])
        if base:
            base.name = target
        for t in tool[1:]:
            base = _cad_boolean(base, t, "UNION", delete_tool=delete_tool)
        return base
    if isinstance(tool, (list, tuple)):
        base = target
        for t in tool:
            base = _cad_boolean(base, t, "UNION", delete_tool=delete_tool)
        return base
    return _cad_boolean(target, tool, "UNION", delete_tool=delete_tool)


def _cad_intersect(target, tool, delete_tool=True):
    if isinstance(tool, (list, tuple)):
        base = target
        for t in tool:
            base = _cad_boolean(base, t, "INTERSECT", delete_tool=delete_tool)
        return base
    return _cad_boolean(target, tool, "INTERSECT", delete_tool=delete_tool)


def _cad_boolean(target, tool, operation="DIFFERENCE", delete_tool=True):
    target = _resolve_obj(target)
    tool = _resolve_obj(tool)
    if target is None or tool is None:
        return target
    if hasattr(bpy.context, "view_layer"):
        bpy.context.view_layer.update()
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


def _cad_hole(target, diameter=3.4, depth=100.0, location=(0.0, 0.0, 0.0), axis="Z"):
    lx, ly, lz = _pt3(location)
    cutter = _cad_cylinder(
        name="_hole_cut", diameter=diameter, height=depth,
        location=(lx, ly, lz), centered_z=True,
    )
    ax = str(axis or "Z").upper()
    if ax == "X":
        cutter.rotation_euler = (0.0, math.pi * 0.5, 0.0)
    elif ax == "Y":
        cutter.rotation_euler = (math.pi * 0.5, 0.0, 0.0)
    bpy.context.view_layer.update()
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


def _pt3(pt, default_z=0.0):
    vals = [float(c) for c in pt]
    if len(vals) == 2:
        return (vals[0], vals[1], float(default_z))
    return (vals[0], vals[1], vals[2])


def _cad_count_mesh_islands(bm):
    if not bm.faces:
        return 0
    seen = set()
    islands = 0
    for f in bm.faces:
        if f in seen:
            continue
        islands += 1
        stack = [f]
        seen.add(f)
        while stack:
            cur = stack.pop()
            for e in cur.edges:
                for nf in e.link_faces:
                    if nf not in seen:
                        seen.add(nf)
                        stack.append(nf)
    return islands


def _cad_capsule_bmesh(start, end, width=10.0, thickness=6.0, end_width=None, flat_bottom_z=None, segments=24):
    """Build a watertight manifold capsule/stadium prism (or gusseted ramp) connecting two 3D points."""
    sx, sy, sz = _pt3(start)
    ex, ey, ez = _pt3(end)
    r1 = max(0.2, float(width) * 0.5)
    r2 = max(0.2, float(end_width) * 0.5) if end_width is not None else r1
    t = max(0.2, float(thickness))
    seg = max(8, int(segments))
    zs_lo = float(flat_bottom_z) if flat_bottom_z is not None else sz
    zs_hi = max(zs_lo + 0.2, sz + t)
    ze_lo = float(flat_bottom_z) if flat_bottom_z is not None else ez
    ze_hi = max(ze_lo + 0.2, ez + t)
    bm = bmesh.new()
    for (cx, cy, r, zlo, zhi) in ((sx, sy, r1, zs_lo, zs_hi), (ex, ey, r2, ze_lo, ze_hi)):
        for i in range(seg):
            ang = (2.0 * math.pi * i) / seg
            px = cx + r * math.cos(ang)
            py = cy + r * math.sin(ang)
            bm.verts.new((px, py, zlo))
            bm.verts.new((px, py, zhi))
    res = bmesh.ops.convex_hull(bm, input=list(bm.verts))
    drop = list({
        g for g in (res.get("geom_interior", []) + res.get("geom_unused", []))
        if isinstance(g, bmesh.types.BMVert) and g.is_valid
    })
    if drop:
        bmesh.ops.delete(bm, geom=drop, context="VERTS")
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def _cad_strut(
    name="Strut",
    start=(0.0, 0.0, 0.0),
    end=(25.0, 0.0, 0.0),
    width=10.0,
    thickness=6.0,
    end_width=None,
    flat_bottom=False,
    flat_bottom_z=None,
    segments=24,
    color=None,
    mockup=False,
):
    """Create a rigid manifold structural arm/gusset connecting 3D points `start` and `end`."""
    p1 = _pt3(start)
    p2 = _pt3(end)
    fbz = float(flat_bottom_z) if flat_bottom_z is not None else (min(p1[2], p2[2]) if flat_bottom else None)
    bm = _cad_capsule_bmesh(
        start=p1, end=p2, width=width, thickness=thickness,
        end_width=end_width, flat_bottom_z=fbz, segments=segments,
    )
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    col = _ensure_collection("Hardware_Mockups" if mockup else "CAD_Printable_Parts")
    _link_only(obj, col)
    if color is not None:
        _cad_color(obj, color)
    return obj


def _cad_slot(target, start=(0.0, 0.0, 0.0), end=(10.0, 0.0, 0.0), diameter=3.4, depth=40.0):
    """Cut an elongated mounting/adjustment slot between two XY(Z) points."""
    p1 = _pt3(start)
    p2 = _pt3(end)
    z_mid = (p1[2] + p2[2]) * 0.5
    h = max(1.0, float(depth))
    bm = _cad_capsule_bmesh(
        start=(p1[0], p1[1], z_mid - h * 0.5),
        end=(p2[0], p2[1], z_mid - h * 0.5),
        width=diameter,
        thickness=h,
        segments=24,
    )
    mesh = bpy.data.meshes.new("_slot_cut")
    bm.to_mesh(mesh)
    bm.free()
    cutter = bpy.data.objects.new("_slot_cut", mesh)
    _link_only(cutter, _ensure_collection("CAD_Printable_Parts"))
    bpy.context.view_layer.update()
    return _cad_cut(target, cutter, delete_cutter=True)


def _cad_counterbore_hole(
    target,
    diameter=3.4,
    cb_diameter=6.4,
    cb_depth=3.5,
    depth=40.0,
    location=(0.0, 0.0, 0.0),
    axis="Z",
    flip=False,
    segments=32,
):
    """Cut a through-hole plus coaxial counterbore starting at `location` inward along `-axis` (or `+axis` if flip)."""
    if isinstance(axis, str):
        ax_map = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
        base_dir = mathutils.Vector(ax_map.get(axis.upper(), (0.0, 0.0, 1.0)))
    else:
        base_dir = mathutils.Vector([float(c) for c in axis])
    if base_dir.length < 1e-6:
        base_dir = mathutils.Vector((0.0, 0.0, 1.0))
    d_vec = base_dir.normalized() * (1.0 if flip else -1.0)
    up_axis = "X" if abs(d_vec.y) > 0.9 else "Y"
    rot_eu = d_vec.to_track_quat("Z", up_axis).to_euler()
    loc_v = mathutils.Vector(_pt3(location)) - d_vec * 0.5

    thru = _cad_cylinder(
        name="_cb_thru", diameter=diameter, height=float(depth) + 0.5,
        location=tuple(loc_v), segments=segments,
    )
    thru.rotation_euler = rot_eu
    cb = _cad_cylinder(
        name="_cb_head", diameter=cb_diameter, height=float(cb_depth) + 0.5,
        location=tuple(loc_v), segments=segments,
    )
    cb.rotation_euler = rot_eu
    bpy.context.view_layer.update()
    _cad_cut(target, thru, delete_cutter=True)
    return _cad_cut(target, cb, delete_cutter=True)


def _cad_aim_rotation(from_point, to_point, forward="-Z", up="Y"):
    """Compute a Euler rotation tracking `forward` from `from_point` toward `to_point`."""
    direction = mathutils.Vector(_pt3(to_point)) - mathutils.Vector(_pt3(from_point))
    if direction.length < 1e-6:
        return mathutils.Euler((0.0, 0.0, 0.0))
    return direction.to_track_quat(forward, up).to_euler()


def _cad_aim_at(target, target_point, forward="-Z", up="Y"):
    """Rotate `target` in place so its `forward` local axis points directly at `target_point`."""
    target = _resolve_obj(target)
    if target is None:
        return None
    target.rotation_euler = _cad_aim_rotation(target.location, target_point, forward=forward, up=up)
    if hasattr(bpy.context, "view_layer"):
        bpy.context.view_layer.update()
    return target


def _cad_local_pocket(
    target,
    width=21.6,
    depth=18.2,
    height=4.0,
    local_offset=(0.0, 0.0, 0.0),
    corner_radius=1.0,
    lens_hole_dia=0.0,
    lens_offset=(0.0, 0.0),
    through_depth=25.0,
):
    """Cut a rectangular pocket (and optional lens bore) aligned in `target`'s local coordinate frame."""
    target = _resolve_obj(target)
    if target is None:
        return None
    bpy.context.view_layer.update()
    off = mathutils.Vector(_pt3(local_offset))
    pocket = _cad_box(
        name="_local_pocket", width=width, depth=depth, height=height,
        location=(0.0, 0.0, 0.0), corner_radius=corner_radius,
    )
    pocket.matrix_world = target.matrix_world @ mathutils.Matrix.Translation(off)
    bpy.context.view_layer.update()
    _cad_cut(target, pocket, delete_cutter=True)

    if lens_hole_dia and float(lens_hole_dia) > 0.1:
        lo_x, lo_y = float(lens_offset[0]), float(lens_offset[1])
        bore = _cad_cylinder(
            name="_local_lens_bore", diameter=lens_hole_dia, height=float(through_depth),
            location=(0.0, 0.0, 0.0), centered_z=True, segments=36,
        )
        bore.matrix_world = target.matrix_world @ mathutils.Matrix.Translation(mathutils.Vector((lo_x, lo_y, 0.0)))
        bpy.context.view_layer.update()
        _cad_cut(target, bore, delete_cutter=True)
    return target


def _cad_oriented_box(
    name="Housing",
    width=26.0,
    depth=22.0,
    height=10.0,
    location=(0.0, 0.0, 0.0),
    aim_at=None,
    rotation=None,
    pocket_size=None,
    pocket_offset=(0.0, 0.0, 0.0),
    aperture_diameter=0.0,
    usb_slot_size=None,
    corner_radius=2.5,
    color=None,
    mockup=False,
):
    """Create a housing with local-frame pocket/aperture/USB cuts, aimed and baked into world verts."""
    w, d, h = max(1.0, float(width)), max(1.0, float(depth)), max(1.0, float(height))
    obj = _cad_box(
        name=name, width=w, depth=d, height=h,
        location=(0.0, 0.0, 0.0), corner_radius=corner_radius,
        color=color, mockup=mockup,
    )
    if pocket_size is not None and len(pocket_size) >= 3:
        pw, pd, ph = float(pocket_size[0]), float(pocket_size[1]), float(pocket_size[2])
        px, py, pz = _pt3(pocket_offset)
        p_cut = _cad_box(
            name="_ob_pocket", width=pw, depth=pd, height=ph + 2.0,
            location=(px, py, max(-0.5, h - ph + pz)), corner_radius=min(1.0, float(corner_radius) * 0.5),
        )
        _cad_cut(obj, p_cut, delete_cutter=True)

    if aperture_diameter and float(aperture_diameter) > 0.1:
        px, py, _ = _pt3(pocket_offset)
        ap = _cad_cylinder(
            name="_ob_aperture", diameter=float(aperture_diameter), height=h + 4.0,
            location=(px, py, -2.0), segments=36,
        )
        _cad_cut(obj, ap, delete_cutter=True)

    if usb_slot_size is not None and len(usb_slot_size) >= 2:
        sw, sh = float(usb_slot_size[0]), float(usb_slot_size[1])
        ph_ref = float(pocket_size[2]) if (pocket_size is not None and len(pocket_size) >= 3) else h * 0.6
        usb_cut = _cad_box(
            name="_ob_usb", width=sw, depth=d * 0.65, height=sh + 1.0,
            location=(0.0, -d * 0.42, max(-0.5, h - ph_ref - 0.5)), corner_radius=0.8,
        )
        _cad_cut(obj, usb_cut, delete_cutter=True)

    loc_v = mathutils.Vector(_pt3(location))
    if aim_at is not None:
        direction = mathutils.Vector(_pt3(aim_at)) - loc_v
        if direction.length > 1e-6:
            d_n = direction.normalized()
            up_ax = "X" if abs(d_n.y) > 0.9 else "Y"
            rot_eu = d_n.to_track_quat("Z", up_ax).to_euler()
        else:
            rot_eu = mathutils.Euler((0.0, 0.0, 0.0))
    elif rotation is not None:
        rot_eu = mathutils.Euler(tuple(float(r) for r in rotation))
    else:
        rot_eu = mathutils.Euler((0.0, 0.0, 0.0))

    xform = mathutils.Matrix.Translation(loc_v) @ rot_eu.to_matrix().to_4x4()
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.transform(bm, matrix=xform, verts=bm.verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    return obj


def _cad_place_on_bed(target, z_floor=0.0):
    """Translate `target` vertically so its world bounding-box bottom rests at `z_floor`."""
    target = _resolve_obj(target)
    if target is None or target.type != "MESH" or not target.data.vertices:
        return target
    min_z = min((target.matrix_world @ v.co).z for v in target.data.vertices)
    dz = float(z_floor) - min_z
    if abs(dz) > 1e-5:
        if target.location.length < 1e-5 and sum(abs(r) for r in target.rotation_euler) < 1e-5:
            bm = bmesh.new()
            bm.from_mesh(target.data)
            bmesh.ops.translate(bm, verts=bm.verts, vec=(0.0, 0.0, dz))
            bm.to_mesh(target.data)
            bm.free()
        else:
            target.location.z += dz
    bpy.context.view_layer.update()
    return target


def _cad_audit():
    """Inspect all printable meshes for manifoldness, connected islands, scale, and Z floor."""
    results = []
    issue_msgs = []
    issues = 0
    for o in sorted(bpy.data.objects, key=lambda x: x.name):
        if o.type != "MESH" or o.name.startswith(_MOCKUP_PREFIXES):
            continue
        cols = " ".join(c.name for c in o.users_collection).lower()
        if ("mockup" in cols or "plugs" in cols) and "printable" not in cols:
            continue
        bm = bmesh.new()
        bm.from_mesh(o.data)
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        loose_verts = sum(1 for v in bm.verts if not v.link_edges)
        islands = _cad_count_mesh_islands(bm)
        vol = bm.calc_volume(signed=True)
        bm.free()
        corners = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
        min_z = min((c.z for c in corners), default=0.0)
        max_z = max((c.z for c in corners), default=0.0)
        scale_ok = all(abs(s - 1.0) < 1e-3 for s in o.scale)
        flags = []
        if non_manifold:
            flags.append(f"non-manifold={non_manifold}")
        if loose_verts:
            flags.append(f"loose_verts={loose_verts}")
        if islands > 1:
            flags.append(f"disconnected_islands={islands}")
        if islands == 0:
            flags.append("empty_mesh")
        if vol < 0:
            flags.append("inverted_normals")
        if not scale_ok:
            flags.append(f"unapplied_scale={[round(s,3) for s in o.scale]}")
        if min_z < -0.5:
            flags.append(f"below_bed(min_Z={min_z:.2f}mm)")
        if flags:
            issues += 1
            issue_msgs.append(f"{o.name}: " + ", ".join(flags))
        info = {
            "name": o.name,
            "dims": [round(o.dimensions.x, 2), round(o.dimensions.y, 2), round(o.dimensions.z, 2)],
            "min_z": round(min_z, 2),
            "max_z": round(max_z, 2),
            "islands": islands,
            "non_manifold": non_manifold,
            "loose_verts": loose_verts,
            "flags": flags,
        }
        results.append(info)
        status = "OK" if not flags else "WARN: " + ", ".join(flags)
        print(f"  [Audit] {o.name}: {info['dims'][0]}x{info['dims'][1]}x{info['dims'][2]}mm | islands={islands} | min_Z={min_z:.2f}mm | {status}")
    if not results:
        issue_msgs.append("No printable mesh objects found in scene.")
        print("  [Audit] WARN: No printable mesh objects found in scene.")
    return {"ok": issues == 0 and len(results) > 0, "issues": issues, "issue_messages": issue_msgs, "objects": results}


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
    mod.strut = _cad_strut
    mod.slot = _cad_slot
    mod.counterbore_hole = _cad_counterbore_hole
    mod.oriented_box = _cad_oriented_box
    mod.place_on_bed = _cad_place_on_bed
    mod.aim_rotation = _cad_aim_rotation
    mod.aim_at = _cad_aim_at
    mod.local_pocket = _cad_local_pocket
    mod.audit = _cad_audit
    mod.audit_geometry = _cad_audit
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
            fh.write("--- CODE ---\n" + code_str[:24000] + "\n")
            if stdout_txt:
                fh.write("--- STDOUT ---\n" + stdout_txt[:4000] + "\n")
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
        "- cad.box(name, width, depth, height, location=(0,0,0), corner_radius=0.0, bevel=0.0, color=(0.7,0.7,0.7,1.0), mockup=False, rotation=(0,0,0))\n"
        "- cad.cylinder(name, diameter=10.0, height=5.0, location=(0,0,0), color=(0.7,0.7,0.7,1.0), mockup=False)\n"
        "- cad.tube(name, outer_diameter=12.0, inner_diameter=8.0, height=10.0, location=(0,0,0), color=None)\n"
        "- cad.strut(name, start=(x1,y1,z1), end=(x2,y2,z2), width=10.0, thickness=6.0, flat_bottom=False, flat_bottom_z=None, color=None)\n"
        "- cad.oriented_box(name, width=26.0, depth=22.0, height=10.0, location=(x,y,z), aim_at=(tx,ty,tz), pocket_size=(pw,pd,ph), aperture_diameter=9.5, usb_slot_size=(sw,sh), corner_radius=2.5, color=None)\n"
        "- cad.place_on_bed(target, z_floor=0.0)\n"
        "- cad.text(name, text='HELLO', size=10.0, height=1.2, location=(0,0,2.0), color=(0.91,0.49,0.05,1.0))\n"
        "- cad.image_relief(name='Logo_Relief', image_path=None, width=42.0, depth=28.0, height=1.2, location=(0,0,2.0), color=(0.91,0.49,0.05,1.0))\n"
        "- cad.bevel(target, offset=1.0, segments=4) / cad.color(target, rgba)\n"
        "- cad.cut(target, cutter) / cad.union(target, tool) / cad.hole(target, diameter=3.4, depth=100.0, location=(0,0,0), axis='Z')\n"
        "- cad.slot(target, start=(x1,y1,z), end=(x2,y2,z), diameter=3.4, depth=40.0)\n"
        "- cad.counterbore_hole(target, diameter=3.4, cb_diameter=6.4, cb_depth=3.5, depth=40.0, location=(x,y,z), axis='Z', flip=False)\n"
        "- cad.aim_at(obj, target_point, forward='-Z', up='Y')\n"
        "- cad.local_pocket(target, width=21.6, depth=18.2, height=4.0, local_offset=(0,0,3.5), corner_radius=1.0, lens_hole_dia=0.0, lens_offset=(0,0))\n"
        "- cad.magnet_pocket(target, diameter=6.25, depth=2.20, location=(0,0,0)) / cad.m3_hole(target, depth=20.0, location=(0,0,0))\n"
        "- cad.audit_geometry()\n"
        "NEVER use `bpy` or `bmesh` directly unless strictly needed; prefer `cad.*` helpers.\n"
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
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_thread": 4},
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
    }).encode("utf-8")
    for base in ("http://127.0.0.1:11435", "http://127.0.0.1:11434"):
        try:
            endpoint = base + "/api/chat"
            parsed = urllib.parse.urlparse(endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            req = urllib.request.Request(
                endpoint, data=payload,
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
'''


# --------------------------------------------------------------------------- #
# Helpers: Settings, Safe Paths, Cross-Platform Handoff
# --------------------------------------------------------------------------- #
_EXEC_LOCK = threading.Lock()
_SETTINGS_LOCK = threading.Lock()


def ensure_studio_assets() -> None:
    """Create studio directory, bootstrap script, and icon safely."""
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if not BOOTSTRAP_SCRIPT.is_file() or BOOTSTRAP_SCRIPT.read_text(encoding="utf-8") != BOOTSTRAP_SOURCE:
        BOOTSTRAP_SCRIPT.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
    if not ICON_FILE.is_file():
        ICON_FILE.write_text(TAB_ICON_SVG, encoding="utf-8")


def _sanitize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return out
    for key in (
        "blender_bin", "projects_dir", "template_blend", "server_host",
        "ollama_url", "ollama_fallback_url", "ai_provider", "ai_model",
        "gemini_api_key", "openai_base_url", "openai_api_key", "openai_model",
    ):
        val = raw.get(key)
        if isinstance(val, str):
            stripped = val.strip()
            if stripped or key in ("template_blend", "gemini_api_key", "openai_api_key"):
                out[key] = stripped
    for key, lo, hi in (
        ("server_port", 1024, 65535),
        ("render_samples", 4, 128),
        ("render_res_x", 320, 3840),
        ("render_res_y", 240, 2160),
    ):
        try:
            v = int(raw.get(key, out[key]))
            out[key] = max(lo, min(hi, v))
        except (TypeError, ValueError):
            pass
    for key in ("single_instance_handoff", "auto_plate_after_export"):
        if key in raw:
            out[key] = bool(raw[key])
    # Re-resolve Blender binary if the saved value is missing on disk
    if out["blender_bin"] and not os.path.isfile(out["blender_bin"]) and shutil.which(out["blender_bin"]) is None:
        detected = detect_blender_executable()
        if os.path.isfile(detected) or shutil.which(detected):
            out["blender_bin"] = detected
    return out


def load_studio_settings(cap: Any = None) -> dict[str, Any]:
    """Load merged settings from OrcaSlicer capability store + studio_settings.json."""
    merged = dict(DEFAULT_SETTINGS)
    with _SETTINGS_LOCK:
        if SETTINGS_FILE.is_file():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged.update(data)
            except Exception:
                pass
    if cap is not None:
        getter = getattr(cap, "get_host_config", None)
        if callable(getter):
            try:
                raw_str = getter()
                parsed = json.loads(raw_str) if isinstance(raw_str, str) and raw_str.strip() else {}
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        if v not in (None, ""):
                            merged[k] = v
            except Exception:
                pass
    return _sanitize_settings(merged)


def save_studio_settings(new_raw: dict[str, Any], cap: Any = None) -> tuple[bool, dict[str, Any]]:
    """Persist settings cleanly to both OrcaSlicer host and studio_settings.json."""
    clean = _sanitize_settings(new_raw)
    ok = True
    try:
        STUDIO_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        ok = False
    if cap is not None:
        setter = getattr(cap, "save_host_config", None)
        if callable(setter):
            try:
                setter(json.dumps(clean))
            except Exception:
                pass
    return ok, clean


def human_size(num_bytes: int) -> str:
    size = float(max(0, int(num_bytes)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


_ALLOWED_CHILD_ENV_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY",
    "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
    "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
    "LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "TERM",
    "SYSTEMROOT", "WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP",
}


def child_environment() -> tuple[dict[str, str], str | None]:
    """Construct a minimal, sanitized environment for child processes without leaking full os.environ."""
    env: dict[str, str] = {}
    for k in _ALLOWED_CHILD_ENV_KEYS:
        val = os.environ.get(k)
        if val is not None:
            env[k] = val
    for var in ("APPIMAGE", "APPDIR", "LD_LIBRARY_PATH", "LD_PRELOAD", "UNION_PRELOAD"):
        env.pop(var, None)
    owd = os.environ.get("OWD") or ""
    return env, (owd if os.path.isdir(owd) else None)


def host_executable() -> str:
    """Resolve the running OrcaSlicer executable across Windows, macOS, and Linux."""
    try:
        if sys.platform.startswith("win"):
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf)):
                return buf.value
        elif sys.platform.startswith("linux"):
            appimage = (os.environ.get("APPIMAGE") or "").strip()
            if appimage and os.path.isfile(appimage):
                return os.path.realpath(appimage)
            return os.readlink("/proc/self/exe")
        elif sys.platform == "darwin":
            libc = ctypes.CDLL(ctypes.util.find_library("c"))  # type: ignore[attr-defined]
            size = ctypes.c_uint32(4096)
            buf = ctypes.create_string_buffer(size.value)
            if libc._NSGetExecutablePath(buf, ctypes.byref(size)) == 0:
                return os.path.realpath(buf.value.decode("utf-8", errors="replace"))
    except Exception:
        return ""
    return ""


def _escape_strings_cstyle(strs: list[str]) -> str:
    out = []
    for raw in strs:
        text = str(raw)
        should_quote = (len(strs) == 1 and text == "") or any(
            c in text for c in (" ", "\t", ";", "\\", '"', "\r", "\n")
        )
        if should_quote:
            body = []
            for ch in text:
                if ch in ("\\", '"'):
                    body.append("\\" + ch)
                elif ch == "\r":
                    body.append("\\r")
                elif ch == "\n":
                    body.append("\\n")
                else:
                    body.append(ch)
            out.append('"' + "".join(body) + '"')
        else:
            out.append(text)
    return ";".join(out)


def _win_main_window() -> Any:
    """Find this process's OrcaSlicer HWND on Windows."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetPropW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    user32.GetPropW.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    our_pid = kernel32.GetCurrentProcessId()
    found: list[Any] = []

    def callback(hwnd: Any, _lparam: Any) -> bool:
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != our_pid:
            return True
        buf = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, buf, 256) == 0 or buf.value != "wxWindowNR":
            return True
        if not user32.GetPropW(hwnd, "Instance_Hash_Minor") or not user32.GetPropW(hwnd, "Instance_Hash_Major"):
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        found.append(hwnd)
        return False

    user32.EnumWindows(enum_proc(callback), 0)
    return found[0] if found else None


class _COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", ctypes.c_ulong),
        ("lpData", ctypes.c_void_p),
    ]


def _win_load_into_running_window(path: str) -> bool:
    hwnd = _win_main_window()
    if not hwnd:
        return False
    payload = _escape_strings_cstyle([host_executable() or "orca-slicer", path])
    buf = ctypes.create_unicode_buffer(payload)
    data = _COPYDATASTRUCT(
        1,
        ctypes.sizeof(ctypes.c_wchar) * (len(payload) + 1),
        ctypes.cast(buf, ctypes.c_void_p),
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.SendMessageW(hwnd, 0x004A, None, ctypes.byref(data))
    return True


def _instance_hashes() -> list[str]:
    cache = ORCA_DATA_DIR / "cache"
    if not cache.is_dir():
        return []
    found = []
    try:
        for entry in cache.iterdir():
            if entry.suffix == ".lock" and entry.stem.isdigit():
                try:
                    found.append((entry.stat().st_mtime, entry.stem))
                except OSError:
                    found.append((0.0, entry.stem))
    except OSError:
        return []
    found.sort(reverse=True)
    return [stem for _, stem in found]


def _dbus_align(buf: bytes, size: int) -> bytes:
    rem = len(buf) % size
    return buf + (b"\0" * (size - rem)) if rem else buf


def _dbus_str(value: str | bytes, kind: bytes = b"s") -> bytes:
    raw = bytes(value) if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8")
    if kind == b"g":
        return bytes([len(raw)]) + raw + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0"


def _dbus_header_field(fields: bytes, code: int, kind: bytes, value: str) -> bytes:
    out = _dbus_align(fields, 8) + bytes([code]) + _dbus_str(kind, b"g")
    if kind in (b"s", b"o"):
        return _dbus_align(out, 4) + _dbus_str(value)
    if kind == b"g":
        return out + _dbus_str(value, b"g")
    raise ValueError("unsupported dbus header field")


def _dbus_message(serial: int, dest: str, path: str, iface: str, member: str, body_str: str | None = None) -> bytes:
    body = _dbus_str(body_str) if body_str is not None else b""
    fields = b""
    fields = _dbus_header_field(fields, 1, b"o", path)
    fields = _dbus_header_field(fields, 2, b"s", iface)
    fields = _dbus_header_field(fields, 3, b"s", member)
    fields = _dbus_header_field(fields, 6, b"s", dest)
    if body_str is not None:
        fields = _dbus_header_field(fields, 8, b"g", "s")
    flags = 0x01 if body_str is not None else 0x00
    head = struct.pack("<BBBBII", ord("l"), 1, flags, 1, len(body), serial)
    head += struct.pack("<I", len(fields)) + fields
    head = _dbus_align(head, 8)
    return head + body


def _dbus_load_into_running_window(path: str) -> bool:
    hashes = _instance_hashes()
    if not hashes:
        return False
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    sock_path = ""
    for part in address.split(";"):
        if part.startswith("unix:"):
            for item in part[5:].split(","):
                k, _, v = item.partition("=")
                if k == "path":
                    sock_path = v
                elif k == "abstract":
                    sock_path = "\0" + v
            if sock_path:
                break
    if not sock_path:
        return False
    payload = _escape_strings_cstyle([host_executable() or "orca-slicer", path])
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(4.0)
    try:
        sock.connect(sock_path)
        uid_hex = str(os.getuid()).encode("ascii").hex().encode("ascii")
        sock.sendall(b"\0AUTH EXTERNAL " + uid_hex + b"\r\n")
        if not sock.recv(1024).startswith(b"OK"):
            return False
        sock.sendall(b"BEGIN\r\n")
        sock.sendall(_dbus_message(1, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello"))
        sock.recv(4096)
        iface_prefix = "com.orcaslicer.OrcaSlicer.InstanceCheck.Object"
        obj_prefix = "/com/orcaslicer/OrcaSlicer/InstanceCheck/Object"
        for idx, stem in enumerate(hashes):
            sock.sendall(_dbus_message(
                2 + idx, iface_prefix + stem, obj_prefix + stem,
                iface_prefix + stem, "AnotherInstance", payload
            ))
        return True
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def open_in_orca(path: str, single_instance: bool = True) -> str:
    """Deliver an STL/3MF/STEP file onto the running OrcaSlicer build plate (call from UI thread)."""
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise RuntimeError(f"File not found: {abs_path}")
    if os.path.splitext(abs_path)[1].lower() not in IMPORTABLE_EXT:
        raise RuntimeError(f"Unsupported model extension: {abs_path}")

    if single_instance:
        if sys.platform.startswith("linux") and _dbus_load_into_running_window(abs_path):
            return "window"
        if sys.platform.startswith("win") and _win_load_into_running_window(abs_path):
            return "window"

    exe = host_executable()
    if sys.platform == "darwin":
        app = next((p for p in Path(exe).parents if p.suffix == ".app"), None) if exe else None
        cmd = ["open", "-a", str(app), abs_path] if app else ["open", abs_path]
        subprocess.Popen(cmd, shell=False, close_fds=True)
        return "window"

    if not exe or not os.path.isfile(exe):
        raise RuntimeError("Could not resolve OrcaSlicer executable")
    if "python" in os.path.basename(exe).lower():
        raise RuntimeError(f"Unexpected host executable: {exe}")

    flag = "--single-instance" if single_instance else "--no-single-instance"
    env, cwd = child_environment()
    kwargs: dict[str, Any] = {"shell": False, "close_fds": True, "env": env, "cwd": cwd}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.Popen([exe, flag, abs_path], **kwargs)
    return exe


def safe_inside(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([str(root.resolve()), str(candidate.resolve())]) == str(root.resolve())
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Blender TCP (:9999) Client & Process Helpers
# --------------------------------------------------------------------------- #
def _recv_exact(sock: socket.socket, length: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def is_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def blender_exec(code: str, host: str = "127.0.0.1", port: int = 9999, timeout: float = 30.0) -> dict[str, Any]:
    """Execute Python code inside the live Blender socket server and return parsed JSON."""
    payload = code.encode("utf-8")
    with _EXEC_LOCK:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, int(port)))
            sock.sendall(len(payload).to_bytes(4, "big") + payload)
            hdr = _recv_exact(sock, 4)
            if not hdr:
                return {"ok": False, "stdout": "", "stderr": "Blender closed the connection.", "file_data": None}
            resp_len = int.from_bytes(hdr, "big")
            if resp_len <= 0 or resp_len > 64 * 1024 * 1024:
                return {"ok": False, "stdout": "", "stderr": f"Invalid response size: {resp_len}", "file_data": None}
            raw = _recv_exact(sock, resp_len)
            if not raw:
                return {"ok": False, "stdout": "", "stderr": "Incomplete response from Blender.", "file_data": None}
            parsed = json.loads(raw.decode("utf-8"))
            return {
                "ok": parsed.get("status") == "success",
                "stdout": parsed.get("stdout", ""),
                "stderr": parsed.get("stderr", ""),
                "file_data": parsed.get("file_data"),
            }
        except OSError as exc:
            return {"ok": False, "stdout": "", "stderr": f"Connection failed ({host}:{port}): {exc}", "file_data": None}
        except Exception as exc:
            return {"ok": False, "stdout": "", "stderr": f"Bridge error: {exc}", "file_data": None}
        finally:
            try:
                sock.close()
            except Exception:
                pass


def spawn_blender_process(settings: dict[str, Any], blend_path: str = "", background: bool = True) -> None:
    """Spawn Blender GUI or headless daemon with server_bootstrap.py (MUST run on UI thread)."""
    ensure_studio_assets()
    blender_bin = settings["blender_bin"]
    if not (os.path.isfile(blender_bin) or shutil.which(blender_bin)):
        detected = detect_blender_executable()
        if os.path.isfile(detected) or shutil.which(detected):
            blender_bin = detected
        else:
            raise RuntimeError(f"Blender binary not found: {blender_bin}. Set it in Settings.")

    target = ""
    if blend_path and os.path.isfile(blend_path):
        target = blend_path
    elif settings.get("template_blend") and os.path.isfile(settings["template_blend"]):
        target = settings["template_blend"]

    args = [blender_bin]
    if background:
        args.append("--background")
    if target:
        args.append(target)
    args.extend(["--python", str(BOOTSTRAP_SCRIPT)])

    env, cwd = child_environment()
    env["BLENDER_SERVER_PORT"] = str(settings["server_port"])
    env["BLENDER_SERVER_HOST"] = "0.0.0.0"

    kwargs: dict[str, Any] = {
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": env,
        "cwd": cwd,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if background else 0
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


# --------------------------------------------------------------------------- #
# Remote Blender Introspection, Render, and Export Snippets
# --------------------------------------------------------------------------- #
_INSPECT_CODE = '''
import bpy, json, mathutils, os
scene = bpy.context.scene
printable = []
mockups = []
others = []
mockup_prefixes = ("M_", "Mockup_", "Vis_", "Plug_", "Ground_Shadow", "Drawing_Paper", "Floor")
printable_types = ("MESH", "CURVE", "FONT", "SURFACE", "META")

for o in sorted(bpy.data.objects, key=lambda x: x.name.lower()):
    cols = [c.name for c in o.users_collection]
    if o.type in printable_types:
        corners = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
        xs = [v.x for v in corners]
        ys = [v.y for v in corners]
        zs = [v.z for v in corners]
        dims = [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2), round(max(zs) - min(zs), 2)]
        if o.type != "MESH" and max(dims) <= 0.001:
            others.append({
                "name": o.name,
                "type": o.type,
                "collections": cols,
                "location": [round(v, 2) for v in o.location],
            })
            continue
        center = [round((min(xs)+max(xs))*0.5, 2), round((min(ys)+max(ys))*0.5, 2), round((min(zs)+max(zs))*0.5, 2)]
        bmin = [round(min(xs), 2), round(min(ys), 2), round(min(zs), 2)]
        bmax = [round(max(xs), 2), round(max(ys), 2), round(max(zs), 2)]
        col_str = " ".join(cols).lower()
        is_mockup = (
            o.name.startswith(mockup_prefixes)
            or (("mockup" in col_str or "plugs" in col_str)
                and "printable" not in col_str and "case" not in col_str and "inlay" not in col_str)
        )
        verts = len(o.data.vertices) if (o.type == "MESH" and o.data and hasattr(o.data, "vertices")) else 0
        polys = len(o.data.polygons) if (o.type == "MESH" and o.data and hasattr(o.data, "polygons")) else 0
        mats = [m.name for m in o.data.materials if m] if (o.data and hasattr(o.data, "materials")) else []
        info = {
            "name": o.name,
            "type": o.type,
            "dims": dims,
            "center": center,
            "bmin": bmin,
            "bmax": bmax,
            "verts": verts,
            "polys": polys,
            "scale": [round(s, 3) for s in o.scale],
            "materials": mats,
            "modifiers": [m.name for m in o.modifiers],
            "collections": cols,
            "visible": not o.hide_render and o.visible_get(),
        }
        if is_mockup:
            mockups.append(info)
        else:
            printable.append(info)
    else:
        others.append({
            "name": o.name,
            "type": o.type,
            "collections": cols,
            "location": [round(v, 2) for v in o.location],
        })

__file_data__ = {
    "filepath": bpy.data.filepath or "",
    "filename": os.path.basename(bpy.data.filepath) if bpy.data.filepath else "(Unsaved Scene)",
    "version": bpy.app.version_string,
    "background": bool(bpy.app.background),
    "is_dirty": bool(bpy.data.is_dirty),
    "engine": scene.render.engine,
    "unit_system": scene.unit_settings.system,
    "scale_length": round(scene.unit_settings.scale_length, 6),
    "collections": [c.name for c in bpy.data.collections],
    "printable": printable,
    "mockups": mockups,
    "others": others,
}
'''


def build_open_blend_code(filepath: str) -> str:
    return (
        f"import bpy\n"
        f"bpy.ops.wm.open_mainfile(filepath={json.dumps(filepath)})\n"
        + _INSPECT_CODE
    )


def build_new_project_code(filepath: str, template_path: str = "") -> str:
    if template_path:
        prefix = f"""import bpy, mathutils, os
bpy.ops.wm.open_mainfile(filepath={json.dumps(template_path)})
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 0.001
to_remove = [o for o in bpy.data.objects if o.type in ("MESH", "CURVE", "FONT", "SURFACE", "META")]
for o in to_remove:
    bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
light_presets = [
    (4.5, (85.0, -95.0, 115.0)),
    (1.8, (-95.0, -55.0, 50.0)),
    (2.5, (0.0, 100.0, 85.0)),
]
for idx, lobj in enumerate([o for o in bpy.data.objects if o.type == "LIGHT"]):
    if lobj.location.length < 0.01 and sum(abs(a) for a in lobj.rotation_euler) < 0.01:
        energy, loc = light_presets[idx % len(light_presets)]
        lobj.data.energy = energy
        lobj.location = loc
        lobj.rotation_euler = (mathutils.Vector((0, 0, 0)) - lobj.location).to_track_quat("-Z", "Y").to_euler()
os.makedirs(os.path.dirname({json.dumps(filepath)}), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath={json.dumps(filepath)})
"""
    else:
        prefix = f"""import bpy, mathutils, os
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 0.001
for lname, energy, loc in [
    ("KeyLight", 4.5, (85.0, -95.0, 115.0)),
    ("FillLight", 1.8, (-95.0, -55.0, 50.0)),
    ("RimLight", 2.5, (0.0, 100.0, 85.0)),
]:
    ldata = bpy.data.lights.new(lname, type="SUN")
    ldata.energy = energy
    lobj = bpy.data.objects.new(lname, ldata)
    scene.collection.objects.link(lobj)
    lobj.location = loc
    lobj.rotation_euler = (mathutils.Vector((0, 0, 0)) - lobj.location).to_track_quat("-Z", "Y").to_euler()
cdata = bpy.data.cameras.new("RenderCam")
cam = bpy.data.objects.new("RenderCam", cdata)
scene.collection.objects.link(cam)
cam.data.lens = 45.0
cam.data.clip_start = 0.1
cam.data.clip_end = 5000.0
cam.location = (75.0, -95.0, 65.0)
cam.rotation_euler = (mathutils.Vector((0, 0, 0)) - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam
os.makedirs(os.path.dirname({json.dumps(filepath)}), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath={json.dumps(filepath)})
"""
    return prefix + _INSPECT_CODE


def build_render_code(preset: str, out_path: str, res_x: int, res_y: int, samples: int) -> str:
    return f"""import bpy, math, mathutils, os, base64

scene = bpy.context.scene
preset = {json.dumps(preset)}
out_path = {json.dumps(out_path)}
mockup_prefixes = ("M_", "Mockup_", "Vis_", "Plug_", "Ground_Shadow", "Drawing_Paper", "Floor")

def is_mockup(o):
    cols = " ".join(c.name for c in o.users_collection).lower()
    return o.name.startswith(mockup_prefixes) or (
        ("mockup" in cols or "plugs" in cols) and "printable" not in cols and "case" not in cols
    )

meshes = [o for o in bpy.data.objects if o.type in ("MESH", "CURVE", "FONT", "SURFACE", "META") and not o.hide_render]
focus = [o for o in meshes if not is_mockup(o)] or meshes

saved_locs = {{}}
saved_light_xform = {{}}
temp_objs = []
prev_cam = scene.camera
prev_engine = scene.render.engine
prev_rx = scene.render.resolution_x
prev_ry = scene.render.resolution_y
prev_fmt = scene.render.image_settings.file_format
prev_path = scene.render.filepath
prev_bg_color = None

try:
    if preset == "exploded" and len(focus) > 1:
        centers = []
        for o in focus:
            cs = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
            ctr = sum(cs, mathutils.Vector((0, 0, 0))) / 8.0
            centers.append((o, ctr))
        g_ctr = sum((c for _, c in centers), mathutils.Vector((0, 0, 0))) / len(centers)
        for idx, (o, ctr) in enumerate(centers):
            saved_locs[o.name] = o.location.copy()
            delta = ctr - g_ctr
            if delta.length < 1.0:
                ang = idx * (2.0 * math.pi / len(centers))
                delta = mathutils.Vector((math.cos(ang), math.sin(ang), 0.7)) * 20.0
            else:
                delta = delta * 1.15
                delta.z += (idx - (len(centers) - 1) * 0.5) * 8.0
            o.location = o.location + delta
        bpy.context.view_layer.update()

    pts = []
    for o in focus:
        for c in o.bound_box:
            pts.append(o.matrix_world @ mathutils.Vector(c))
    if not pts:
        pts = [mathutils.Vector((-20, -20, 0)), mathutils.Vector((20, 20, 20))]
    mn = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    center = (mn + mx) * 0.5
    dims = mx - mn
    span = max(dims.x, dims.y, dims.z, 25.0)

    lights = [o for o in bpy.data.objects if o.type == "LIGHT" and not o.hide_render]
    if not lights:
        for lname, energy, loc, rot in [
            ("_Studio_Key", 4.0, (center.x + span*1.5, center.y - span*1.5, center.z + span*2.0), (0.65, 0.0, 0.78)),
            ("_Studio_Fill", 1.8, (center.x - span*1.8, center.y - span*1.0, center.z + span*0.8), (1.1, 0.0, -0.9)),
            ("_Studio_Rim", 2.5, (center.x, center.y + span*1.8, center.z + span*1.4), (-0.8, 0.0, 3.14)),
        ]:
            ldata = bpy.data.lights.new(name=lname, type="SUN")
            ldata.energy = energy
            lobj = bpy.data.objects.new(lname, ldata)
            bpy.context.scene.collection.objects.link(lobj)
            lobj.location = loc
            lobj.rotation_euler = rot
            temp_objs.append(lobj)
    else:
        light_presets = [
            (4.0, (center.x + span*1.5, center.y - span*1.5, center.z + span*2.0)),
            (1.8, (center.x - span*1.8, center.y - span*1.0, center.z + span*0.8)),
            (2.5, (center.x, center.y + span*1.8, center.z + span*1.4)),
        ]
        for idx, lo in enumerate(lights):
            if lo.location.length < 0.01 and sum(abs(a) for a in lo.rotation_euler) < 0.01:
                saved_light_xform[lo.name] = (lo.location.copy(), lo.rotation_euler.copy())
                _, target_loc = light_presets[idx % len(light_presets)]
                lo.location = target_loc
                lo.rotation_euler = (center - lo.location).to_track_quat("-Z", "Y").to_euler()

    if preset != "current" or prev_cam is None:
        cdata = bpy.data.cameras.new("_Studio_Cam")
        cobj = bpy.data.objects.new("_Studio_Cam", cdata)
        bpy.context.scene.collection.objects.link(cobj)
        temp_objs.append(cobj)
        cdata.clip_start = 0.1
        cdata.clip_end = max(5000.0, span * 30.0)
        if preset == "top":
            cdata.type = "ORTHO"
            cdata.ortho_scale = max(dims.x, dims.y, 20.0) * 1.35
            cobj.location = (center.x, center.y, center.z + span * 3.0)
        elif preset == "front":
            cdata.type = "ORTHO"
            cdata.ortho_scale = max(dims.x, dims.z, 20.0) * 1.45
            cobj.location = (center.x, center.y - span * 3.0, center.z)
        elif preset == "iso":
            cdata.type = "ORTHO"
            cdata.ortho_scale = span * 1.55
            cobj.location = center + mathutils.Vector((1.0, -1.0, 0.85)).normalized() * (span * 3.0)
        else:
            cdata.type = "PERSP"
            cdata.lens = 45.0
            cobj.location = center + mathutils.Vector((1.35, -1.65, 1.05)) * span
        direction = center - cobj.location
        cobj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        scene.camera = cobj

    engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    for candidate in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"):
        if candidate in engines:
            scene.render.engine = candidate
            break
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = {int(samples)}
    if scene.world and scene.world.use_nodes:
        bg = scene.world.node_tree.nodes.get("Background")
        if bg and len(bg.inputs) > 0:
            prev_bg_color = tuple(bg.inputs[0].default_value)
            if sum(prev_bg_color[:3]) < 0.06:
                bg.inputs[0].default_value = (0.045, 0.052, 0.065, 1.0)

    scene.render.resolution_x = {int(res_x)}
    scene.render.resolution_y = {int(res_y)}
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    scene.render.filepath = out_path
    bpy.ops.render.render(write_still=True)

    with open(out_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    __file_data__ = {{
        "preset": preset,
        "path": out_path,
        "b64": b64,
        "width": {int(res_x)},
        "height": {int(res_y)},
    }}
finally:
    for name, loc in saved_locs.items():
        if name in bpy.data.objects:
            bpy.data.objects[name].location = loc
    for name, (loc, rot) in saved_light_xform.items():
        if name in bpy.data.objects:
            bpy.data.objects[name].location = loc
            bpy.data.objects[name].rotation_euler = rot
    scene.camera = prev_cam
    scene.render.engine = prev_engine
    scene.render.resolution_x = prev_rx
    scene.render.resolution_y = prev_ry
    scene.render.image_settings.file_format = prev_fmt
    scene.render.filepath = prev_path
    if prev_bg_color is not None and scene.world and scene.world.use_nodes:
        bg = scene.world.node_tree.nodes.get("Background")
        if bg and len(bg.inputs) > 0:
            bg.inputs[0].default_value = prev_bg_color
    for obj in temp_objs:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
"""


def build_export_stl_code(object_names: list[str], dest_path: str) -> str:
    return f"""import bpy, os
targets = {json.dumps(object_names)}
dest = {json.dumps(dest_path)}
mockup_prefixes = ("M_", "Mockup_", "Vis_", "Plug_", "Ground_Shadow", "Drawing_Paper", "Floor")
printable_types = ("MESH", "CURVE", "FONT", "SURFACE", "META")

if targets == ["__all_printable__"]:
    objs = [
        o for o in bpy.data.objects
        if o.type in printable_types and not o.name.startswith(mockup_prefixes)
        and "mockup" not in " ".join(c.name for c in o.users_collection).lower()
        and "plugs" not in " ".join(c.name for c in o.users_collection).lower()
    ]
else:
    objs = [bpy.data.objects[n] for n in targets if n in bpy.data.objects and bpy.data.objects[n].type in printable_types]

if not objs:
    raise RuntimeError("No matching printable geometry objects found to export.")

prev_selected = [o for o in bpy.context.selected_objects]
prev_active = bpy.context.view_layer.objects.active
prev_hidden = {{o.name: (o.hide_viewport, o.hide_get()) for o in objs}}
temp_meshes = []
export_objs = []
try:
    for o in objs:
        o.hide_viewport = False
        o.hide_set(False)
        if o.type == "MESH":
            export_objs.append(o)
        else:
            dup = o.copy()
            dup.data = o.data.copy()
            bpy.context.scene.collection.objects.link(dup)
            bpy.ops.object.select_all(action="DESELECT")
            dup.select_set(True)
            bpy.context.view_layer.objects.active = dup
            bpy.ops.object.convert(target="MESH", keep_original=False)
            temp_meshes.append(dup)
            export_objs.append(dup)

    bpy.ops.object.select_all(action="DESELECT")
    for o in export_objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = export_objs[0]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(
            filepath=dest,
            ascii_format=False,
            export_selected_objects=True,
            global_scale=1.0,
            use_scene_unit=False,
            apply_modifiers=True,
        )
    else:
        bpy.ops.export_mesh.stl(
            filepath=dest,
            ascii=False,
            use_selection=True,
            global_scale=1.0,
            use_scene_unit=False,
            use_mesh_modifiers=True,
        )
    __file_data__ = {{
        "path": dest,
        "size": os.path.getsize(dest),
        "objects": [o.name for o in objs],
    }}
finally:
    for tmp in temp_meshes:
        if tmp.name in bpy.data.objects:
            d = tmp.data
            bpy.data.objects.remove(tmp, do_unlink=True)
            if d and d.users == 0 and isinstance(d, bpy.types.Mesh):
                bpy.data.meshes.remove(d)
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        if o.name in bpy.data.objects:
            hv, hg = prev_hidden.get(o.name, (False, False))
            o.hide_viewport = hv
            o.hide_set(hg)
    for o in prev_selected:
        if o.name in bpy.data.objects:
            o.select_set(True)
    if prev_active and prev_active.name in bpy.data.objects:
        bpy.context.view_layer.objects.active = prev_active
"""


# --------------------------------------------------------------------------- #
# Built-in CAD Macros
# --------------------------------------------------------------------------- #
MACROS: dict[str, str] = {
    "studio_rig": '''# Setup 3-Point Studio Lighting & RenderCam aimed at scene center
import bpy, mathutils
pts = []
for o in bpy.data.objects:
    if o.type == "MESH" and not o.name.startswith("Ground_"):
        pts.extend(o.matrix_world @ mathutils.Vector(c) for c in o.bound_box)
center = (sum(pts, mathutils.Vector((0,0,0))) / len(pts)) if pts else mathutils.Vector((0,0,0))
span = max((max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3)), default=60.0) if pts else 60.0

for name, energy, offset in [
    ("KeyLight", 4.5, (span*1.5, -span*1.5, span*2.0)),
    ("FillLight", 1.8, (-span*1.8, -span*1.0, span*0.8)),
    ("RimLight", 2.5, (0.0, span*1.8, span*1.4)),
]:
    if name in bpy.data.objects:
        lobj = bpy.data.objects[name]
    else:
        ldata = bpy.data.lights.new(name, type="SUN")
        lobj = bpy.data.objects.new(name, ldata)
        bpy.context.scene.collection.objects.link(lobj)
    lobj.data.energy = energy
    lobj.location = center + mathutils.Vector(offset)
    lobj.rotation_euler = (center - lobj.location).to_track_quat("-Z", "Y").to_euler()

if "RenderCam" in bpy.data.objects:
    cam = bpy.data.objects["RenderCam"]
else:
    cdata = bpy.data.cameras.new("RenderCam")
    cam = bpy.data.objects.new("RenderCam", cdata)
    bpy.context.scene.collection.objects.link(cam)
cam.data.lens = 45.0
cam.data.clip_start = 0.1
cam.data.clip_end = 5000.0
cam.location = center + mathutils.Vector((1.35, -1.65, 1.05)) * max(span, 30.0)
cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
bpy.context.scene.camera = cam
print(f"Studio rig configured around {tuple(round(v, 2) for v in center)} (span={span:.1f}mm)")
''',
    "magnet_mockup": '''# Add 6x2mm Neodymium Magnet (+0.25mm press-fit clearance pocket) mockup
import bpy, bmesh
col = bpy.data.collections.get("Hardware_Mockups") or bpy.data.collections.new("Hardware_Mockups")
if col.name not in bpy.context.scene.collection.children:
    bpy.context.scene.collection.children.link(col)
idx = sum(1 for o in bpy.data.objects if o.name.startswith("Mockup_Mag_"))
name = f"Mockup_Mag_{idx}"
mesh = bpy.data.meshes.new(name)
obj = bpy.data.objects.new(name, mesh)
col.objects.link(obj)
bm = bmesh.new()
bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32, radius1=3.125, radius2=3.125, depth=2.20)
bm.to_mesh(mesh)
bm.free()
obj.location = (0.0, 0.0, 1.1)
mat = bpy.data.materials.get("Mat_Magnet") or bpy.data.materials.new("Mat_Magnet")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs["Base Color"].default_value = (0.75, 0.78, 0.82, 1.0)
    bsdf.inputs["Metallic"].default_value = 1.0
    bsdf.inputs["Roughness"].default_value = 0.2
obj.data.materials.append(mat)
print(f"Created {name} (Ø6.25mm x 2.20mm press-fit pocket mockup for 6x2mm magnet)")
''',
    "m3_boss": '''# Add M3 Screw & DIN 934 Captive Hex Nut Mockup
import bpy, bmesh
col = bpy.data.collections.get("Hardware_Mockups") or bpy.data.collections.new("Hardware_Mockups")
if col.name not in bpy.context.scene.collection.children:
    bpy.context.scene.collection.children.link(col)
idx = sum(1 for o in bpy.data.objects if o.name.startswith("M_Screw_M3_"))
name = f"M_Screw_M3_{idx}"
mesh = bpy.data.meshes.new(name)
obj = bpy.data.objects.new(name, mesh)
col.objects.link(obj)
bm = bmesh.new()
bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=24, radius1=1.7, radius2=1.7, depth=10.0)
bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=6, radius1=3.25, radius2=3.25, depth=2.6,
                      matrix=((1,0,0,0),(0,1,0,0),(0,0,1,4.0),(0,0,0,1)))
bm.to_mesh(mesh)
bm.free()
obj.location = (0.0, 0.0, 5.0)
print(f"Created {name} (M3 3.4mm clearance shaft + 5.65mm AF captive hex nut pocket)")
''',
    "sg90_mockup": '''# Add TowerPro SG90 9g Micro Servo Mockup in Hardware_Mockups
import bpy, bmesh
col = bpy.data.collections.get("Hardware_Mockups") or bpy.data.collections.new("Hardware_Mockups")
if col.name not in bpy.context.scene.collection.children:
    bpy.context.scene.collection.children.link(col)
idx = sum(1 for o in bpy.data.objects if o.name.startswith("M_SG90_"))
name = f"M_SG90_{idx}"
mesh = bpy.data.meshes.new(name)
obj = bpy.data.objects.new(name, mesh)
col.objects.link(obj)
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
for v in bm.verts:
    v.co.x *= 23.0
    v.co.y *= 12.5
    v.co.z *= 22.0
bm.to_mesh(mesh)
bm.free()
obj.location = (0.0, 0.0, 11.0)
mat = bpy.data.materials.get("Mat_Servo_Blue") or bpy.data.materials.new("Mat_Servo_Blue")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs["Base Color"].default_value = (0.05, 0.28, 0.85, 1.0)
obj.data.materials.append(mat)
print(f"Created {name} (23.0 x 12.5 x 22.0mm SG90 body mockup)")
''',
    "print_audit": '''# 3D Print Readiness Audit (manifold check, connected islands, scale, Z-floor)
import bpy, bmesh, mathutils
mockup_prefixes = ("M_", "Mockup_", "Vis_", "Plug_", "Ground_Shadow", "Drawing_Paper", "Floor")
print("=== 3D Print Readiness Audit ===")
issues = 0
for o in sorted(bpy.data.objects, key=lambda x: x.name):
    if o.type != "MESH" or o.name.startswith(mockup_prefixes):
        continue
    cols = " ".join(c.name for c in o.users_collection).lower()
    if ("mockup" in cols or "plugs" in cols) and "printable" not in cols:
        continue
    bm = bmesh.new()
    bm.from_mesh(o.data)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    seen = set()
    islands = 0
    for f in bm.faces:
        if f in seen:
            continue
        islands += 1
        stack = [f]
        seen.add(f)
        while stack:
            cur = stack.pop()
            for e in cur.edges:
                for nf in e.link_faces:
                    if nf not in seen:
                        seen.add(nf)
                        stack.append(nf)
    vol = bm.calc_volume(signed=True)
    bm.free()
    corners = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
    min_z = min(c.z for c in corners)
    scale_ok = all(abs(s - 1.0) < 1e-3 for s in o.scale)
    flags = []
    if non_manifold:
        flags.append(f"non-manifold={non_manifold}")
    if loose_verts:
        flags.append(f"loose_verts={loose_verts}")
    if islands > 1:
        flags.append(f"DISCONNECTED_FLOATING_PARTS(islands={islands})")
    elif islands == 0:
        flags.append("empty_mesh")
    if vol < 0:
        flags.append("inverted_normals")
    if not scale_ok:
        flags.append(f"unapplied_scale={[round(s,3) for s in o.scale]}")
    if min_z < -0.5:
        flags.append(f"below_bed(min_Z={min_z:.2f}mm)")
    if flags:
        issues += 1
    status = "OK" if not flags else "WARN: " + ", ".join(flags)
    print(f"  {o.name}: {o.dimensions.x:.2f}x{o.dimensions.y:.2f}x{o.dimensions.z:.2f}mm | islands={islands} | min_Z={min_z:.2f}mm | {status}")
print(f"Audit complete: {issues} object(s) flagged.")
''',
}


# --------------------------------------------------------------------------- #
# AI Assistant Helpers (Local Ollama + Google Gemini + OpenAI-Compatible)
# --------------------------------------------------------------------------- #
def _extract_python_block(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if blocks:
        return max(blocks, key=lambda b: len(b.strip())).strip()
    m_open = re.search(r"```(?:python|py)?\s*\n(.*)$", text, re.DOTALL | re.IGNORECASE)
    if m_open:
        return m_open.group(1).strip()
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if re.match(r"^\s*(?:import\s+(?:bpy|cad|bmesh|math)|from\s+(?:bpy|cad|bmesh)\s+import|cad\.)", line):
            return "\n".join(lines[idx:]).strip()
    if any(tok in text for tok in ("cad.", "bpy.", "bmesh.")):
        return text.strip()
    return ""


def _build_ai_system_prompt(scene_info: dict[str, Any] | None) -> str:
    scene_lines = []
    has_existing_parts = False
    if scene_info and scene_info.get("filepath"):
        scene_lines.append(f"Active .blend: {scene_info.get('filename')} ({scene_info.get('filepath')})")
        scene_lines.append(f"Blender version: {scene_info.get('version', '5.2.0')}")
        printable_objs = scene_info.get("printable", [])
        if printable_objs:
            has_existing_parts = True
            scene_lines.append("Existing Printable Mesh Objects in Scene:")
            for p in printable_objs[:25]:
                scene_lines.append(
                    f"  - `{p['name']}`: dims={p['dims']}mm [X={p['dims'][0]}, Y={p['dims'][1]}, Z={p['dims'][2]}], "
                    f"center={p['center']}, bmin={p['bmin']}, bmax={p['bmax']}, polys={p.get('polys', 0)}"
                )
        mockup_objs = scene_info.get("mockups", [])
        if mockup_objs:
            scene_lines.append("Reference Hardware Mockups in Scene (DO NOT DELETE/OVERWRITE):")
            for m in mockup_objs[:25]:
                scene_lines.append(
                    f"  - `{m['name']}`: center={m['center']}, dims={m.get('dims', [])}, bmin={m.get('bmin', [])}, bmax={m.get('bmax', [])}"
                )
    else:
        scene_lines.append("Active scene: No scene metadata loaded (assume empty 1 BU = 1 mm workspace).")

    rule_clear = (
        "CRITICAL RULE ON `cad.clear()`:\n"
        "- DO NOT CALL `cad.clear()` when modifying an existing part or adding a modular bracket to the scene! Calling `cad.clear()` wipes out the user's existing CAD designs, imported carriage mounts, and reference mockups!\n"
        "- ONLY call `cad.clear()` if the user explicitly commands 'clear scene', 'start from scratch', 'reset', or if the scene is completely empty.\n"
        "- When adding a modular bracket (e.g. `Ender5_BLTouch_Watchdog_Mount` or a separate sensor arm): model ONLY the new bracket/part, referencing existing parts by name via `bpy.data.objects.get(...)` without deleting existing objects.\n"
        if has_existing_parts else
        "SCENE IS EMPTY: Start by calling `cad.clear()` to initialize mm units, collections, and 3-point lighting.\n"
    )

    return (
        "You are an expert procedural CAD engineer building mechanically functional, 3D-printable parts for Blender 5.2.\n"
        "Units: 1 Blender Unit = 1.0 millimeter. Scale must stay strictly at (1, 1, 1).\n\n"
        + rule_clear +
        "\nCOORDINATE SYSTEM & PRIMITIVE RULES (PRECISION GROUNDING):\n"
        "1. `cad.box(name, width, depth, height, location=(cx, cy, z_floor), corner_radius=0.0, centered_z=False)`:\n"
        "   - `width` is X size, `depth` is Y size, `height` is Z size.\n"
        "   - `cx` and `cy` are the CENTER coordinates in X and Y.\n"
        "   - `z_floor` is the bottom face Z coordinate (part spans Z = z_floor to z_floor + height).\n"
        "   - Part extends in world space from [cx - width/2, cy - depth/2, z_floor] to [cx + width/2, cy + depth/2, z_floor + height].\n\n"
        "2. `cad.cylinder(name, diameter=..., height=..., location=(cx, cy, z_floor), segments=64)`:\n"
        "   - Centered in XY at (cx, cy). Bottom face rests at z_floor.\n\n"
        "3. `cad.strut(name, start=(x1, y1, z1), end=(x2, y2, z2), width=10.0, thickness=6.0, flat_bottom=True)`:\n"
        "   - Creates a rigid, manifold capsule/gusset arm bridging points `start` and `end`.\n"
        "   - If `flat_bottom=True`, the bottom of the arm is leveled to min(z1, z2) for clean print bed adhesion.\n"
        "   - Connect struts to the SOLID BACK, BASE, OR MOUNTING FLANGE of sensor pods—NEVER penetrate the camera lens aperture or PCB cavity!\n\n"
        "4. `cad.oriented_box(name, width, depth, height, location=(x,y,z), aim_at=(tx,ty,tz), pocket_size=(pw,pd,ph), aperture_diameter=..., usb_slot_size=(sw,sh), corner_radius=...)`:\n"
        "   - Pre-cuts camera/sensor pocket, lens bore, and USB port in local coordinates, then aims local +Z directly at target (tx, ty, tz).\n"
        "   - The lens looks outward along +Z toward aim_at.\n\n"
        "5. FASTENERS & CUTOUTS (STANDARDIZED DIMENSIONS):\n"
        "   - M3 clearance hole: diameter=3.4 mm.\n"
        "   - M3 socket head cap counterbore: `cad.counterbore_hole(target, diameter=3.4, cb_diameter=6.4, cb_depth=3.5, depth=40.0, location=(x,y,z), axis='Z', flip=False)`.\n"
        "   - M3 heat-set insert pocket: diameter=4.2 mm, depth=4.5 mm.\n"
        "   - BLTouch probe mounting: 2x M3 holes spaced exactly 18.0 mm on center.\n"
        "   - BIQU H2 extruder mounting: 4x M3 rear pattern, 35.0 mm horizontal spacing x 15.0 mm vertical spacing.\n"
        "   - Adjustment slots: `cad.slot(target, start=(x1,y1,z), end=(x2,y2,z), diameter=3.4, depth=40.0)`.\n\n"
        "6. BED ORIENTATION & AUDIT:\n"
        "   - All printable parts must rest flat at Z = 0.0 with positive Z thickness (`cad.place_on_bed(obj)`).\n"
        "   - Always union touching structural solids (`cad.union`) before cutting final mounting holes.\n"
        "   - Always end your script with `cad.audit_geometry()`.\n\n"
        "BUILT-IN `cad` MODULE FUNCTIONS:\n"
        "  cad.clear()                                    # Resets scene (ONLY use when starting from scratch)\n"
        "  cad.box(name, width, depth, height, location=(cx,cy,z), corner_radius=0.0, color=...)\n"
        "  cad.rounded_plate(name, width, depth, thickness, corner_radius=4.0, location=(cx,cy,z))\n"
        "  cad.cylinder(name, diameter=..., height=..., location=(cx,cy,z), segments=64)\n"
        "  cad.tube(name, outer_diameter=..., inner_diameter=..., height=..., location=(cx,cy,z))\n"
        "  cad.strut(name, start=(x1,y1,z1), end=(x2,y2,z2), width=10.0, thickness=6.0, flat_bottom=True)\n"
        "  cad.oriented_box(name, width=26.0, depth=22.0, height=12.0, location=(x,y,z), aim_at=(tx,ty,tz), pocket_size=(21.5,18.0,4.0), aperture_diameter=9.5, usb_slot_size=(10.5,5.0), corner_radius=2.5)\n"
        "  cad.hole(target, diameter=3.4, depth=50.0, location=(x,y,z), axis='Z')\n"
        "  cad.counterbore_hole(target, diameter=3.4, cb_diameter=6.4, cb_depth=3.5, depth=40.0, location=(x,y,z), axis='Z', flip=False)\n"
        "  cad.slot(target, start=(x1,y1,z), end=(x2,y2,z), diameter=3.4, depth=40.0)\n"
        "  cad.magnet_pocket(target, diameter=6.25, depth=2.20, location=(x,y,z))\n"
        "  cad.m3_hole(target, depth=20.0, location=(x,y,z), nut_trap=True)\n"
        "  cad.union(target, tool) / cad.cut(target, cutter) / cad.intersect(target, tool)  # tool can also be a list of objects\n"
        "  cad.place_on_bed(target, z_floor=0.0)\n"
        "  cad.color(target, (r, g, b, 1.0), roughness=0.35)\n"
        "  cad.audit_geometry()\n\n"
        "EXAMPLE 1: MODULAR BLTOUCH + CAMERA POD (Without calling cad.clear()):\n"
        "```python\n"
        "import cad, bpy\n"
        "# 1. Model separate modular BLTouch + camera mount bracket\n"
        "# BLTouch shelf with 18mm center-to-center M3 mounting holes\n"
        "shelf = cad.box('BLTouch_Shelf', width=26.0, depth=14.0, height=4.5, location=(-28.0, -12.0, 0.0), corner_radius=2.0)\n"
        "cad.counterbore_hole(shelf, diameter=3.4, cb_diameter=6.4, cb_depth=2.2, location=(-28.0 - 9.0, -12.0, 4.5), axis='Z', flip=False)\n"
        "cad.counterbore_hole(shelf, diameter=3.4, cb_diameter=6.4, cb_depth=2.2, location=(-28.0 + 9.0, -12.0, 4.5), axis='Z', flip=False)\n\n"
        "# 2. Aimed camera housing for Seeed XIAO Sense (lens aimed at nozzle tip)\n"
        "cam_pod = cad.oriented_box('Cam_Pod', width=24.0, depth=20.0, height=10.0, location=(-36.0, -8.0, 22.0),\n"
        "                           aim_at=(13.9, -18.0, 28.0), pocket_size=(21.2, 17.8, 3.8), aperture_diameter=9.2,\n"
        "                           usb_slot_size=(10.0, 4.5), corner_radius=2.0)\n\n"
        "# 3. Rigid structural strut bridging shelf to pod\n"
        "arm = cad.strut('Pod_Gusset', start=(-28.0, -8.0, 4.0), end=(-36.0, -8.0, 18.0), width=9.0, thickness=5.0, flat_bottom=False)\n"
        "mount = cad.union(shelf, [arm, cam_pod])\n"
        "cad.place_on_bed(mount)\n"
        "cad.audit_geometry()\n"
        "```\n\n"
        "EXAMPLE 2: MODIFYING AN EXISTING MOUNT EAR (Extending ear by 1.0mm):\n"
        "```python\n"
        "import cad, bpy\n"
        "part = bpy.data.objects.get('Ender5_BIQU_H2_Mount')\n"
        "if part:\n"
        "    # Add reinforcing rib / 1mm ear extension\n"
        "    rib = cad.box('Ear_Extension', width=1.0, depth=14.0, height=7.0, location=(-47.0, 18.8, 0.0))\n"
        "    cad.union(part, rib)\n"
        "    cad.audit_geometry()\n"
        "```\n\n"
        "Output a 1-2 sentence concise summary, followed by ONE self-contained ```python``` code block ending with `cad.audit_geometry()`.\n\n"
        "Current Scene Context:\n" + "\n".join(scene_lines)
    )


def _decode_pdf_literal(raw: bytes) -> str:
    """Decode a parenthesized PDF literal string into text."""
    out = bytearray()
    i = 0
    n = len(raw)
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < n:
        ch = raw[i]
        if ch == 92 and i + 1 < n:  # backslash
            nxt = raw[i + 1]
            if nxt in escapes:
                out.append(escapes[nxt])
                i += 2
            elif nxt in (10, 13):  # line continuation
                i += 3 if (nxt == 13 and i + 2 < n and raw[i + 2] == 10) else 2
            elif 48 <= nxt <= 55:  # octal \ddd
                oct_digits = bytes([nxt])
                j = i + 2
                while j < n and len(oct_digits) < 3 and 48 <= raw[j] <= 55:
                    oct_digits += bytes([raw[j]])
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
            else:
                out.append(nxt)
                i += 2
        else:
            out.append(ch)
            i += 1
    data = bytes(out)
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="ignore")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="ignore")


def _decode_pdf_hex(hex_bytes: bytes) -> str:
    """Decode a hex-encoded PDF string (UTF-16BE BOM or 8-bit text)."""
    cleaned = re.sub(rb"\s+", b"", hex_bytes)
    if len(cleaned) % 2 == 1:
        cleaned += b"0"
    try:
        data = bytes.fromhex(cleaned.decode("ascii"))
    except ValueError:
        return ""
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="ignore")
    if len(data) >= 4 and sum(1 for idx in range(0, len(data), 2) if data[idx] == 0) > len(data) // 4:
        return data.decode("utf-16-be", errors="ignore")
    return data.decode("latin-1", errors="ignore")


def _scan_pdf_paren(buf: bytes, start: int) -> tuple[bytes, int]:
    """Scan balanced PDF literal parentheses starting at '(' index and return (inner, next_idx)."""
    depth = 1
    j = start + 1
    n = len(buf)
    while j < n and depth > 0:
        b = buf[j]
        if b == 92:
            j += 2
            continue
        if b == 40:
            depth += 1
        elif b == 41:
            depth -= 1
        j += 1
    end_inner = j - 1 if depth == 0 else j
    return buf[start + 1 : end_inner], j


def _extract_pdf_text_from_stream(stream_bytes: bytes) -> list[str]:
    """Extract text runs from a decoded PDF content stream via Tj/TJ operators."""
    chunks: list[str] = []
    i = 0
    n = len(stream_bytes)
    while i < n:
        c = stream_bytes[i]
        if c == 40:  # '(' literal followed by Tj / ' / "
            inner_bytes, j = _scan_pdf_paren(stream_bytes, i)
            if stream_bytes[j : j + 16].lstrip().startswith((b"Tj", b"'", b'"')):
                txt = _decode_pdf_literal(inner_bytes)
                if txt:
                    chunks.append(txt)
            i = j
        elif c == 91:  # '[' ... ']' TJ
            j = i + 1
            while j < n and stream_bytes[j] != 93:
                if stream_bytes[j] == 40:
                    _, j = _scan_pdf_paren(stream_bytes, j)
                else:
                    j += 1
            if j < n and stream_bytes[j] == 93:
                j += 1
            if stream_bytes[j : j + 16].lstrip().startswith(b"TJ"):
                inner = stream_bytes[i + 1 : j - 1]
                parts: list[str] = []
                k = 0
                m = len(inner)
                while k < m:
                    ic = inner[k]
                    if ic == 40:
                        lit, k = _scan_pdf_paren(inner, k)
                        parts.append(_decode_pdf_literal(lit))
                    elif ic == 60 and not (k + 1 < m and inner[k + 1] == 60):
                        end = inner.find(b">", k + 1)
                        if end != -1:
                            parts.append(_decode_pdf_hex(inner[k + 1 : end]))
                            k = end + 1
                        else:
                            k += 1
                    else:
                        k += 1
                joined = "".join(parts)
                if joined:
                    chunks.append(joined)
            i = j
        elif c == 60 and not (i + 1 < n and stream_bytes[i + 1] == 60):
            end = stream_bytes.find(b">", i + 1)
            if end != -1 and stream_bytes[end + 1 : end + 16].lstrip().startswith(b"Tj"):
                txt = _decode_pdf_hex(stream_bytes[i + 1 : end])
                if txt:
                    chunks.append(txt)
                i = end + 1
            else:
                i += 1
        else:
            i += 1
    return chunks


def _extract_pdf_text(raw_bytes: bytes, max_chars: int = 24000) -> str:
    """Pure-stdlib best-effort PDF text extraction from FlateDecode streams & raw text."""
    extracted: list[str] = []
    streams = [m.group(1) for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw_bytes, re.DOTALL)]
    outside = re.sub(rb"stream\r?\n.*?\r?\nendstream", b"", raw_bytes, flags=re.DOTALL)
    if outside:
        streams.append(outside)

    for chunk in streams:
        candidates = [chunk]
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                candidates.insert(0, zlib.decompress(chunk, wbits))
                break
            except Exception:
                pass
        for stream_data in candidates:
            if b"Tj" in stream_data or b"TJ" in stream_data or b"BT" in stream_data:
                extracted.extend(_extract_pdf_text_from_stream(stream_data))
                break

    text = "\n".join(line.strip() for line in extracted if line.strip())
    if not text:
        noise = {
            "obj", "endobj", "stream", "endstream", "xref", "trailer", "startxref",
            "/Type", "/Font", "/Filter", "/FlateDecode", "/Length", "/Subtype",
            "/Resources", "/Contents", "/Catalog", "/Pages", "/Page", "/MediaBox",
        }
        runs = re.findall(rb"[\x20-\x7e]{4,}", raw_bytes)
        cleaned = []
        for r in runs:
            s = r.decode("ascii", errors="ignore").strip()
            if s and not any(tok in s for tok in noise) and any(ch.isalpha() for ch in s):
                cleaned.append(s)
        text = "\n".join(cleaned)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated at max length]"
    return text


def _extract_step_summary(raw_bytes: bytes, max_holes: int = 80) -> str:
    """Extract bounding boxes, solid names, and cylindrical hole patterns from an ISO-10303-21 STEP file."""
    text = raw_bytes.decode("utf-8", errors="ignore")
    defs = {
        int(m.group(1)): m.group(2).strip()
        for m in re.finditer(r"#(\d+)\s*=\s*([^;]+);", text)
    }
    pts: dict[int, tuple[float, float, float]] = {}
    dirs: dict[int, tuple[float, float, float]] = {}
    axes: dict[int, tuple[Any, Any, Any]] = {}
    for eid, body in defs.items():
        if body.startswith("CARTESIAN_POINT"):
            m = re.match(
                r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-\d.Ee+]+)\s*,\s*([-\d.Ee+]+)\s*,\s*([-\d.Ee+]+)\s*\)\s*\)",
                body,
            )
            if m:
                pts[eid] = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
        elif body.startswith("DIRECTION"):
            m = re.match(
                r"DIRECTION\s*\(\s*'[^']*'\s*,\s*\(\s*([-\d.Ee+]+)\s*,\s*([-\d.Ee+]+)\s*,\s*([-\d.Ee+]+)\s*\)\s*\)",
                body,
            )
            if m:
                dirs[eid] = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    for eid, body in defs.items():
        if body.startswith("AXIS2_PLACEMENT_3D"):
            m = re.match(r"AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*#(\d+)\s*,\s*#(\d+)\s*\)", body)
            if m:
                axes[eid] = (pts.get(int(m.group(1))), dirs.get(int(m.group(2))), dirs.get(int(m.group(3))))

    lines = ["STEP CAD Geometry Summary (units: mm if SI standard):"]
    solids = []
    for eid, body in defs.items():
        m = re.match(r"MANIFOLD_SOLID_BREP\s*\(\s*'([^']*)'\s*,\s*#(\d+)\s*\)", body)
        if m:
            solids.append((m.group(1) or f"Solid_{eid}", int(m.group(2))))

    def _collect_refs(root_id: int) -> set[int]:
        seen: set[int] = set()
        stack = [root_id]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in defs:
                continue
            seen.add(cur)
            for ref in re.findall(r"#(\d+)", defs[cur]):
                stack.append(int(ref))
        return seen

    if solids:
        for sname, shell_id in solids[:12]:
            s_ids = _collect_refs(shell_id)
            s_pts = [pts[k] for k in s_ids if k in pts]
            if not s_pts:
                continue
            xs = [p[0] for p in s_pts]
            ys = [p[1] for p in s_pts]
            zs = [p[2] for p in s_pts]
            lines.append(
                f"Solid `{sname}`: X={min(xs):.2f}..{max(xs):.2f} (w={max(xs)-min(xs):.2f}), "
                f"Y={min(ys):.2f}..{max(ys):.2f} (d={max(ys)-min(ys):.2f}), "
                f"Z={min(zs):.2f}..{max(zs):.2f} (h={max(zs)-min(zs):.2f}) mm"
            )
    elif pts:
        all_p = list(pts.values())
        xs = [p[0] for p in all_p]
        ys = [p[1] for p in all_p]
        zs = [p[2] for p in all_p]
        lines.append(
            f"Overall BBox ({len(all_p)} pts): X={min(xs):.2f}..{max(xs):.2f} ({max(xs)-min(xs):.2f}mm), "
            f"Y={min(ys):.2f}..{max(ys):.2f} ({max(ys)-min(ys):.2f}mm), "
            f"Z={min(zs):.2f}..{max(zs):.2f} ({max(zs)-min(zs):.2f}mm)"
        )

    cyls = []
    for body in defs.values():
        if body.startswith("CYLINDRICAL_SURFACE"):
            m = re.match(r"CYLINDRICAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*([-\d.Ee+]+)\s*\)", body)
            if m:
                aid, r = int(m.group(1)), float(m.group(2))
                pt, d_ax, _ = axes.get(aid, (None, None, None))
                if pt and d_ax:
                    cyls.append((
                        round(r * 2.0, 2),
                        tuple(round(v, 2) for v in pt),
                        tuple(round(abs(v), 2) for v in d_ax),
                    ))
    uniq_cyls = sorted(set(cyls), key=lambda x: (x[2], x[0], x[1]))
    if uniq_cyls:
        lines.append(f"Cylindrical Holes / Bosses ({len(uniq_cyls)} unique):")
        for dia, pt, ax in uniq_cyls[:max_holes]:
            lines.append(f"  - dia={dia:5.2f}mm @ {pt} axis={ax}")
    return "\n".join(lines)


def _extract_3mf_summary(raw_bytes: bytes) -> tuple[str, str]:
    """Extract mesh object bounding boxes and embedded thumbnail PNG (base64) from a .3mf archive."""
    lines = ["3MF CAD Archive Summary:"]
    thumb_b64 = ""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            names = zf.namelist()
            for cand in ("Metadata/thumbnail.png", "Metadata/plate_1.png", "Thumbnails/thumbnail.png"):
                if cand in names:
                    thumb_b64 = base64.b64encode(zf.read(cand)).decode("ascii")
                    break
            model_files = [n for n in names if n.lower().endswith(".model")]
            for mf in model_files[:4]:
                root = ET.fromstring(zf.read(mf))
                objs = [el for el in root.iter() if el.tag.endswith("object")]
                for obj in objs:
                    verts = [
                        (float(v.attrib["x"]), float(v.attrib["y"]), float(v.attrib["z"]))
                        for v in obj.iter()
                        if v.tag.endswith("vertex") and "x" in v.attrib
                    ]
                    if not verts:
                        continue
                    xs, ys, zs = [p[0] for p in verts], [p[1] for p in verts], [p[2] for p in verts]
                    oid = obj.attrib.get("id", "?")
                    oname = obj.attrib.get("name", f"Object_{oid}")
                    lines.append(
                        f"Object `{oname}` ({len(verts)} verts): "
                        f"span=({max(xs)-min(xs):.2f} x {max(ys)-min(ys):.2f} x {max(zs)-min(zs):.2f}) mm | "
                        f"X={min(xs):.2f}..{max(xs):.2f}, Y={min(ys):.2f}..{max(ys):.2f}, Z={min(zs):.2f}..{max(zs):.2f}"
                    )
    except Exception as exc:
        lines.append(f"(Failed to parse 3MF archive: {exc})")
    return "\n".join(lines), thumb_b64


def _extract_stl_summary(raw_bytes: bytes) -> str:
    """Extract triangle count and exact mm bounding box from binary or ASCII STL bytes."""
    mn = [float("inf"), float("inf"), float("inf")]
    mx = [float("-inf"), float("-inf"), float("-inf")]
    tri_count = 0
    is_ascii = raw_bytes[:5].lower() == b"solid" and b"facet normal" in raw_bytes[:2048].lower()
    if is_ascii:
        for m in re.finditer(rb"vertex\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)", raw_bytes):
            tri_count += 1
            for i in range(3):
                val = float(m.group(i + 1))
                mn[i] = min(mn[i], val)
                mx[i] = max(mx[i], val)
        tri_count //= 3
    elif len(raw_bytes) >= 84:
        expected_tris = struct.unpack_from("<I", raw_bytes, 80)[0]
        if 84 + expected_tris * 50 <= len(raw_bytes) + 64:
            tri_count = expected_tris
            off = 84
            for _ in range(min(tri_count, (len(raw_bytes) - 84) // 50)):
                vals = struct.unpack_from("<12f", raw_bytes, off)
                for vi in (3, 6, 9):
                    for ax in range(3):
                        v = vals[vi + ax]
                        mn[ax] = min(mn[ax], v)
                        mx[ax] = max(mx[ax], v)
                off += 50
    if tri_count <= 0 or mn[0] == float("inf"):
        return "STL file attached (could not parse vertex bounds)."
    return (
        f"STL Mesh Summary ({'ASCII' if is_ascii else 'binary'}, {tri_count} triangles):\n"
        f"  dims = ({mx[0]-mn[0]:.2f} x {mx[1]-mn[1]:.2f} x {mx[2]-mn[2]:.2f}) mm\n"
        f"  X={mn[0]:.2f}..{mx[0]:.2f}, Y={mn[1]:.2f}..{mx[1]:.2f}, Z={mn[2]:.2f}..{mx[2]:.2f} mm"
    )


def _prepare_ai_attachments(
    attachments: list[dict[str, Any]] | None,
    viewport_b64: str = "",
) -> tuple[list[dict[str, str]], str, list[str]]:
    """Normalize image and document/CAD attachments for multi-modal and text LLM calls."""
    images: list[dict[str, str]] = []
    doc_sections: list[str] = []
    summary_names: list[str] = []

    if viewport_b64:
        clean_vp = viewport_b64.split(",", 1)[-1].strip() if viewport_b64.startswith("data:") else viewport_b64.strip()
        if clean_vp:
            images.append({"name": "viewport_render.png", "mime": "image/png", "b64": clean_vp})
            summary_names.append("viewport_render.png (viewport)")

    total_doc_chars = 0
    max_per_doc = 24000
    max_total_docs = 64000

    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        name = str(att.get("name") or "attachment").strip() or "attachment"
        kind = str(att.get("kind") or "doc").lower().strip()
        mime = str(att.get("mime") or "").strip()
        raw_b64 = str(att.get("b64") or "").strip()
        if raw_b64.startswith("data:") and "," in raw_b64:
            hdr, raw_b64 = raw_b64.split(",", 1)
            if not mime and ":" in hdr and ";" in hdr:
                mime = hdr.split(":", 1)[1].split(";", 1)[0].strip()

        if kind == "image":
            if raw_b64:
                saved_path = ""
                try:
                    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
                    safe_fn = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name)).strip("._") or f"upload_{int(time.time())}.png"
                    dest_up = UPLOADS_DIR / safe_fn
                    dest_up.write_bytes(base64.b64decode(raw_b64))
                    saved_path = str(dest_up)
                except Exception:
                    saved_path = ""
                images.append({"name": name, "mime": mime or "image/png", "b64": raw_b64, "path": saved_path})
                summary_names.append(f"{name} (image)")
            continue

        low_name = name.lower()
        is_step = kind == "step" or low_name.endswith((".step", ".stp"))
        is_3mf = kind == "3mf" or low_name.endswith(".3mf")
        is_stl = kind == "stl" or low_name.endswith(".stl")
        is_pdf = kind == "pdf" or low_name.endswith(".pdf") or mime == "application/pdf"

        if is_step:
            raw_bytes = base64.b64decode(raw_b64) if raw_b64 else str(att.get("text") or "").encode("utf-8", errors="ignore")
            doc_text = _extract_step_summary(raw_bytes)
            tag = "step"
        elif is_3mf:
            doc_text = ""
            if raw_b64:
                raw_bytes = base64.b64decode(raw_b64)
                doc_text, thumb_b64 = _extract_3mf_summary(raw_bytes)
                if thumb_b64:
                    images.append({"name": f"{name} thumbnail", "mime": "image/png", "b64": thumb_b64, "path": ""})
            tag = "3mf"
        elif is_stl:
            doc_text = _extract_stl_summary(base64.b64decode(raw_b64)) if raw_b64 else ""
            tag = "stl"
        elif is_pdf:
            doc_text = ""
            if raw_b64:
                try:
                    pdf_bytes = base64.b64decode(raw_b64)
                    doc_text = _extract_pdf_text(pdf_bytes, max_chars=max_per_doc)
                except Exception as exc:
                    doc_text = f"(Failed to parse PDF bytes: {exc})"
            if not doc_text:
                doc_text = "(No extractable text found in PDF; it may be scanned or vector-only — try attaching a PNG screenshot of the page.)"
            tag = "pdf"
        else:
            doc_text = str(att.get("text") or "")
            if not doc_text and raw_b64:
                try:
                    doc_text = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
                except Exception:
                    doc_text = ""
            doc_text = doc_text.strip()
            tag = "doc"

        if not doc_text:
            continue

        if len(doc_text) > max_per_doc:
            doc_text = doc_text[:max_per_doc] + f"\n...[truncated at {max_per_doc} chars]"

        remaining = max_total_docs - total_doc_chars
        if remaining <= 200:
            break
        if len(doc_text) > remaining:
            doc_text = doc_text[:remaining] + "\n...[truncated due to total attachment budget]"

        total_doc_chars += len(doc_text)
        doc_sections.append(
            f"--- BEGIN ATTACHED DOCUMENT: {name} ---\n{doc_text}\n--- END ATTACHED DOCUMENT: {name} ---"
        )
        summary_names.append(f"{name} ({tag})")

    return images, "\n\n".join(doc_sections), summary_names


_GEMINI_MODEL_ALIASES: dict[str, str] = {
    "gemini-2.5-flash": "gemini-3-flash-preview",
    "gemini-2.5-pro": "gemini-3.5-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
}
_GEMINI_FALLBACK_ORDER: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
)


def _format_http_error(exc: urllib.error.HTTPError, model: str) -> str:
    raw_body = ""
    try:
        raw_body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    detail = exc.reason or f"HTTP {exc.code}"
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            err_obj = parsed[0] if isinstance(parsed, list) and parsed else parsed
            if isinstance(err_obj, dict):
                detail = str((err_obj.get("error") or {}).get("message") or err_obj.get("message") or detail)
        except Exception:
            detail = raw_body[:240]
    return f"HTTP {exc.code} ({model}): {detail}"


_ALLOWED_URL_SCHEMES = ("http", "https")


def _validate_safe_url(url: str) -> str:
    """Validate that an outbound network request targets an approved HTTP/HTTPS host."""
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(f"Restricted URL scheme '{parsed.scheme}': only HTTP and HTTPS are permitted")
    if not parsed.netloc:
        raise ValueError(f"Invalid network destination '{url}': host is required")
    return url


def _safe_urlopen(req: urllib.request.Request | str, timeout: float = 30.0):
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    _validate_safe_url(url)
    return urllib.request.urlopen(req, timeout=timeout)


def _call_openai_chat(
    endpoint: str,
    api_key: str,
    model: str,
    sys_prompt: str,
    prompt: str,
    images: list[dict[str, str]] | None = None,
    timeout: float = 120.0,
) -> str:
    if images:
        user_content: Any = [{"type": "text", "text": prompt}]
        for img in images:
            mime = img.get("mime") or "image/png"
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{img['b64']}"},
            })
    else:
        user_content = prompt
    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content},
        ],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with _safe_urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_format_http_error(exc, model)) from exc


def _call_gemini_chat(
    api_key: str,
    requested_model: str,
    sys_prompt: str,
    prompt: str,
    images: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    """Call Google Gemini OpenAI endpoint with alias mapping and multi-model failover."""
    endpoint = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    req_name = (requested_model or "gemini-3-flash-preview").strip()
    primary = _GEMINI_MODEL_ALIASES.get(req_name, req_name)
    candidates: list[str] = []
    for cand in (primary, *_GEMINI_FALLBACK_ORDER):
        if cand and cand not in candidates:
            candidates.append(cand)

    errors: list[str] = []
    for cand in candidates:
        try:
            text = _call_openai_chat(endpoint, api_key, cand, sys_prompt, prompt, images, timeout=25.0)
            if not text.strip():
                errors.append(f"{cand}: empty response")
                continue
            note = f"(Used `{cand}` — `{req_name}` was unavailable/busy.) " if cand != req_name else ""
            return text, note
        except RuntimeError as exc:
            err_str = str(exc)
            errors.append(err_str)
            if err_str.startswith(("HTTP 401", "HTTP 403")) and "API key" in err_str:
                raise
        except Exception as exc:
            errors.append(f"{cand}: {exc}")

    raise RuntimeError("All Gemini models failed -> " + " | ".join(errors[:3]))


def _is_ollama_vision_model(model_name: str) -> bool:
    low = model_name.lower()
    return any(tok in low for tok in ("vision", "vl", "llava", "minicpm", "gemma3", "moondream"))


def _pick_ollama_vision_fallback(settings: dict[str, Any]) -> str:
    """Return an installed Ollama vision model name if available, else empty string."""
    preferred = "lukey03/qwen3.5-9b-abliterated-vision:latest"
    for base_url in (settings["ollama_url"], settings["ollama_fallback_url"]):
        endpoint = base_url.rstrip("/") + "/api/tags"
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with _safe_urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            names = [str(m.get("name") or "") for m in (data.get("models") or []) if m.get("name")]
            if preferred in names:
                return preferred
            for name in names:
                if _is_ollama_vision_model(name):
                    return name
            return ""
        except Exception:
            continue
    return ""


def _pick_ollama_coder_model(settings: dict[str, Any], requested: str) -> str:
    """Return a code-focused Ollama model for Stage-2 CAD generation."""
    if requested and not _is_ollama_vision_model(requested):
        return requested
    for base_url in (settings["ollama_url"], settings["ollama_fallback_url"]):
        endpoint = base_url.rstrip("/") + "/api/tags"
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with _safe_urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            names = [str(m.get("name") or "") for m in (data.get("models") or []) if m.get("name")]
            for cand in ("qwen2.5-coder:7b", "qwen2.5-coder:14b", "qwen2.5-coder:32b"):
                if cand in names:
                    return cand
            for n in names:
                if "coder" in n.lower() and not _is_ollama_vision_model(n):
                    return n
            return ""
        except Exception:
            continue
    return ""


def _post_ollama_chat(
    settings: dict[str, Any],
    model: str,
    sys_prompt: str,
    prompt: str,
    image_b64s: list[str],
    num_ctx: int = 8192,
) -> str:
    user_msg: dict[str, Any] = {"role": "user", "content": prompt}
    if image_b64s:
        user_msg["images"] = image_b64s
    payload = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_thread": 4},
        "messages": [
            {"role": "system", "content": sys_prompt},
            user_msg,
        ],
    }).encode("utf-8")
    last_err: Exception | None = None
    for base_url in (settings["ollama_url"], settings["ollama_fallback_url"]):
        endpoint = base_url.rstrip("/") + "/api/chat"
        try:
            req = urllib.request.Request(
                endpoint, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with _safe_urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg_obj = data.get("message") or {}
            return msg_obj.get("content") or msg_obj.get("thinking") or ""
        except Exception as exc:
            last_err = exc
    raise RuntimeError(
        f"Ollama unreachable ({settings['ollama_url']} / {settings['ollama_fallback_url']}): {last_err}"
    )


def call_ai_agent(
    prompt: str,
    provider: str,
    model: str,
    scene_info: dict[str, Any] | None,
    settings: dict[str, Any],
    image_b64: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sys_prompt = _build_ai_system_prompt(scene_info)
    images, doc_block, summary_names = _prepare_ai_attachments(attachments, image_b64)

    full_prompt = prompt.strip() or "Analyze the attached reference files and generate the corresponding Blender bpy CAD script."
    if images:
        img_lines = []
        for img in images:
            p_hint = f", saved at `{img['path']}` (usable in `cad.image_relief(image_path=...)`)" if img.get("path") else ""
            img_lines.append(f"- {img['name']} ({img['mime']}{p_hint})")
        full_prompt += "\n\nAttached Reference Images (inspect visually alongside the prompt):\n" + "\n".join(img_lines)
    if doc_block:
        full_prompt += "\n\nAttached Reference Documents:\n" + doc_block

    model_note = ""

    if provider == "gemini":
        api_key = (settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError(
                "No Gemini API key configured. Open Settings in Blender Studio and enter your Gemini API key, "
                "or switch provider to Local Ollama / OpenAI-Compatible."
            )
        text, gem_note = _call_gemini_chat(api_key, model or "gemini-3-flash-preview", sys_prompt, full_prompt, images)
        model_note = gem_note + model_note
    elif provider == "openai":
        base = (settings.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        api_key = (settings.get("openai_api_key") or os.environ.get("OPENAI_API_KEY") or "").strip()
        chosen_model = (settings.get("openai_model") or model or "gpt-4o-mini").strip()
        endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        text = _call_openai_chat(endpoint, api_key, chosen_model, sys_prompt, full_prompt, images)
    else:
        chosen_model = model or "qwen2.5-coder:7b"
        img_b64s = [img["b64"] for img in images]
        num_ctx = 16384 if (doc_block or img_b64s) else 8192
        text = ""
        if img_b64s:
            vision_model = chosen_model if _is_ollama_vision_model(chosen_model) else _pick_ollama_vision_fallback(settings)
            coder_model = _pick_ollama_coder_model(settings, model)
            if vision_model and coder_model and vision_model != coder_model:
                try:
                    vis_sys = (
                        "You are a precision CAD visual analyst. Inspect the attached picture(s) and produce a concise, "
                        "accurate dimensional & geometric specification for 3D printing (silhouette, approximate mm dimensions, "
                        "exact visible text/logos, raised/recessed features, borders, holes, and whether an embossed image relief "
                        "via `cad.image_relief` or clean `cad.box`/`cad.rounded_plate`/`cad.text` geometry fits best). "
                        "Do NOT write Python code."
                    )
                    vis_spec = _post_ollama_chat(settings, vision_model, vis_sys, full_prompt, img_b64s, num_ctx=num_ctx).strip()
                    stage2_prompt = (
                        f"{full_prompt}\n\n"
                        f"Visual Inspection Specification from `{vision_model}`:\n{vis_spec}\n\n"
                        "Write the complete working Python script using `cad` (`cad.clear()`, `cad.rounded_plate()`, "
                        "`cad.box()`, `cad.cylinder()`, `cad.text()`, `cad.image_relief()`, `cad.cut()`, `cad.hole()`) "
                        "to build this part accurately."
                    )
                    text = _post_ollama_chat(settings, coder_model, sys_prompt, stage2_prompt, [], num_ctx=num_ctx)
                    model_note = f"(Two-stage vision `{vision_model}` -> `{coder_model}`.) "
                except Exception:
                    text = ""
            if not text and vision_model:
                chosen_model = vision_model
                if chosen_model != (model or ""):
                    model_note = f"(Auto-switched to vision model `{vision_model}` for attached image context.) "
        if not text:
            try:
                use_imgs = img_b64s if _is_ollama_vision_model(chosen_model) else []
                text = _post_ollama_chat(settings, chosen_model, sys_prompt, full_prompt, use_imgs, num_ctx=num_ctx)
            except Exception:
                text = _post_ollama_chat(settings, chosen_model, sys_prompt, full_prompt, [], num_ctx=num_ctx)
                if img_b64s:
                    model_note = (
                        f"(Model `{chosen_model}` rejected image inputs; images omitted.) "
                    )

    code = _extract_python_block(text)
    explanation = re.sub(r"```(?:python|py)?\s*\n.*?```", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    return {
        "explanation": (model_note + (explanation or "Generated Blender Python script.")).strip(),
        "code": code,
        "raw": text,
        "attachments_used": summary_names,
    }


# --------------------------------------------------------------------------- #
# Project Directory Scanner
# --------------------------------------------------------------------------- #
def scan_projects_dir(projects_dir: str) -> dict[str, Any]:
    root = Path(projects_dir)
    blends, models, media = [], [], []
    if not root.is_dir():
        return {"blends": blends, "models": models, "media": media, "root": str(root)}

    def _visit(folder: Path, depth: int) -> None:
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if depth < 2 and entry.name not in ("anim_frames", "__pycache__"):
                    _visit(entry, depth + 1)
                continue
            ext = entry.suffix.lower()
            try:
                st = entry.stat()
            except OSError:
                continue
            rel = str(entry.relative_to(root))
            item = {
                "name": entry.name,
                "rel": rel,
                "path": str(entry),
                "size": st.st_size,
                "size_h": human_size(st.st_size),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                "ts": st.st_mtime,
            }
            if ext == ".blend":
                blends.append(item)
            elif ext in IMPORTABLE_EXT:
                models.append(item)
            elif ext in MEDIA_EXT:
                media.append(item)

    _visit(root, 0)
    blends.sort(key=lambda x: x["ts"], reverse=True)
    models.sort(key=lambda x: x["ts"], reverse=True)
    media.sort(key=lambda x: x["ts"], reverse=True)
    return {"blends": blends, "models": models, "media": media[:80], "root": str(root)}


# --------------------------------------------------------------------------- #
# Embedded HTML/CSS/JS Frontend (Single-File WebView2/WebKit2GTK Safe)
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blender Studio</title>
<style>
  :root {
    --bg: #16181d;
    --panel: #1e2128;
    --card: #252932;
    --card-hi: #2d323e;
    --border: #323846;
    --text: #e6ebf2;
    --muted: #9aa4b2;
    --accent: #e87d0d;
    --accent-hi: #ff9424;
    --teal: #26a69a;
    --teal-hi: #34c2b4;
    --ok: #3fb950;
    --err: #f85149;
    --warn: #d29922;
    --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    --sans: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--text); font: 13px/1.4 var(--sans); overflow: hidden; }
  .app { display: flex; flex-direction: column; height: 100vh; }

  .topbar {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding: 8px 12px; background: var(--panel); border-bottom: 1px solid var(--border);
  }
  .brand { display: flex; align-items: center; gap: 7px; font-weight: 700; font-size: 14px; letter-spacing: .2px; margin-right: 6px; }
  .brand .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--err); box-shadow: 0 0 0 2px rgba(255,255,255,0.06); }
  .brand .dot.on { background: var(--ok); }
  .brand .dot.busy { background: var(--warn); }
  .pill {
    font-size: 11px; padding: 3px 8px; border-radius: 999px;
    background: var(--card); border: 1px solid var(--border); color: var(--muted);
    max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .pill.on { color: #b8f5c1; border-color: rgba(63,185,80,.45); background: rgba(63,185,80,.12); }
  .spacer { flex: 1; }

  button, select, input, textarea {
    font: inherit; color: var(--text); background: var(--card);
    border: 1px solid var(--border); border-radius: 6px; outline: none;
  }
  button {
    padding: 5px 10px; cursor: pointer; font-weight: 500; font-size: 12px;
    transition: background .12s, border-color .12s;
  }
  button:hover:not(:disabled) { background: var(--card-hi); border-color: #4a5366; }
  button:disabled { opacity: .45; cursor: default; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #121417; font-weight: 700; }
  button.primary:hover:not(:disabled) { background: var(--accent-hi); border-color: var(--accent-hi); }
  button.teal { background: var(--teal); border-color: var(--teal); color: #0c1716; font-weight: 700; }
  button.teal:hover:not(:disabled) { background: var(--teal-hi); border-color: var(--teal-hi); }
  button.sm { padding: 3px 8px; font-size: 11px; border-radius: 5px; }
  button.danger { color: #ff9a95; border-color: rgba(248,81,73,.35); }
  button.danger:hover:not(:disabled) { background: rgba(248,81,73,.14); }
  select, input[type="text"], input[type="number"], input[type="password"] { padding: 5px 8px; font-size: 12px; }

  .main {
    flex: 1; min-height: 0;
    display: grid; grid-template-columns: 315px 1fr 410px; gap: 1px; background: var(--border);
  }
  .col { background: var(--bg); display: flex; flex-direction: column; min-height: 0; min-width: 0; }
  .col-head {
    display: flex; align-items: center; gap: 6px; padding: 7px 10px;
    background: var(--panel); border-bottom: 1px solid var(--border); font-weight: 600; font-size: 12px;
  }
  .tabs { display: flex; gap: 4px; padding: 6px 8px; background: var(--panel); border-bottom: 1px solid var(--border); }
  .tab {
    flex: 1; text-align: center; padding: 5px 6px; border-radius: 5px; font-size: 11px;
    color: var(--muted); background: transparent; border: 1px solid transparent; cursor: pointer;
  }
  .tab.active { color: var(--text); background: var(--card); border-color: var(--border); font-weight: 600; }
  .scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 8px; }

  .item {
    background: var(--panel); border: 1px solid var(--border); border-radius: 7px;
    padding: 8px 9px; margin-bottom: 6px;
  }
  .item:hover { border-color: #465066; }
  .item.active { border-color: var(--accent); background: rgba(232,125,13,.08); }
  .item-top { display: flex; align-items: center; gap: 6px; justify-content: space-between; }
  .item-title { font-weight: 600; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .item-meta { font-size: 11px; color: var(--muted); margin-top: 2px; font-family: var(--mono); }
  .item-acts { display: flex; gap: 5px; margin-top: 6px; flex-wrap: wrap; }
  .sec-lbl {
    font-size: 10px; text-transform: uppercase; letter-spacing: .7px; color: var(--muted);
    margin: 8px 2px 5px; display: flex; align-items: center; justify-content: space-between;
  }

  .cam-bar {
    display: flex; align-items: center; gap: 5px; flex-wrap: wrap; padding: 7px 10px;
    background: var(--panel); border-bottom: 1px solid var(--border);
  }
  .viewport {
    flex: 1; min-height: 0; position: relative; display: flex; align-items: center; justify-content: center;
    background: radial-gradient(circle at center, #202531 0%, #121419 100%);
    overflow: hidden; padding: 12px;
  }
  .viewport img {
    max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 6px;
    box-shadow: 0 10px 30px rgba(0,0,0,.5); transition: transform .15s ease;
  }
  .vp-empty { text-align: center; color: var(--muted); max-width: 380px; }
  .vp-empty h3 { margin: 0 0 6px; color: var(--text); font-size: 15px; }
  .vp-badge {
    position: absolute; top: 10px; left: 10px; font-size: 11px; font-family: var(--mono);
    background: rgba(22,24,29,.82); border: 1px solid var(--border); border-radius: 5px; padding: 3px 8px;
  }
  .filmstrip {
    height: 92px; display: flex; gap: 6px; align-items: center; overflow-x: auto;
    padding: 7px 10px; background: var(--panel); border-top: 1px solid var(--border);
  }
  .thumb {
    flex: 0 0 auto; width: 118px; height: 74px; border-radius: 5px; border: 1px solid var(--border);
    background: var(--card); overflow: hidden; cursor: pointer; position: relative;
    display: flex; align-items: flex-end; justify-content: center;
  }
  .thumb:hover { border-color: var(--accent); }
  .thumb span {
    position: relative; z-index: 1; width: 100%; font-size: 10px; padding: 2px 4px;
    background: rgba(15,17,22,.82); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: center;
  }

  .ai-box { padding: 9px 10px; border-bottom: 1px solid var(--border); background: var(--panel); transition: background .12s, border-color .12s; }
  .ai-box.dragover { outline: 1px dashed var(--accent); outline-offset: -3px; background: rgba(232,125,13,.08); }
  .attach-bar { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; max-height: 84px; overflow-y: auto; }
  .attach-chip {
    display: inline-flex; align-items: center; gap: 5px; max-width: 100%;
    padding: 2px 6px; border-radius: 5px; background: var(--card); border: 1px solid var(--border);
    font-size: 11px; color: var(--text);
  }
  .attach-chip img { width: 18px; height: 18px; object-fit: cover; border-radius: 3px; flex-shrink: 0; }
  .attach-badge {
    font-size: 9px; font-weight: 700; text-transform: uppercase; padding: 1px 4px;
    border-radius: 3px; background: rgba(38,166,154,.22); color: #7ae2d8; flex-shrink: 0;
  }
  .attach-chip .att-name { max-width: 155px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .attach-chip .att-size { color: var(--muted); font-size: 10px; font-family: var(--mono); }
  .attach-chip .rm {
    border: none; background: transparent; color: var(--muted); padding: 0 2px;
    cursor: pointer; font-weight: 700; line-height: 1; font-size: 12px;
  }
  .attach-chip .rm:hover { color: var(--err); background: transparent; }
  .btn-spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid rgba(255,255,255,0.35);
    border-radius: 50%;
    border-top-color: #ffffff;
    animation: btnSpin 0.75s linear infinite;
    vertical-align: middle;
    margin-right: 5px;
    flex-shrink: 0;
  }
  @keyframes btnSpin {
    to { transform: rotate(360deg); }
  }
  button.primary.generating {
    background: linear-gradient(135deg, #d97008, #f59e0b) !important;
    border-color: #f59e0b !important;
    cursor: wait !important;
    box-shadow: 0 0 12px rgba(245, 158, 11, 0.45);
    opacity: 0.95;
    pointer-events: none;
  }
  .ai-progress-track {
    display: none;
    width: 100%;
    height: 3px;
    background: rgba(255,255,255,0.08);
    border-radius: 3px;
    overflow: hidden;
    margin-top: 6px;
    position: relative;
  }
  .ai-progress-track.active {
    display: block;
  }
  .ai-progress-fill {
    width: 45%;
    height: 100%;
    background: linear-gradient(90deg, #26a69a, #e87d0d, #26a69a);
    background-size: 200% 100%;
    border-radius: 3px;
    animation: aiProgressPulse 1.25s ease-in-out infinite;
  }
  @keyframes aiProgressPulse {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(250%); }
  }
  .row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  textarea {
    width: 100%; resize: vertical; padding: 8px; line-height: 1.4;
    background: #12141a; color: #e8eef7; border: 1px solid var(--border); border-radius: 6px;
  }
  .code-ed { flex: 1; min-height: 160px; font: 12px/1.45 var(--mono); tab-size: 4; }
  .console {
    height: 150px; overflow-y: auto; padding: 8px 10px; background: #0f1115;
    border-top: 1px solid var(--border); font: 11.5px/1.4 var(--mono); white-space: pre-wrap; word-break: break-word;
  }
  .console .ok { color: #8ce99a; }
  .console .err { color: #ff8787; }
  .console .info { color: #8ecae6; }

  .statusbar {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 5px 12px; background: var(--panel); border-top: 1px solid var(--border);
    font-size: 11px; color: var(--muted);
  }
  .modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,.65); display: none;
    align-items: center; justify-content: center; z-index: 50;
  }
  .modal-bg.open { display: flex; }
  .modal {
    width: 520px; max-width: 94vw; max-height: 92vh; overflow-y: auto;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px; box-shadow: 0 20px 50px rgba(0,0,0,.6);
  }
  .modal h3 { margin: 0 0 10px; font-size: 15px; }
  .field { margin-bottom: 9px; }
  .field label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
  .field input, .field select { width: 100%; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  label.chk { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; color: var(--muted); cursor: pointer; }
</style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <div class="brand">
      <span class="dot" id="srv-dot"></span>
      <span>Blender Studio</span>
    </div>
    <span class="pill" id="srv-pill">Disconnected (:9999)</span>
    <span class="pill" id="blend-pill">No .blend loaded</span>
    <div class="spacer"></div>
    <button class="teal sm" id="btn-headless" title="Start background Blender socket daemon on port 9999">Start Headless Server</button>
    <button class="sm" id="btn-gui" title="Open Blender GUI with socket server active">Launch Blender GUI</button>
    <button class="sm" id="btn-refresh">Refresh</button>
    <button class="sm" id="btn-save">Save .blend</button>
    <button class="sm danger" id="btn-stop" title="Stop port 9999 Blender server">Stop Server</button>
    <button class="sm" id="btn-settings">Settings</button>
  </div>

  <div class="main">
    <div class="col">
      <div class="tabs">
        <button class="tab active" data-ltab="scene">Scene</button>
        <button class="tab" data-ltab="projects">Projects</button>
        <button class="tab" data-ltab="library">STL / 3MF</button>
      </div>
      <div class="scroll" id="left-body"></div>
    </div>

    <div class="col">
      <div class="cam-bar">
        <span style="font-weight:600;font-size:12px;margin-right:4px">Render:</span>
        <button class="primary sm" data-cam="hero">Hero</button>
        <button class="sm" data-cam="top">Top</button>
        <button class="sm" data-cam="front">Front</button>
        <button class="sm" data-cam="iso">Isometric</button>
        <button class="sm" data-cam="exploded">Exploded</button>
        <button class="sm" data-cam="current">Scene Cam</button>
        <div class="spacer"></div>
        <button class="sm" id="btn-zoom-out">-</button>
        <button class="sm" id="btn-zoom-fit">Fit</button>
        <button class="sm" id="btn-zoom-in">+</button>
      </div>
      <div class="viewport" id="viewport">
        <div class="vp-badge" id="vp-badge" style="display:none"></div>
        <div class="vp-empty" id="vp-empty">
          <h3>Render-Inspect-Refine Viewport</h3>
          <p>Start the headless Blender server (or launch Blender GUI), pick a project or scene, and click <b>Hero</b> or <b>Exploded</b> to inspect geometry before sending STLs to your OrcaSlicer plate.</p>
        </div>
        <img id="vp-img" style="display:none" alt="Diagnostic render preview">
      </div>
      <div class="filmstrip" id="filmstrip"></div>
    </div>

    <div class="col">
      <div class="col-head">
        <span>AI CAD Assistant</span>
        <div class="spacer"></div>
        <select id="ai-model" style="max-width:215px;font-size:11px;padding:3px 6px">
          <option value="gemini::gemini-3.8-flash">Gemini 3.8 Flash (High Precision)</option>
          <option value="gemini::gemini-3-flash-preview">Gemini 3 Flash (Fast)</option>
          <option value="gemini::gemini-3.5-flash">Gemini 3.5 Flash</option>
          <option value="gemini::gemini-3.5-flash-lite">Gemini 3.5 Flash-Lite</option>
          <option value="gemini::gemini-3.1-flash-lite">Gemini 3.1 Flash-Lite</option>
          <option value="ollama::qwen2.5-coder:7b">Ollama: qwen2.5-coder:7b</option>
          <option value="ollama::lukey03/qwen3.5-9b-abliterated-vision:latest">Ollama: qwen3.5-9b-vision</option>
          <option value="openai::custom">OpenAI-Compatible (Settings)</option>
        </select>
      </div>
      <div class="ai-box" id="ai-box">
        <textarea id="ai-prompt" rows="3" placeholder="Describe a part or edit, or drop / paste reference images, docs &amp; CAD (.step, .3mf, .stl, .md, .pdf, .py)..."></textarea>
        <input type="file" id="ai-file-input" multiple accept="image/*,.step,.stp,.3mf,.stl,.md,.txt,.json,.csv,.py,.scad,.yaml,.yml,.xml,.svg,.html,.pdf,.log,.ini,.toml" style="display:none">
        <div class="attach-bar" id="ai-attachments" style="display:none"></div>
        <div class="ai-progress-track" id="ai-progress-track"><div class="ai-progress-fill"></div></div>
        <div class="row" style="margin-top:6px">
          <button class="sm" id="btn-ai-attach" title="Attach pictures, STEP/3MF/STL CAD models, or documents (.md, .py, .scad, .pdf) — or drag &amp; drop / paste into prompt">+ Image / CAD / Doc</button>
          <button class="sm danger" id="btn-ai-clear-att" style="display:none" title="Remove all attached reference files">Clear</button>
          <label class="chk"><input type="checkbox" id="chk-auto-run" checked> Auto-run</label>
          <label class="chk" title="Also include the current viewport diagnostic render"><input type="checkbox" id="chk-attach-img"> +Viewport</label>
          <div class="spacer"></div>
          <button class="primary sm" id="btn-ai-go" title="Generate Blender Python CAD script">
            <span class="btn-spinner" id="ai-spinner" style="display:none"></span>
            <span id="ai-btn-text">Generate bpy</span>
          </button>
        </div>
      </div>

      <div class="col-head">
        <span>bpy Script &amp; Macros</span>
        <div class="spacer"></div>
        <button class="sm" data-macro="studio_rig" title="Add 3-point lighting & RenderCam">Rig</button>
        <button class="sm" data-macro="magnet_mockup" title="Add 6x2mm magnet mockup">+Magnet</button>
        <button class="sm" data-macro="m3_boss" title="Add M3 screw & nut mockup">+M3</button>
        <button class="sm" data-macro="sg90_mockup" title="Add SG90 servo mockup">+SG90</button>
        <button class="sm" data-macro="print_audit" title="Check printable meshes for non-manifold edges & scale">Audit</button>
      </div>
      <div style="flex:1;min-height:0;display:flex;flex-direction:column;padding:8px;gap:6px">
        <textarea id="bpy-code" class="code-ed" spellcheck="false" placeholder="# Write Blender Python (bpy / bmesh / mathutils) or load a macro / AI script..."></textarea>
        <div class="row">
          <button class="teal sm" id="btn-run-bpy">Run in Blender (Ctrl+Enter)</button>
          <button class="sm" id="btn-clear-log">Clear Log</button>
          <div class="spacer"></div>
          <button class="primary sm" id="btn-plate-all">Export &amp; Plate All</button>
        </div>
      </div>
      <div class="console" id="console"></div>
    </div>
  </div>

  <div class="statusbar">
    <span id="status-msg">Ready.</span>
    <span id="projects-root"></span>
  </div>
</div>

<div class="modal-bg" id="modal-settings">
  <div class="modal">
    <h3>Blender Studio Settings</h3>
    <div class="field"><label>Blender Executable</label><input type="text" id="cfg-blender"></div>
    <div class="field"><label>3D Designs Folder</label><input type="text" id="cfg-projects"></div>
    <div class="field"><label>Server Template .blend (optional)</label><input type="text" id="cfg-template"></div>
    <div class="grid2">
      <div class="field"><label>Server Host (Local or Tailscale IP)</label><input type="text" id="cfg-host"></div>
      <div class="field"><label>Server Port</label><input type="number" id="cfg-port"></div>
    </div>
    <div class="grid2">
      <div class="field"><label>Ollama Primary URL</label><input type="text" id="cfg-ollama"></div>
      <div class="field"><label>Ollama Fallback URL</label><input type="text" id="cfg-ollama2"></div>
    </div>
    <div class="field"><label>Gemini API Key (optional)</label><input type="password" id="cfg-gemkey" placeholder="AIza... or AQ..."></div>
    <div class="grid2">
      <div class="field"><label>OpenAI-Compatible Base URL</label><input type="text" id="cfg-oaurl" placeholder="https://api.openai.com/v1"></div>
      <div class="field"><label>OpenAI-Compatible Model</label><input type="text" id="cfg-oamodel" placeholder="gpt-4o-mini"></div>
    </div>
    <div class="field"><label>OpenAI-Compatible API Key (optional for local LM Studio)</label><input type="password" id="cfg-oakey"></div>
    <div class="grid2">
      <div class="field"><label>Render Width x Height</label>
        <div class="row"><input type="number" id="cfg-rx" style="width:90px"><span>x</span><input type="number" id="cfg-ry" style="width:90px"></div>
      </div>
      <div class="field"><label>EEVEE Samples</label><input type="number" id="cfg-samples"></div>
    </div>
    <div class="row" style="margin:6px 0 12px">
      <label class="chk"><input type="checkbox" id="cfg-single"> Load STLs into current OrcaSlicer window</label>
      <label class="chk"><input type="checkbox" id="cfg-autoplate"> Send to plate right after STL export</label>
    </div>
    <div class="field" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
      <label>Blender Bootstrap Script (Self-Contained)</label>
      <div style="font-size:11px;color:var(--muted);margin-bottom:8px">
        Connecting an existing Blender session or remote host? Copy the embedded server bootstrap script or export it to run inside Blender's Scripting workspace.
      </div>
      <div class="row" style="gap:8px">
        <button class="sm" id="btn-copy-bootstrap" type="button">📋 Copy Bootstrap Script</button>
        <button class="sm" id="btn-export-bootstrap" type="button">💾 Export server_bootstrap.py</button>
      </div>
    </div>
    <div class="row">
      <div class="spacer"></div>
      <button class="sm" id="btn-cfg-close">Cancel</button>
      <button class="primary sm" id="btn-cfg-save">Save Settings</button>
    </div>
  </div>
</div>

<div class="modal-bg" id="modal-new">
  <div class="modal">
    <h3>New Blender CAD Project</h3>
    <p style="margin:0 0 10px;font-size:12px;color:var(--muted)">Creates a millimeter-calibrated Blender project (1 BU = 1 mm) with studio lights and camera ready for procedural CAD.</p>
    <div class="field"><label>Project Name (without .blend)</label><input type="text" id="new-name" placeholder="e.g. Sensor_Mount_v1"></div>
    <div class="row" style="margin-top:12px">
      <div class="spacer"></div>
      <button class="sm" id="btn-new-cancel">Cancel</button>
      <button class="primary sm" id="btn-new-create">Create &amp; Open</button>
    </div>
  </div>
</div>

<script>
'use strict';
const $ = id => document.getElementById(id);
const post = msg => { if (window.orca && window.orca.postMessage) window.orca.postMessage(msg); };
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const S = {
  connected: false,
  scene: null,
  projects: { blends: [], models: [], media: [], root: '' },
  ltab: 'scene',
  showMockups: false,
  zoom: 1.0,
  lastRenderB64: '',
  aiAttachments: [],
  settings: {},
};

function log(text, cls) {
  if (!text) return;
  const el = $('console');
  const div = document.createElement('div');
  if (cls) div.className = cls;
  div.textContent = text;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function setStatus(text, busy) {
  $('status-msg').textContent = text;
  if (busy) {
    $('srv-dot').className = 'dot busy';
  } else {
    $('srv-dot').className = S.connected ? 'dot on' : 'dot';
  }
}

function renderTopBar() {
  const dot = $('srv-dot');
  const pill = $('srv-pill');
  const bpill = $('blend-pill');
  const port = S.settings.server_port || 9999;
  if (S.connected && S.scene) {
    dot.className = 'dot on';
    const mode = S.scene.background ? 'Headless' : 'GUI';
    pill.textContent = `Connected :${port} (${mode})`;
    pill.className = 'pill on';
    bpill.textContent = S.scene.filename || '(Unsaved)';
    bpill.title = S.scene.filepath || '';
  } else {
    dot.className = 'dot';
    pill.textContent = `Disconnected (:${port})`;
    pill.className = 'pill';
    bpill.textContent = 'No active Blender server';
  }
}

function renderLeft() {
  const body = $('left-body');
  if (S.ltab === 'scene') {
    if (!S.connected || !S.scene) {
      body.innerHTML = `<div class="item"><div class="item-title">Blender Server Offline</div>
        <div class="item-meta">Click <b>Start Headless Server</b> or <b>Launch Blender GUI</b> above, or open any .blend from the <b>Projects</b> tab.</div></div>`;
      return;
    }
    const pr = S.scene.printable || [];
    const mk = S.scene.mockups || [];
    let html = `<div class="sec-lbl"><span>Printable Meshes (${pr.length})</span></div>`;
    if (!pr.length) {
      html += `<div class="item"><div class="item-meta">No printable meshes in current scene. Use AI CAD or a macro to generate parts.</div></div>`;
    }
    for (const o of pr) {
      const dims = o.dims ? `${o.dims[0]} × ${o.dims[1]} × ${o.dims[2]} mm` : '';
      const detail = o.type === 'MESH' ? `${o.polys} faces` : esc(o.type || 'OBJ');
      html += `<div class="item">
        <div class="item-top"><span class="item-title" title="${esc(o.name)}">${esc(o.name)}</span></div>
        <div class="item-meta">${esc(dims)} &middot; ${detail}</div>
        <div class="item-acts">
          <button class="sm" data-export-one="${esc(o.name)}" data-plate="0">Export STL</button>
          <button class="teal sm" data-export-one="${esc(o.name)}" data-plate="1">Send to Plate</button>
        </div>
      </div>`;
    }
    html += `<div class="sec-lbl"><span>Hardware Mockups (${mk.length})</span>
      <button class="sm" id="btn-tog-mock">${S.showMockups ? 'Hide' : 'Show'}</button></div>`;
    if (S.showMockups) {
      for (const m of mk) {
        const dims = m.dims ? `${m.dims[0]} × ${m.dims[1]} × ${m.dims[2]} mm` : '';
        html += `<div class="item"><div class="item-top"><span class="item-title">${esc(m.name)}</span></div>
          <div class="item-meta">${esc(dims)}</div></div>`;
      }
    }
    body.innerHTML = html;
    const tog = $('btn-tog-mock');
    if (tog) tog.onclick = () => { S.showMockups = !S.showMockups; renderLeft(); };
    body.querySelectorAll('[data-export-one]').forEach(b => {
      b.onclick = () => {
        const name = b.dataset.exportOne;
        const plate = b.dataset.plate === '1';
        setStatus(`Exporting ${name}.stl...`, true);
        post({ command: 'export_stl', object_names: [name], send_to_plate: plate });
      };
    });
  } else if (S.ltab === 'projects') {
    const blends = S.projects.blends || [];
    let html = `<div class="row" style="margin-bottom:8px">
      <button class="primary sm" id="btn-new-proj" style="width:100%">+ New Project (.blend)</button>
    </div><div class="sec-lbl"><span>.blend Projects (${blends.length})</span></div>`;
    const activeFp = (S.scene && S.scene.filepath) || '';
    for (const f of blends) {
      const isAct = activeFp && activeFp === f.path;
      html += `<div class="item ${isAct ? 'active' : ''}">
        <div class="item-top"><span class="item-title" title="${esc(f.rel)}">${esc(f.rel)}</span></div>
        <div class="item-meta">${esc(f.size_h)} &middot; ${esc(f.mtime)}</div>
        <div class="item-acts">
          <button class="teal sm" data-open-blend="${esc(f.path)}">${isAct ? 'Reload' : 'Open in Server'}</button>
          <button class="sm" data-gui-blend="${esc(f.path)}">Open GUI</button>
        </div>
      </div>`;
    }
    body.innerHTML = html;
    $('btn-new-proj').onclick = () => $('modal-new').classList.add('open');
    body.querySelectorAll('[data-open-blend]').forEach(b => {
      b.onclick = () => {
        setStatus(`Opening ${b.dataset.openBlend}...`, true);
        post({ command: 'open_blend', filepath: b.dataset.openBlend });
      };
    });
    body.querySelectorAll('[data-gui-blend]').forEach(b => {
      b.onclick = () => {
        setStatus('Launching Blender GUI...', true);
        post({ command: 'start_blender_gui', blend_path: b.dataset.guiBlend });
      };
    });
  } else {
    const models = S.projects.models || [];
    let html = `<div class="sec-lbl"><span>Exported STL / 3MF (${models.length})</span></div>`;
    for (const m of models) {
      html += `<div class="item">
        <div class="item-top"><span class="item-title" title="${esc(m.rel)}">${esc(m.rel)}</span></div>
        <div class="item-meta">${esc(m.size_h)} &middot; ${esc(m.mtime)}</div>
        <div class="item-acts">
          <button class="teal sm" data-plate-file="${esc(m.path)}">Send to Plate</button>
        </div>
      </div>`;
    }
    body.innerHTML = html;
    body.querySelectorAll('[data-plate-file]').forEach(b => {
      b.onclick = () => {
        setStatus(`Sending ${b.dataset.plateFile} to plate...`, true);
        post({ command: 'send_to_plate', path: b.dataset.plateFile });
      };
    });
  }
}

function renderFilmstrip() {
  const fs = $('filmstrip');
  const media = S.projects.media || [];
  if (!media.length) {
    fs.innerHTML = `<span style="color:var(--muted);font-size:11px">No saved project renders yet. Click a camera view above to render.</span>`;
    return;
  }
  fs.innerHTML = media.slice(0, 24).map(m =>
    `<div class="thumb" data-media="${esc(m.path)}" title="${esc(m.rel)}">
      <span>${esc(m.name)}</span>
    </div>`
  ).join('');
  fs.querySelectorAll('[data-media]').forEach(t => {
    t.onclick = () => {
      setStatus(`Loading ${t.dataset.media}...`, true);
      post({ command: 'load_image', path: t.dataset.media });
    };
  });
}

function showViewportImage(dataUrl, label, rawB64) {
  $('vp-empty').style.display = 'none';
  const img = $('vp-img');
  img.src = dataUrl;
  img.style.display = 'block';
  S.zoom = 1.0;
  img.style.transform = 'scale(1)';
  $('vp-badge').textContent = label || 'Preview';
  $('vp-badge').style.display = 'block';
  if (rawB64) S.lastRenderB64 = rawB64;
}

document.querySelectorAll('[data-ltab]').forEach(btn => {
  btn.onclick = () => {
    S.ltab = btn.dataset.ltab;
    document.querySelectorAll('[data-ltab]').forEach(b => b.classList.toggle('active', b === btn));
    renderLeft();
  };
});

$('btn-headless').onclick = () => {
  const fp = (S.scene && S.scene.filepath) || '';
  setStatus('Starting headless Blender server on :9999...', true);
  post({ command: 'start_blender_headless', blend_path: fp });
};
$('btn-gui').onclick = () => {
  const fp = (S.scene && S.scene.filepath) || '';
  setStatus('Launching Blender GUI on :9999...', true);
  post({ command: 'start_blender_gui', blend_path: fp });
};
$('btn-refresh').onclick = () => {
  setStatus('Refreshing Blender server & project list...', true);
  post({ command: 'refresh_all' });
};
$('btn-save').onclick = () => {
  setStatus('Saving .blend...', true);
  post({ command: 'save_blend' });
};
$('btn-stop').onclick = () => {
  setStatus('Stopping Blender server...', true);
  post({ command: 'stop_blender_server' });
};

document.querySelectorAll('[data-cam]').forEach(btn => {
  btn.onclick = () => {
    const preset = btn.dataset.cam;
    setStatus(`Rendering ${preset} view...`, true);
    post({ command: 'render_view', preset });
  };
});
$('btn-zoom-in').onclick = () => { S.zoom = Math.min(4.0, S.zoom + 0.25); $('vp-img').style.transform = `scale(${S.zoom})`; };
$('btn-zoom-out').onclick = () => { S.zoom = Math.max(0.4, S.zoom - 0.25); $('vp-img').style.transform = `scale(${S.zoom})`; };
$('btn-zoom-fit').onclick = () => { S.zoom = 1.0; $('vp-img').style.transform = 'scale(1)'; };

document.querySelectorAll('[data-macro]').forEach(btn => {
  btn.onclick = () => post({ command: 'get_macro', name: btn.dataset.macro });
});
function runBpy(autoRender) {
  const code = $('bpy-code').value;
  if (!code.trim()) return;
  setStatus('Executing bpy script...', true);
  post({ command: 'run_bpy', code, auto_render: !!autoRender });
}
$('btn-run-bpy').onclick = () => runBpy(false);
$('bpy-code').addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); runBpy(false); }
});
$('btn-clear-log').onclick = () => { $('console').innerHTML = ''; };
$('btn-plate-all').onclick = () => {
  setStatus('Exporting all printable meshes & sending to plate...', true);
  post({ command: 'export_stl', object_names: ['__all_printable__'], send_to_plate: true });
};

function humanBytes(n) {
  let v = Number(n) || 0;
  for (const u of ['B', 'KB', 'MB']) {
    if (v < 1024 || u === 'MB') return u === 'B' ? `${v} B` : `${v.toFixed(1)} ${u}`;
    v /= 1024;
  }
  return `${v.toFixed(1)} MB`;
}

function renderAiAttachments() {
  const bar = $('ai-attachments');
  const clearBtn = $('btn-ai-clear-att');
  if (!S.aiAttachments.length) {
    bar.style.display = 'none';
    bar.innerHTML = '';
    clearBtn.style.display = 'none';
    return;
  }
  bar.style.display = 'flex';
  clearBtn.style.display = 'inline-block';
  bar.innerHTML = S.aiAttachments.map((att, idx) => {
    const ext = (att.name.split('.').pop() || att.kind || 'DOC').toUpperCase().slice(0, 4);
    const thumb = att.kind === 'image' && att.previewUrl
      ? `<img src="${esc(att.previewUrl)}" alt="">`
      : `<span class="attach-badge">${esc(ext)}</span>`;
    return `<span class="attach-chip" title="${esc(att.name)} (${esc(att.size_h)})">
      ${thumb}
      <span class="att-name">${esc(att.name)}</span>
      <span class="att-size">${esc(att.size_h)}</span>
      <button type="button" class="rm" data-rm-att="${idx}" title="Remove">&times;</button>
    </span>`;
  }).join('');
  bar.querySelectorAll('[data-rm-att]').forEach(btn => {
    btn.onclick = () => {
      const i = parseInt(btn.dataset.rmAtt, 10);
      if (!isNaN(i)) {
        S.aiAttachments.splice(i, 1);
        renderAiAttachments();
      }
    };
  });
}

function addAiFiles(fileList) {
  if (!fileList || !fileList.length) return;
  const maxBytes = 15 * 1024 * 1024;
  const docExts = new Set(['md','txt','json','csv','py','scad','yaml','yml','xml','svg','html','htm','log','ini','toml','cfg','js','ts','css','sql']);
  const cadExts = new Set(['step','stp','3mf','stl']);
  const imgExts = new Set(['png','jpg','jpeg','webp','gif','bmp']);

  for (const file of fileList) {
    if (!file) continue;
    if (file.size > maxBytes) {
      log(`[Attach] Skipped ${file.name} (${humanBytes(file.size)} exceeds 15 MB limit).`, 'err');
      continue;
    }
    const name = file.name || `pasted_${Date.now()}.png`;
    const ext = (name.split('.').pop() || '').toLowerCase();
    const mime = (file.type || '').toLowerCase();
    const isSvg = ext === 'svg' || mime === 'image/svg+xml';
    const isPdf = ext === 'pdf' || mime === 'application/pdf';
    const isCad = cadExts.has(ext);
    const isImage = !isSvg && ((mime.startsWith('image/') && mime !== 'image/svg+xml') || imgExts.has(ext));

    if (isImage) {
      const reader = new FileReader();
      reader.onload = ev => {
        const dataUrl = String(ev.target && ev.target.result || '');
        if (!dataUrl) return;
        const img = new Image();
        img.onload = () => {
          const maxDim = 1536;
          let finalUrl = dataUrl;
          let finalMime = mime || (ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : `image/${ext || 'png'}`);
          if (img.width > maxDim || img.height > maxDim) {
            const scale = Math.min(maxDim / img.width, maxDim / img.height);
            const canvas = document.createElement('canvas');
            canvas.width = Math.max(1, Math.round(img.width * scale));
            canvas.height = Math.max(1, Math.round(img.height * scale));
            const ctx = canvas.getContext('2d');
            if (ctx) {
              ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
              const outMime = (finalMime === 'image/jpeg') ? 'image/jpeg' : 'image/png';
              finalUrl = canvas.toDataURL(outMime, 0.9);
              finalMime = outMime;
            }
          }
          const b64 = finalUrl.includes(',') ? finalUrl.split(',', 2)[1] : finalUrl;
          S.aiAttachments.push({
            name, kind: 'image', mime: finalMime, size: file.size, size_h: humanBytes(file.size),
            b64, text: '', previewUrl: finalUrl,
          });
          renderAiAttachments();
        };
        img.onerror = () => {
          const b64 = dataUrl.includes(',') ? dataUrl.split(',', 2)[1] : dataUrl;
          S.aiAttachments.push({
            name, kind: 'image', mime: mime || 'image/png', size: file.size, size_h: humanBytes(file.size),
            b64, text: '', previewUrl: dataUrl,
          });
          renderAiAttachments();
        };
        img.src = dataUrl;
      };
      reader.readAsDataURL(file);
    } else if (isPdf || isCad) {
      const reader = new FileReader();
      reader.onload = ev => {
        const dataUrl = String(ev.target && ev.target.result || '');
        const b64 = dataUrl.includes(',') ? dataUrl.split(',', 2)[1] : dataUrl;
        if (!b64) return;
        S.aiAttachments.push({
          name, kind: isPdf ? 'pdf' : 'cad', mime: isPdf ? 'application/pdf' : (mime || 'application/octet-stream'),
          size: file.size, size_h: humanBytes(file.size), b64, text: '', previewUrl: '',
        });
        renderAiAttachments();
      };
      reader.readAsDataURL(file);
    } else if (mime.startsWith('text/') || mime.includes('json') || mime.includes('xml') || docExts.has(ext) || !ext) {
      const reader = new FileReader();
      reader.onload = ev => {
        const text = String(ev.target && ev.target.result || '');
        S.aiAttachments.push({
          name, kind: 'doc', mime: mime || 'text/plain', size: file.size, size_h: humanBytes(file.size),
          b64: '', text, previewUrl: '',
        });
        renderAiAttachments();
      };
      reader.readAsText(file);
    } else {
      log(`[Attach] Unsupported file type: ${name} (${mime || ext || 'binary'}). Try an image, PDF, CAD (.step/.3mf/.stl), or text/code document.`, 'err');
    }
  }
}

$('btn-ai-attach').onclick = () => $('ai-file-input').click();
$('ai-file-input').onchange = e => {
  addAiFiles(e.target.files);
  e.target.value = '';
};
$('btn-ai-clear-att').onclick = () => {
  S.aiAttachments = [];
  renderAiAttachments();
};

const aiBox = $('ai-box');
['dragenter', 'dragover'].forEach(evtName => {
  aiBox.addEventListener(evtName, e => {
    e.preventDefault();
    e.stopPropagation();
    aiBox.classList.add('dragover');
  });
});
['dragleave', 'drop'].forEach(evtName => {
  aiBox.addEventListener(evtName, e => {
    e.preventDefault();
    e.stopPropagation();
    if (evtName === 'drop' && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      addAiFiles(e.dataTransfer.files);
    }
    aiBox.classList.remove('dragover');
  });
});

$('ai-prompt').addEventListener('paste', e => {
  const cd = e.clipboardData;
  if (!cd) return;
  const files = [];
  if (cd.files && cd.files.length) {
    for (const f of cd.files) files.push(f);
  } else if (cd.items && cd.items.length) {
    for (const item of cd.items) {
      if (item.kind === 'file') {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
  }
  if (files.length) {
    e.preventDefault();
    addAiFiles(files);
  }
});

let aiProgressTimer = null;
let aiProgressSecs = 0;

function setAiGenerating(active, modelName) {
  const btn = $('btn-ai-go');
  const sp = $('ai-spinner');
  const txt = $('ai-btn-text');
  const trk = $('ai-progress-track');
  if (aiProgressTimer) {
    clearInterval(aiProgressTimer);
    aiProgressTimer = null;
  }
  if (!btn) return;
  if (active) {
    btn.classList.add('generating');
    btn.disabled = true;
    if (sp) sp.style.display = 'inline-block';
    if (trk) trk.classList.add('active');
    aiProgressSecs = 0;
    if (txt) txt.textContent = 'Generating (0s)...';
    aiProgressTimer = setInterval(() => {
      aiProgressSecs++;
      if (txt) txt.textContent = `Generating (${aiProgressSecs}s)...`;
    }, 1000);
  } else {
    btn.classList.remove('generating');
    btn.disabled = false;
    if (sp) sp.style.display = 'none';
    if (trk) trk.classList.remove('active');
    if (txt) txt.textContent = 'Generate bpy';
  }
}

$('btn-ai-go').onclick = () => {
  const rawPrompt = $('ai-prompt').value.trim();
  if (!rawPrompt && !S.aiAttachments.length) return;
  const prompt = rawPrompt || 'Analyze the attached reference files and generate the corresponding Blender bpy CAD script.';
  const [provider, model] = ($('ai-model').value || 'gemini::gemini-3.8-flash').split('::');
  const attCount = S.aiAttachments.length + ($('chk-attach-img').checked && S.lastRenderB64 ? 1 : 0);
  const attNote = attCount ? ` (+${attCount} attachment${attCount > 1 ? 's' : ''})` : '';
  const displayModel = provider === 'openai' ? (S.settings.openai_model || 'custom model') : model;
  setStatus(`Generating bpy via ${displayModel}${attNote}...`, true);
  setAiGenerating(true, displayModel);
  post({
    command: 'ai_prompt',
    prompt,
    provider,
    model,
    auto_run: $('chk-auto-run').checked,
    image_b64: $('chk-attach-img').checked ? S.lastRenderB64 : '',
    attachments: S.aiAttachments.map(a => ({
      name: a.name,
      kind: a.kind,
      mime: a.mime,
      b64: a.b64 || '',
      text: a.text || '',
    })),
  });
};

$('btn-settings').onclick = () => {
  const c = S.settings || {};
  $('cfg-blender').value = c.blender_bin || '';
  $('cfg-projects').value = c.projects_dir || '';
  $('cfg-template').value = c.template_blend || '';
  $('cfg-host').value = c.server_host || '127.0.0.1';
  $('cfg-port').value = c.server_port || 9999;
  $('cfg-ollama').value = c.ollama_url || '';
  $('cfg-ollama2').value = c.ollama_fallback_url || '';
  $('cfg-gemkey').value = c.gemini_api_key || '';
  $('cfg-oaurl').value = c.openai_base_url || 'https://api.openai.com/v1';
  $('cfg-oamodel').value = c.openai_model || 'gpt-4o-mini';
  $('cfg-oakey').value = c.openai_api_key || '';
  $('cfg-rx').value = c.render_res_x || 1280;
  $('cfg-ry').value = c.render_res_y || 720;
  $('cfg-samples').value = c.render_samples || 16;
  $('cfg-single').checked = c.single_instance_handoff !== false;
  $('cfg-autoplate').checked = c.auto_plate_after_export !== false;
  $('modal-settings').classList.add('open');
};
$('btn-cfg-close').onclick = () => $('modal-settings').classList.remove('open');
$('btn-cfg-save').onclick = () => {
  post({
    command: 'save_settings',
    settings: {
      blender_bin: $('cfg-blender').value.trim(),
      projects_dir: $('cfg-projects').value.trim(),
      template_blend: $('cfg-template').value.trim(),
      server_host: $('cfg-host').value.trim() || '127.0.0.1',
      server_port: parseInt($('cfg-port').value, 10) || 9999,
      ollama_url: $('cfg-ollama').value.trim(),
      ollama_fallback_url: $('cfg-ollama2').value.trim(),
      gemini_api_key: $('cfg-gemkey').value.trim(),
      openai_base_url: $('cfg-oaurl').value.trim(),
      openai_model: $('cfg-oamodel').value.trim(),
      openai_api_key: $('cfg-oakey').value.trim(),
      render_res_x: parseInt($('cfg-rx').value, 10) || 1280,
      render_res_y: parseInt($('cfg-ry').value, 10) || 720,
      render_samples: parseInt($('cfg-samples').value, 10) || 16,
      single_instance_handoff: $('cfg-single').checked,
      auto_plate_after_export: $('cfg-autoplate').checked,
    },
  });
  $('modal-settings').classList.remove('open');
};
$('btn-new-cancel').onclick = () => $('modal-new').classList.remove('open');
$('btn-new-create').onclick = () => {
  const name = $('new-name').value.trim();
  if (!name) return;
  $('modal-new').classList.remove('open');
  setStatus(`Creating ${name}.blend...`, true);
  post({ command: 'new_project', name });
};

const _fallbackCopy = text => {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    setStatus('Bootstrap script copied to clipboard! Paste into Blender Scripting tab.', false);
    log('Copied server_bootstrap.py to clipboard for manual Blender connection.', 'ok');
  } catch (_) {
    setStatus('Failed to copy to clipboard automatically.', true);
  }
  document.body.removeChild(ta);
};

$('btn-copy-bootstrap').onclick = () => {
  post({ command: 'get_bootstrap_source' });
};
$('btn-export-bootstrap').onclick = () => {
  post({ command: 'export_bootstrap' });
};

if (window.orca && window.orca.onMessage) {
  window.orca.onMessage(msg => {
    if (!msg) return;
    switch (msg.command) {
      case 'bootstrap_source':
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(msg.code || '').then(() => {
            setStatus('Bootstrap script copied to clipboard! Paste into Blender Scripting tab.', false);
            log('Copied server_bootstrap.py to clipboard for manual Blender connection.', 'ok');
          }).catch(() => {
            _fallbackCopy(msg.code || '');
          });
        } else {
          _fallbackCopy(msg.code || '');
        }
        break;
      case 'init_state':
        S.settings = msg.settings || {};
        $('projects-root').textContent = (msg.projects && msg.projects.root) || '';
        if (S.settings.ai_provider) {
          const val = S.settings.ai_provider === 'openai'
            ? 'openai::custom'
            : `${S.settings.ai_provider}::${S.settings.ai_model || 'qwen2.5-coder:7b'}`;
          if ([...$('ai-model').options].some(o => o.value === val)) $('ai-model').value = val;
        }
        if (msg.projects) { S.projects = msg.projects; renderFilmstrip(); }
        S.connected = !!msg.connected;
        S.scene = msg.scene || null;
        renderTopBar();
        renderLeft();
        setStatus(msg.status_text || (S.connected ? 'Connected to Blender.' : 'Ready (Blender server offline).'), false);
        break;
      case 'scene_state':
        S.connected = !!msg.connected;
        S.scene = msg.scene || null;
        renderTopBar();
        renderLeft();
        setStatus(msg.status_text || (S.connected ? 'Scene updated.' : 'Blender server disconnected.'), false);
        break;
      case 'projects':
        S.projects = msg.projects || S.projects;
        renderFilmstrip();
        renderLeft();
        break;
      case 'macro':
        $('bpy-code').value = msg.code || '';
        log(`Loaded macro: ${msg.name}`, 'info');
        break;
      case 'bpy_result':
        if (msg.stdout) log(msg.stdout, 'ok');
        if (msg.stderr) log(msg.stderr, msg.ok ? 'info' : 'err');
        setStatus(msg.ok ? 'bpy script finished.' : 'bpy script failed.', false);
        if (msg.scene) { S.connected = true; S.scene = msg.scene; renderTopBar(); renderLeft(); }
        break;
      case 'ai_result':
        setAiGenerating(false);
        if (msg.ok) {
          if (msg.attachments_used && msg.attachments_used.length) {
            log('[AI Context] ' + msg.attachments_used.join(', '), 'info');
          }
          if (msg.explanation) log('[AI] ' + msg.explanation, 'info');
          if (msg.code) $('bpy-code').value = msg.code;
          if (msg.auto_run && msg.code) {
            runBpy(true);
          } else {
            setStatus('AI generated bpy script.', false);
          }
        } else {
          log('[AI Error] ' + msg.error, 'err');
          setStatus('AI request failed.', false);
        }
        break;
      case 'render_result':
        if (msg.ok) {
          showViewportImage('data:image/png;base64,' + msg.b64, `${msg.preset.toUpperCase()} (${msg.width}x${msg.height})`, msg.b64);
          setStatus(`Rendered ${msg.preset} view.`, false);
        } else {
          log('[Render Error] ' + msg.error, 'err');
          setStatus('Render failed.', false);
        }
        break;
      case 'image_loaded':
        if (msg.ok) {
          showViewportImage(msg.data_url, msg.label, msg.raw_b64 || '');
          setStatus(`Viewing ${msg.label}`, false);
        } else {
          log('[Media Error] ' + msg.error, 'err');
          setStatus('Could not load image.', false);
        }
        break;
      case 'export_result':
        if (msg.ok) {
          log(`[Export] ${msg.path} (${msg.size_h})`, 'ok');
          setStatus(`Exported ${msg.filename}`, false);
          if (msg.open_after && msg.path) {
            post({ command: 'send_to_plate', path: msg.path });
          }
        } else {
          log('[Export Error] ' + msg.error, 'err');
          setStatus('Export failed.', false);
        }
        break;
      case 'info':
        if (msg.log) log(msg.log, msg.err ? 'err' : 'info');
        setStatus(msg.text || 'Ready.', false);
        break;
      case 'settings_saved':
        S.settings = msg.settings || S.settings;
        setStatus(msg.ok ? 'Settings saved.' : 'Could not save settings.', false);
        break;
    }
  });
}

post({ command: 'init' });
</script>
</body>
</html>
"""

CONFIG_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { background:#181a20; color:#e6ebf2; font:13px/1.4 system-ui,sans-serif; padding:14px; margin:0; }
  label { display:block; font-size:11px; color:#9aa4b2; margin:8px 0 3px; }
  input { width:100%; box-sizing:border-box; padding:6px 8px; background:#232730; color:#e6ebf2; border:1px solid #343b4a; border-radius:6px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .row { display:flex; align-items:center; gap:12px; margin-top:12px; flex-wrap:wrap; }
  button { padding:7px 14px; background:#e87d0d; color:#111; border:none; border-radius:6px; font-weight:700; cursor:pointer; }
</style></head><body>
  <div class="grid">
    <div>
      <label>Blender Binary</label><input id="blender_bin">
      <label>3D Designs Folder</label><input id="projects_dir">
      <label>Server Template .blend (optional)</label><input id="template_blend">
      <label>Gemini API Key</label><input id="gemini_api_key" type="password">
    </div>
    <div>
      <label>Server Host</label><input id="server_host">
      <label>Server Port</label><input id="server_port" type="number">
      <label>Ollama Primary URL</label><input id="ollama_url">
      <label>OpenAI-Compatible Base URL</label><input id="openai_base_url">
    </div>
  </div>
  <div class="grid">
    <div><label>OpenAI-Compatible Model</label><input id="openai_model"></div>
    <div><label>OpenAI-Compatible API Key</label><input id="openai_api_key" type="password"></div>
  </div>
  <div class="row">
    <label style="display:inline-flex;align-items:center;gap:6px;margin:0"><input type="checkbox" id="single_instance_handoff" style="width:auto"> Load into current window</label>
    <label style="display:inline-flex;align-items:center;gap:6px;margin:0"><input type="checkbox" id="auto_plate_after_export" style="width:auto"> Auto-plate after export</label>
    <button id="save">Save</button>
    <span id="msg" style="font-size:12px;color:#9aa4b2"></span>
  </div>
<script>
'use strict';
const $ = id => document.getElementById(id);
let cur = {};
if (window.orca && window.orca.onConfig) {
  window.orca.onConfig(cfg => {
    cur = cfg || {};
    ['blender_bin','projects_dir','template_blend','server_host','server_port',
     'ollama_url','gemini_api_key','openai_base_url','openai_model','openai_api_key'].forEach(k => {
      $(k).value = cur[k] == null ? '' : cur[k];
    });
    $('single_instance_handoff').checked = cur.single_instance_handoff !== false;
    $('auto_plate_after_export').checked = cur.auto_plate_after_export !== false;
  });
}
$('save').onclick = () => {
  const out = Object.assign({}, cur, {
    blender_bin: $('blender_bin').value.trim(),
    projects_dir: $('projects_dir').value.trim(),
    template_blend: $('template_blend').value.trim(),
    server_host: $('server_host').value.trim() || '127.0.0.1',
    server_port: parseInt($('server_port').value, 10) || 9999,
    ollama_url: $('ollama_url').value.trim(),
    gemini_api_key: $('gemini_api_key').value.trim(),
    openai_base_url: $('openai_base_url').value.trim(),
    openai_model: $('openai_model').value.trim(),
    openai_api_key: $('openai_api_key').value.trim(),
    single_instance_handoff: $('single_instance_handoff').checked,
    auto_plate_after_export: $('auto_plate_after_export').checked,
  });
  if (window.orca && window.orca.saveConfig) window.orca.saveConfig(out);
  $('msg').textContent = 'Saved';
};
</script>
</body></html>
"""


# --------------------------------------------------------------------------- #
# OrcaSlicer Pages Capability & Plugin Entrypoint
# --------------------------------------------------------------------------- #
_PagesBase = getattr(getattr(orca, "pages", None), "PagesPluginCapabilityBase", None) or object


class BlenderStudioPage(_PagesBase):
    """Top-bar page capability providing live Blender CAD integration."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._last_scene: dict[str, Any] | None = None

    def get_name(self) -> str:
        return "Blender Studio"

    def get_type(self) -> Any:
        if orca is not None and hasattr(orca, "plugin") and hasattr(orca, "base"):
            plugin_type = getattr(orca, "PluginType", None)
            if plugin_type is not None:
                for attr in ("Pages", "Page"):
                    val = getattr(plugin_type, attr, None)
                    if val is not None:
                        return val
        getter = getattr(super(), "get_type", None)
        return getter() if callable(getter) else None

    def get_icon(self) -> str:
        try:
            ensure_studio_assets()
            return str(ICON_FILE)
        except Exception:
            return ""

    def get_ui(self) -> str:
        return PAGE

    def get_default_config(self) -> dict[str, Any]:
        return dict(DEFAULT_SETTINGS)

    def get_host_config(self) -> str:
        getter = getattr(super(), "get_config", None)
        if callable(getter):
            return str(getter())
        return "{}"

    def save_host_config(self, cfg_str: str) -> bool:
        setter = getattr(super(), "save_config", None)
        if callable(setter):
            return bool(setter(cfg_str))
        return False

    def has_config_ui(self) -> bool:
        return True

    def get_config_ui(self) -> str:
        return CONFIG_PAGE

    def _post(self, payload: dict[str, Any]) -> None:
        try:
            self.post_message(payload)
        except Exception:
            pass

    def _spawn(self, fn: Any, *args: Any) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _settings(self) -> dict[str, Any]:
        return load_studio_settings(self)

    def _query_scene(self, settings: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
        res = blender_exec(_INSPECT_CODE, settings["server_host"], settings["server_port"], timeout=8.0)
        if res["ok"] and isinstance(res["file_data"], dict):
            with self._lock:
                self._last_scene = res["file_data"]
            return True, res["file_data"]
        return False, None

    def _stop_existing_server_before_spawn(self, settings: dict[str, Any]) -> None:
        host = settings["server_host"]
        port = int(settings["server_port"])
        if not is_port_open(host, port, timeout=0.2):
            return
        blender_exec(
            "import bpy\n"
            "if bpy.data.filepath and bpy.data.is_dirty:\n"
            "    try:\n"
            "        bpy.ops.wm.save_mainfile()\n"
            "    except Exception:\n"
            "        pass\n"
            "__stop_server__ = True\n",
            host,
            port,
            timeout=2.5,
        )
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if not is_port_open(host, port, timeout=0.15):
                break
            time.sleep(0.1)

    def on_message(self, msg: Any) -> None:
        msg = msg if isinstance(msg, dict) else {}
        cmd = msg.get("command")
        settings = self._settings()

        try:
            if cmd in ("init", "refresh_all"):
                self._spawn(self._worker_init, settings)
            elif cmd == "start_blender_headless":
                self._stop_existing_server_before_spawn(settings)
                spawn_blender_process(settings, str(msg.get("blend_path") or ""), background=True)
                self._spawn(self._worker_wait_and_refresh, settings, "Headless Blender server started on :9999.")
            elif cmd == "start_blender_gui":
                self._stop_existing_server_before_spawn(settings)
                spawn_blender_process(settings, str(msg.get("blend_path") or ""), background=False)
                self._spawn(self._worker_wait_and_refresh, settings, "Blender GUI launched with :9999 server.")
            elif cmd == "stop_blender_server":
                self._spawn(self._worker_stop_server, settings)
            elif cmd == "open_blend":
                filepath = str(msg.get("filepath") or "")
                if not os.path.isfile(filepath):
                    self._post({"command": "info", "text": "File not found.", "log": f"Missing: {filepath}", "err": True})
                    return
                if not is_port_open(settings["server_host"], settings["server_port"]):
                    spawn_blender_process(settings, filepath, background=True)
                    self._spawn(self._worker_wait_and_refresh, settings, f"Started headless server with {os.path.basename(filepath)}.")
                else:
                    self._spawn(self._worker_open_blend, settings, filepath)
            elif cmd == "new_project":
                self._spawn(self._worker_new_project, settings, str(msg.get("name") or "New_Design"))
            elif cmd == "save_blend":
                self._spawn(self._worker_save_blend, settings)
            elif cmd == "get_macro":
                name = str(msg.get("name") or "")
                self._post({"command": "macro", "name": name, "code": MACROS.get(name, "")})
            elif cmd == "run_bpy":
                self._spawn(self._worker_run_bpy, settings, str(msg.get("code") or ""), bool(msg.get("auto_render")))
            elif cmd == "render_view":
                self._spawn(self._worker_render_view, settings, str(msg.get("preset") or "hero"))
            elif cmd == "load_image":
                self._spawn(self._worker_load_image, settings, str(msg.get("path") or ""))
            elif cmd == "export_stl":
                self._spawn(
                    self._worker_export_stl,
                    settings,
                    list(msg.get("object_names") or ["__all_printable__"]),
                    bool(msg.get("send_to_plate")),
                )
            elif cmd == "send_to_plate":
                target = str(msg.get("path") or "")
                res = open_in_orca(target, bool(settings["single_instance_handoff"]))
                note = "Sent to current OrcaSlicer build plate." if res == "window" else f"Launched {os.path.basename(res)} with file."
                self._post({"command": "info", "text": note, "log": f"[Plate] {target} -> {note}"})
            elif cmd == "ai_prompt":
                self._spawn(self._worker_ai_prompt, settings, msg)
            elif cmd == "get_bootstrap_source":
                self._post({"command": "bootstrap_source", "code": BOOTSTRAP_SOURCE})
            elif cmd == "export_bootstrap":
                ensure_studio_assets()
                target_path = Path(settings["projects_dir"]) / "server_bootstrap.py"
                target_path.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
                self._post({
                    "command": "info",
                    "text": f"Exported bootstrap script to {target_path.name}",
                    "log": f"[Bootstrap] Saved to {target_path}",
                })
            elif cmd == "save_settings":
                ok, clean = save_studio_settings(msg.get("settings") or {}, self)
                self._post({"command": "settings_saved", "ok": ok, "settings": clean})
                self._spawn(self._worker_init, clean)
        except Exception as exc:
            self._post({"command": "info", "text": f"Error: {exc}", "log": traceback.format_exc(), "err": True})

    def _worker_init(self, settings: dict[str, Any]) -> None:
        ensure_studio_assets()
        projects = scan_projects_dir(settings["projects_dir"])
        connected, scene = self._query_scene(settings)
        self._post({
            "command": "init_state",
            "settings": settings,
            "projects": projects,
            "connected": connected,
            "scene": scene,
        })

    def _worker_wait_and_refresh(self, settings: dict[str, Any], ready_msg: str) -> None:
        deadline = time.monotonic() + 12.0
        connected = False
        scene = None
        while time.monotonic() < deadline:
            connected, scene = self._query_scene(settings)
            if connected:
                break
            time.sleep(0.35)
        projects = scan_projects_dir(settings["projects_dir"])
        self._post({"command": "projects", "projects": projects})
        self._post({
            "command": "scene_state",
            "connected": connected,
            "scene": scene,
            "status_text": ready_msg if connected else "Timed out waiting for Blender on port 9999.",
        })

    def _worker_stop_server(self, settings: dict[str, Any]) -> None:
        blender_exec("__stop_server__ = True\nprint('Stopping server.')", settings["server_host"], settings["server_port"], timeout=3.0)
        time.sleep(0.3)
        self._post({"command": "scene_state", "connected": False, "scene": None, "status_text": "Blender server stopped."})

    def _worker_open_blend(self, settings: dict[str, Any], filepath: str) -> None:
        res = blender_exec(build_open_blend_code(filepath), settings["server_host"], settings["server_port"], timeout=30.0)
        if res["ok"] and isinstance(res["file_data"], dict):
            with self._lock:
                self._last_scene = res["file_data"]
            self._post({
                "command": "scene_state",
                "connected": True,
                "scene": res["file_data"],
                "status_text": f"Opened {os.path.basename(filepath)}.",
            })
        else:
            self._post({"command": "info", "text": "Failed to open .blend.", "log": res["stderr"], "err": True})

    def _worker_new_project(self, settings: dict[str, Any], raw_name: str) -> None:
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw_name).strip(" .") or "New_Design"
        if not safe.lower().endswith(".blend"):
            safe += ".blend"
        projects_root = Path(settings["projects_dir"])
        dest = projects_root / safe
        if dest.exists():
            stem = dest.stem
            for i in range(2, 100):
                cand = projects_root / f"{stem}_{i}.blend"
                if not cand.exists():
                    dest = cand
                    break
        tpl = str(settings.get("template_blend") or "").strip()
        template_path = tpl if (tpl and os.path.isfile(tpl)) else ""
        code = build_new_project_code(str(dest), template_path=template_path)

        if not is_port_open(settings["server_host"], settings["server_port"]):
            self._post({"command": "info", "text": "Start Headless Server first, then click + New Project.", "log": f"Target: {dest}"})
        else:
            res = blender_exec(code, settings["server_host"], settings["server_port"], timeout=20.0)
            if res["ok"] and isinstance(res["file_data"], dict):
                with self._lock:
                    self._last_scene = res["file_data"]
                self._post({"command": "scene_state", "connected": True, "scene": res["file_data"], "status_text": f"Created & opened {dest.name}."})
            else:
                self._post({"command": "info", "text": "Failed to create project.", "log": res["stderr"], "err": True})
        self._post({"command": "projects", "projects": scan_projects_dir(settings["projects_dir"])})

    def _worker_save_blend(self, settings: dict[str, Any]) -> None:
        code = (
            "import bpy, os\n"
            "if bpy.data.filepath:\n"
            "    bpy.ops.wm.save_mainfile()\n"
            "    print('Saved:', bpy.data.filepath)\n"
            "else:\n"
            f"    p = os.path.join({json.dumps(settings['projects_dir'])}, 'Untitled_Studio.blend')\n"
            "    os.makedirs(os.path.dirname(p), exist_ok=True)\n"
            "    bpy.ops.wm.save_as_mainfile(filepath=p)\n"
            "    print('Saved as:', p)\n"
        )
        res = blender_exec(code, settings["server_host"], settings["server_port"], timeout=15.0)
        self._post({
            "command": "info",
            "text": "Saved .blend file." if res["ok"] else "Save failed.",
            "log": res["stdout"] or res["stderr"],
            "err": not res["ok"],
        })

    def _worker_run_bpy(self, settings: dict[str, Any], code: str, auto_render: bool) -> None:
        res = blender_exec(code, settings["server_host"], settings["server_port"], timeout=60.0)
        _, scene = self._query_scene(settings)
        self._post({
            "command": "bpy_result",
            "ok": res["ok"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "scene": scene,
        })
        if auto_render and (res["ok"] or (scene and scene.get("printable"))):
            self._worker_render_view(settings, "hero")

    def _worker_render_view(self, settings: dict[str, Any], preset: str) -> None:
        ensure_studio_assets()
        safe_preset = re.sub(r"[^a-z0-9_]", "", preset.lower()) or "hero"
        out_path = str(RENDERS_DIR / f"preview_{safe_preset}.png")
        code = build_render_code(
            safe_preset,
            out_path,
            settings["render_res_x"],
            settings["render_res_y"],
            settings["render_samples"],
        )
        res = blender_exec(code, settings["server_host"], settings["server_port"], timeout=120.0)
        if res["ok"] and isinstance(res["file_data"], dict) and res["file_data"].get("b64"):
            fd = res["file_data"]
            self._post({
                "command": "render_result",
                "ok": True,
                "preset": fd.get("preset", safe_preset),
                "b64": fd["b64"],
                "width": fd.get("width", settings["render_res_x"]),
                "height": fd.get("height", settings["render_res_y"]),
            })
        else:
            self._post({"command": "render_result", "ok": False, "error": res["stderr"] or "No image returned."})

    def _worker_load_image(self, settings: dict[str, Any], path_str: str) -> None:
        p = Path(path_str)
        root = Path(settings["projects_dir"])
        if not (safe_inside(root, p) or safe_inside(RENDERS_DIR, p)) or not p.is_file():
            self._post({"command": "image_loaded", "ok": False, "error": f"Inaccessible image: {path_str}"})
            return
        ext = p.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        self._post({
            "command": "image_loaded",
            "ok": True,
            "data_url": f"data:{mime};base64,{b64}",
            "raw_b64": b64 if ext == "png" else "",
            "label": p.name,
        })

    def _worker_export_stl(self, settings: dict[str, Any], object_names: list[str], send_to_plate: bool) -> None:
        projects_root = Path(settings["projects_dir"])
        if object_names == ["__all_printable__"]:
            with self._lock:
                stem = Path((self._last_scene or {}).get("filename") or "Blender_Export").stem
            base_name = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "Blender_Export"
        else:
            base_name = re.sub(r"[^A-Za-z0-9._-]+", "_", object_names[0]).strip("._") or "Part"
        dest = str(projects_root / f"{base_name}.stl")
        code = build_export_stl_code(object_names, dest)
        res = blender_exec(code, settings["server_host"], settings["server_port"], timeout=60.0)
        if res["ok"] and isinstance(res["file_data"], dict):
            fd = res["file_data"]
            self._post({"command": "projects", "projects": scan_projects_dir(settings["projects_dir"])})
            self._post({
                "command": "export_result",
                "ok": True,
                "path": fd["path"],
                "filename": os.path.basename(fd["path"]),
                "size_h": human_size(fd.get("size", 0)),
                "open_after": bool(send_to_plate or settings.get("auto_plate_after_export")),
            })
        else:
            self._post({"command": "export_result", "ok": False, "error": res["stderr"] or "STL export failed."})

    def _worker_ai_prompt(self, settings: dict[str, Any], msg: dict[str, Any]) -> None:
        try:
            with self._lock:
                scene = self._last_scene
            server_up = is_port_open(settings["server_host"], settings["server_port"])
            if scene is None and server_up:
                _, scene = self._query_scene(settings)
            user_prompt = str(msg.get("prompt") or "")
            provider = str(msg.get("provider") or "ollama")
            model = str(msg.get("model") or "qwen2.5-coder:7b")
            auto_run = bool(msg.get("auto_run"))

            res = call_ai_agent(
                prompt=user_prompt,
                provider=provider,
                model=model,
                scene_info=scene,
                settings=settings,
                image_b64=str(msg.get("image_b64") or ""),
                attachments=list(msg.get("attachments") or []),
            )
            if not (auto_run and res["code"]):
                self._post({
                    "command": "ai_result",
                    "ok": True,
                    "explanation": res["explanation"],
                    "code": res["code"],
                    "auto_run": False,
                    "attachments_used": res.get("attachments_used", []),
                })
                return

            if not server_up:
                server_up = is_port_open(settings["server_host"], settings["server_port"])
            if not server_up:
                self._post({
                    "command": "ai_result",
                    "ok": True,
                    "explanation": res["explanation"] + " (Start Headless Server / Blender GUI to execute.)",
                    "code": res["code"],
                    "auto_run": False,
                    "attachments_used": res.get("attachments_used", []),
                })
                return

            exec_res = blender_exec(res["code"], settings["server_host"], settings["server_port"], timeout=60.0)
            note_prefix = ""
            audit_data: dict[str, Any] | None = None
            audit_issues: list[str] = []
            if exec_res["ok"]:
                aud_res = blender_exec(
                    "import cad\n__file_data__ = cad.audit_geometry()\n",
                    settings["server_host"], settings["server_port"], timeout=15.0,
                )
                if aud_res["ok"] and isinstance(aud_res.get("file_data"), dict):
                    audit_data = aud_res["file_data"]
                    objs = audit_data.get("objects") or []
                    if not objs:
                        audit_issues.append("No printable MESH objects were created in CAD_Printable_Parts.")
                    for ob in objs:
                        if ob.get("flags"):
                            audit_issues.append(f"{ob['name']} ({', '.join(ob['flags'])})")
                    if aud_res.get("stdout"):
                        exec_res["stdout"] = (exec_res.get("stdout") or "") + "\n" + aud_res["stdout"].strip()

            if (not exec_res["ok"] and exec_res.get("stderr")) or audit_issues:
                try:
                    err_diag = (
                        f"Traceback:\n```\n{exec_res['stderr'][-2000:]}\n```"
                        if not exec_res["ok"]
                        else "3D-Print Geometry Audit FAILED:\n- "
                        + "\n- ".join(audit_issues)
                        + "\nRemember: NEVER rotate a cad.box around its own center expecting it to connect distant parts, "
                        "and NEVER boolean-cut a rotated tray with an unrotated box. Use `cad.strut(start=..., end=..., flat_bottom=True)` "
                        "and `cad.oriented_box(..., pocket_size=..., aperture_diameter=..., aim_at=...)`."
                    )
                    repair_prompt = (
                        f"Original user request:\n{user_prompt}\n\n"
                        f"The generated script had issues inside Blender {scene.get('version', '5.2.0') if scene else '5.2.0'}:\n"
                        f"```python\n{res['code']}\n```\n\n"
                        f"{err_diag}\n\n"
                        "Fix ALL issues so every printable object has `islands == 1` (single connected manifold body), "
                        "`non_manifold == 0`, and `min_Z >= 0.0`, then return ONE complete, self-contained ```python``` block."
                    )
                    repaired = call_ai_agent(
                        prompt=repair_prompt,
                        provider=provider,
                        model=model,
                        scene_info=scene,
                        settings=settings,
                        image_b64="",
                        attachments=[],
                    )
                    if repaired.get("code"):
                        res["code"] = repaired["code"]
                        reason = "geometry audit" if audit_issues else "Blender error"
                        note_prefix = f"(Auto-repaired after {reason}.) "
                        exec_res = blender_exec(res["code"], settings["server_host"], settings["server_port"], timeout=60.0)
                        if exec_res["ok"]:
                            aud_res2 = blender_exec(
                                "import cad\n__file_data__ = cad.audit_geometry()\n",
                                settings["server_host"], settings["server_port"], timeout=15.0,
                            )
                            if aud_res2.get("stdout"):
                                exec_res["stdout"] = (exec_res.get("stdout") or "") + "\n" + aud_res2["stdout"].strip()
                except Exception:
                    pass

            self._post({
                "command": "ai_result",
                "ok": True,
                "explanation": (note_prefix + res["explanation"]).strip(),
                "code": res["code"],
                "auto_run": False,
                "attachments_used": res.get("attachments_used", []),
            })
            _, post_scene = self._query_scene(settings)
            self._post({
                "command": "bpy_result",
                "ok": exec_res["ok"],
                "stdout": exec_res["stdout"],
                "stderr": exec_res["stderr"],
                "scene": post_scene,
            })
            if exec_res["ok"] or (post_scene and post_scene.get("printable")):
                self._worker_render_view(settings, "hero")
        except Exception as exc:
            self._post({"command": "ai_result", "ok": False, "error": str(exc)})


if orca is not None and hasattr(orca, "plugin") and hasattr(orca, "base"):
    @orca.plugin
    class BlenderStudioPlugin(orca.base):
        """Registers the Blender Studio top-bar tab inside OrcaSlicer."""

        def register_capabilities(self) -> None:
            if hasattr(orca, "request_permissions"):
                try:
                    fs_read = [
                        str(STUDIO_DIR),
                        f"{STUDIO_DIR}/*",
                        str(DEFAULT_PROJECTS_DIR),
                        f"{DEFAULT_PROJECTS_DIR}/*",
                        str(Path.home() / "Blender"),
                        f"{Path.home() / 'Blender'}/*",
                        str(Path.home() / "Documents"),
                        f"{Path.home() / 'Documents'}/*",
                        tempfile.gettempdir(),
                        f"{tempfile.gettempdir()}/*",
                    ]
                    if DEFAULT_BLENDER_BIN and os.path.isabs(DEFAULT_BLENDER_BIN):
                        fs_read.append(str(Path(DEFAULT_BLENDER_BIN).parent / "*"))

                    fs_readwrite = [
                        str(STUDIO_DIR),
                        f"{STUDIO_DIR}/*",
                        str(DEFAULT_PROJECTS_DIR),
                        f"{DEFAULT_PROJECTS_DIR}/*",
                        tempfile.gettempdir(),
                        f"{tempfile.gettempdir()}/*",
                    ]

                    network_socket = [
                        ("127.0.0.1", 9999),
                        ("localhost", 9999),
                        ("127.0.0.1", 11434),
                        ("127.0.0.1", 11435),
                    ]

                    network_http = [
                        "http://127.0.0.1:9999",
                        "http://localhost:9999",
                        "http://127.0.0.1:11434",
                        "http://127.0.0.1:11435",
                        "https://generativelanguage.googleapis.com",
                        "https://api.openai.com",
                    ]

                    process_candidates = [
                        DEFAULT_BLENDER_BIN,
                        "blender",
                        "gdbus",
                        "xdg-open",
                        "open",
                        "cmd.exe",
                    ]
                    process_list = [p for p in process_candidates if p]

                    orca.request_permissions(
                        fs_read=fs_read,
                        fs_readwrite=fs_readwrite,
                        network_socket=network_socket,
                        network_http=network_http,
                        process=process_list,
                        threading=True,
                    )
                except Exception:
                    traceback.print_exc()
            orca.register_capability(BlenderStudioPage)
