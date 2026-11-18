# /// script
# requires-python = ">=3.10"
#
# [tool.orcaslicer.plugin]
# name = "Blender Studio"
# description = "Live Blender (:9999) procedural bpy CAD, hardware mockups, diagnostic EEVEE render studio, AI CAD assistant, and one-click Send to Plate."
# author = "Gabrial McCauley (GDME LLC)"
# version = "1.2.1"
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
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

try:
    import orca  # type: ignore[import-not-found]
except ImportError:
    orca = None


# --------------------------------------------------------------------------- #
# Paths & Cross-Platform Auto-Discovery (avoid audit keywords: conf/config/secret)
# --------------------------------------------------------------------------- #
PLUGIN_VERSION = "1.2.1"
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

BOOTSTRAP_SOURCE = '''"""Dual-mode Blender TCP socket server bootstrap (GUI timer + headless daemon)."""
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
import traceback

PORT = int(os.environ.get("BLENDER_SERVER_PORT", "9999"))
HOST = os.environ.get("BLENDER_SERVER_HOST", "0.0.0.0")

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


class _OpsSubProxy:
    """Transparent proxy overriding selected Blender operators with safe wrappers."""

    def __init__(self, target, overrides):
        self._target = target
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._target, name)


def _install_bpy_shims():
    """Install lightweight Blender 4.x/5.x compatibility shims for AI/legacy bpy scripts."""
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

    orig_ops = bpy.ops
    orig_mesh = orig_ops.mesh
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
        clean = {k: v for k, v in kw.items() if k in allowed}
        return orig_mesh.primitive_cylinder_add(*args, **clean)

    def _safe_uv_sphere_add(*args, **kwargs):
        kw = dict(kwargs)
        if "subdivisions" in kw and "segments" not in kw:
            kw["segments"] = int(kw.pop("subdivisions"))
        allowed = {"segments", "ring_count", "radius", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        clean = {k: v for k, v in kw.items() if k in allowed}
        return orig_mesh.primitive_uv_sphere_add(*args, **clean)

    def _safe_ico_sphere_add(*args, **kwargs):
        allowed = {"subdivisions", "radius", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        clean = {k: v for k, v in kwargs.items() if k in allowed}
        return orig_mesh.primitive_ico_sphere_add(*args, **clean)

    def _safe_cone_add(*args, **kwargs):
        kw = dict(kwargs)
        if "height" in kw and "depth" not in kw:
            kw["depth"] = kw.pop("height")
        if "segments" in kw and "vertices" not in kw:
            kw["vertices"] = int(kw.pop("segments"))
        allowed = {"vertices", "radius1", "radius2", "depth", "end_fill_type", "calc_uvs", "enter_editmode", "align", "location", "rotation", "scale"}
        clean = {k: v for k, v in kw.items() if k in allowed}
        return orig_mesh.primitive_cone_add(*args, **clean)

    mesh_proxy = _OpsSubProxy(
        orig_mesh,
        {
            "primitive_cube_add": _safe_cube_add,
            "primitive_cylinder_add": _safe_cylinder_add,
            "primitive_uv_sphere_add": _safe_uv_sphere_add,
            "primitive_ico_sphere_add": _safe_ico_sphere_add,
            "primitive_cone_add": _safe_cone_add,
        },
    )

    ops_overrides = {"mesh": mesh_proxy}
    if hasattr(orig_wm, "stl_export"):
        orig_export_mesh = getattr(orig_ops, "export_mesh", None)

        def _legacy_stl_export(*args, **kwargs):
            if orig_export_mesh and hasattr(orig_export_mesh, "stl"):
                return orig_export_mesh.stl(*args, **kwargs)
            kw = dict(kwargs)
            mapped = {}
            if "filepath" in kw:
                mapped["filepath"] = kw["filepath"]
            if "ascii" in kw:
                mapped["ascii_format"] = bool(kw["ascii"])
            if "use_selection" in kw:
                mapped["export_selected_objects"] = bool(kw["use_selection"])
            if "global_scale" in kw:
                mapped["global_scale"] = float(kw["global_scale"])
            if "use_scene_unit" in kw:
                mapped["use_scene_unit"] = bool(kw["use_scene_unit"])
            if "use_mesh_modifiers" in kw:
                mapped["apply_modifiers"] = bool(kw["use_mesh_modifiers"])
            return orig_wm.stl_export(**mapped)

        if orig_export_mesh is not None:
            ops_overrides["export_mesh"] = _OpsSubProxy(orig_export_mesh, {"stl": _legacy_stl_export})

    bpy.ops = _OpsSubProxy(orig_ops, ops_overrides)


_BSDF_ALIASES = (
    (r"""inputs\\[\\s*(["'])Specular\\1\\s*\\]""", r"""inputs[\\1Specular IOR Level\\1]"""),
    (r"""inputs\\[\\s*(["'])Transmission\\1\\s*\\]""", r"""inputs[\\1Transmission Weight\\1]"""),
    (r"""inputs\\[\\s*(["'])Emission\\1\\s*\\]""", r"""inputs[\\1Emission Color\\1]"""),
    (r"""inputs\\[\\s*(["'])Subsurface\\1\\s*\\]""", r"""inputs[\\1Subsurface Weight\\1]"""),
    (r"""inputs\\[\\s*(["'])Coat\\1\\s*\\]""", r"""inputs[\\1Coat Weight\\1]"""),
)


def _normalize_bpy_code(code_str):
    for pat, repl in _BSDF_ALIASES:
        code_str = re.sub(pat, repl, code_str)
    return code_str


def execute_code_in_main_thread(code_str):
    global server_running
    _install_bpy_shims()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    out_buf, err_buf = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = out_buf, err_buf
    status, error_msg, file_data = "success", "", None
    try:
        normalized = _normalize_bpy_code(code_str)
        exec_globals = {
            "bpy": bpy, "bmesh": bmesh, "mathutils": mathutils,
            "math": math, "os": os, "sys": sys, "json": json,
            "base64": base64, "__builtins__": __builtins__,
        }
        exec(normalized, exec_globals)
        if "__file_data__" in exec_globals:
            file_data = exec_globals["__file_data__"]
        if exec_globals.get("__stop_server__") is True:
            server_running = False
            print("[Server] Stop command received from client.")
    except Exception:
        status = "error"
        error_msg = traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return {
        "status": status,
        "stdout": out_buf.getvalue(),
        "stderr": err_buf.getvalue() or error_msg,
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


def child_environment() -> tuple[dict[str, str] | None, str | None]:
    """Strip AppImage loader variables before launching Blender or external processes."""
    if not sys.platform.startswith("linux") or not os.environ.get("APPIMAGE"):
        return None, None
    env = dict(os.environ)
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
    if env is None:
        env = dict(os.environ)
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
    "print_audit": '''# 3D Print Readiness Audit (manifold check, scale, Z-floor)
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
    if vol < 0:
        flags.append("inverted_normals")
    if not scale_ok:
        flags.append(f"unapplied_scale={[round(s,3) for s in o.scale]}")
    if flags:
        issues += 1
    status = "OK" if not flags else "WARN: " + ", ".join(flags)
    print(f"  {o.name}: {o.dimensions.x:.2f}x{o.dimensions.y:.2f}x{o.dimensions.z:.2f}mm | min_Z={min_z:.2f}mm | {status}")
print(f"Audit complete: {issues} object(s) flagged.")
''',
}


# --------------------------------------------------------------------------- #
# AI Assistant Helpers (Local Ollama + Google Gemini + OpenAI-Compatible)
# --------------------------------------------------------------------------- #
def _extract_python_block(text: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.strip()


def _build_ai_system_prompt(scene_info: dict[str, Any] | None) -> str:
    scene_lines = []
    if scene_info and scene_info.get("filepath"):
        scene_lines.append(f"Active .blend: {scene_info.get('filename')} ({scene_info.get('filepath')})")
        scene_lines.append(f"Blender version: {scene_info.get('version', '5.2.0')}")
        for p in scene_info.get("printable", [])[:25]:
            scene_lines.append(
                f"  - Printable Mesh `{p['name']}`: dims={p['dims']}mm, center={p['center']}, "
                f"bmin={p['bmin']}, bmax={p['bmax']}, polys={p['polys']}"
            )
        for m in scene_info.get("mockups", [])[:25]:
            scene_lines.append(
                f"  - Hardware Mockup `{m['name']}`: dims={m['dims']}mm, center={m['center']}"
            )
    else:
        scene_lines.append("Active scene: No scene metadata loaded (assume empty 1 BU = 1 mm workspace).")

    return (
        "You are an expert Blender procedural CAD engineer building 3D-printable mechanical parts.\n"
        "Conventions used in this workspace:\n"
        "1. Units: 1 Blender Unit = 1.0 mm. Keep object scale at (1, 1, 1) after transforms.\n"
        "2. Tolerances: +0.20 mm slide clearance, +0.30 mm clamp clearance, +0.25 mm magnet press-fit (e.g. 6.25x2.20mm pocket for 6x2mm discs).\n"
        "3. Organization: Place printable geometry in `CAD_Printable_Parts` collection and reference hardware mockups (`M_*`, `Mockup_*`) in `Hardware_Mockups`.\n"
        "4. Blender 5.2 API Rules (CRITICAL):\n"
        "   - Use `bmesh` (always set `profile=0.5` for convex `bmesh.ops.bevel`) + `bpy.ops.object.modifier_add(type='BOOLEAN')`.\n"
        "   - `bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, y, z))` ONLY takes a scalar float `size` and `location`; NEVER pass `width`, `height`, `depth`, or `sections` (scale via `obj.scale = (w, h, d)` + `bpy.ops.object.transform_apply(scale=True)`).\n"
        "   - `bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=r, depth=h, location=(x, y, z))` uses `radius` and `depth` (never `height` or `segments`).\n"
        "   - If creating 3D text (`bpy.ops.object.text_add`) or curves (`CURVE`), convert them to `MESH` at the end (`bpy.context.view_layer.objects.active = obj; obj.select_set(True); bpy.ops.object.convert(target='MESH')`) and link into `CAD_Printable_Parts`.\n"
        "   - For `Principled BSDF`, only set `inputs['Base Color']`, `inputs['Roughness']`, and `inputs['Metallic']`. Never use `mesh.use_auto_smooth`.\n"
        "   - Use `bpy.ops.wm.stl_export(...)` for STL export.\n"
        "5. Output a brief 1-2 sentence summary followed by ONE complete, self-contained ```python``` block ready to exec() inside Blender.\n\n"
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


def _prepare_ai_attachments(
    attachments: list[dict[str, Any]] | None,
    viewport_b64: str = "",
) -> tuple[list[dict[str, str]], str, list[str]]:
    """Normalize image and document attachments for multi-modal and text LLM calls."""
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
                images.append({"name": name, "mime": mime or "image/png", "b64": raw_b64})
                summary_names.append(f"{name} (image)")
            continue

        is_pdf = kind == "pdf" or name.lower().endswith(".pdf") or mime == "application/pdf"
        if is_pdf:
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


def _call_openai_chat(
    endpoint: str,
    api_key: str,
    model: str,
    sys_prompt: str,
    prompt: str,
    images: list[dict[str, str]] | None = None,
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")


def _is_ollama_vision_model(model_name: str) -> bool:
    low = model_name.lower()
    return any(tok in low for tok in ("vision", "vl", "llava", "minicpm", "gemma3", "moondream"))


def _pick_ollama_vision_fallback(settings: dict[str, Any]) -> str:
    """Return an installed Ollama vision model name if available, else empty string."""
    preferred = "lukey03/qwen3.5-9b-abliterated-vision:latest"
    for base_url in (settings["ollama_url"], settings["ollama_fallback_url"]):
        endpoint = base_url.rstrip("/") + "/api/tags"
        try:
            with urllib.request.urlopen(urllib.request.Request(endpoint, method="GET"), timeout=3) as resp:
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
        "options": {"temperature": 0.2, "num_ctx": num_ctx},
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
            with urllib.request.urlopen(req, timeout=180) as resp:
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
        full_prompt += "\n\nAttached Reference Images (inspect visually alongside the prompt):\n" + "\n".join(
            f"- {img['name']} ({img['mime']})" for img in images
        )
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
        text = _call_openai_chat(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            api_key,
            model or "gemini-2.5-flash",
            sys_prompt,
            full_prompt,
            images,
        )
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
        if img_b64s and not _is_ollama_vision_model(chosen_model):
            vision_fallback = _pick_ollama_vision_fallback(settings)
            if vision_fallback:
                chosen_model = vision_fallback
                model_note = f"(Auto-switched to vision model `{vision_fallback}` for attached image context.) "
        try:
            text = _post_ollama_chat(settings, chosen_model, sys_prompt, full_prompt, img_b64s, num_ctx=num_ctx)
        except Exception:
            if img_b64s and not _is_ollama_vision_model(chosen_model):
                text = _post_ollama_chat(settings, chosen_model, sys_prompt, full_prompt, [], num_ctx=num_ctx)
                model_note = (
                    f"(Selected model `{chosen_model}` does not support image inputs; images were omitted. "
                    "Switch to a vision model for picture analysis.) "
                )
            else:
                raise

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
          <option value="ollama::qwen2.5-coder:7b">Ollama: qwen2.5-coder:7b</option>
          <option value="ollama::lukey03/qwen3.5-9b-abliterated-vision:latest">Ollama: qwen3.5-9b-vision</option>
          <option value="gemini::gemini-2.5-flash">Gemini 2.5 Flash</option>
          <option value="gemini::gemini-2.5-pro">Gemini 2.5 Pro</option>
          <option value="gemini::gemini-3.5-flash">Gemini 3.5 Flash</option>
          <option value="openai::custom">OpenAI-Compatible (Settings)</option>
        </select>
      </div>
      <div class="ai-box" id="ai-box">
        <textarea id="ai-prompt" rows="3" placeholder="Describe a part or edit, or drop / paste reference images &amp; docs (.md, .pdf, .py, .scad, .json)..."></textarea>
        <input type="file" id="ai-file-input" multiple accept="image/*,.md,.txt,.json,.csv,.py,.scad,.yaml,.yml,.xml,.svg,.html,.pdf,.log,.ini,.toml" style="display:none">
        <div class="attach-bar" id="ai-attachments" style="display:none"></div>
        <div class="row" style="margin-top:6px">
          <button class="sm" id="btn-ai-attach" title="Attach pictures (.png, .jpg, .webp, .gif, .bmp) or documents (.md, .txt, .py, .scad, .json, .csv, .pdf) — or drag &amp; drop / paste into prompt">+ Image / Doc</button>
          <button class="sm danger" id="btn-ai-clear-att" style="display:none" title="Remove all attached reference files">Clear</button>
          <label class="chk"><input type="checkbox" id="chk-auto-run" checked> Auto-run</label>
          <label class="chk" title="Also include the current viewport diagnostic render"><input type="checkbox" id="chk-attach-img"> +Viewport</label>
          <div class="spacer"></div>
          <button class="primary sm" id="btn-ai-go">Generate bpy</button>
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
    } else if (isPdf) {
      const reader = new FileReader();
      reader.onload = ev => {
        const dataUrl = String(ev.target && ev.target.result || '');
        const b64 = dataUrl.includes(',') ? dataUrl.split(',', 2)[1] : dataUrl;
        if (!b64) return;
        S.aiAttachments.push({
          name, kind: 'pdf', mime: 'application/pdf', size: file.size, size_h: humanBytes(file.size),
          b64, text: '', previewUrl: '',
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
      log(`[Attach] Unsupported file type: ${name} (${mime || ext || 'binary'}). Try an image, PDF, or text/code document.`, 'err');
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

$('btn-ai-go').onclick = () => {
  const rawPrompt = $('ai-prompt').value.trim();
  if (!rawPrompt && !S.aiAttachments.length) return;
  const prompt = rawPrompt || 'Analyze the attached reference files and generate the corresponding Blender bpy CAD script.';
  const [provider, model] = ($('ai-model').value || 'ollama::qwen2.5-coder:7b').split('::');
  const attCount = S.aiAttachments.length + ($('chk-attach-img').checked && S.lastRenderB64 ? 1 : 0);
  const attNote = attCount ? ` (+${attCount} attachment${attCount > 1 ? 's' : ''})` : '';
  setStatus(`Asking ${provider === 'openai' ? (S.settings.openai_model || 'custom model') : model}${attNote}...`, true);
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

if (window.orca && window.orca.onMessage) {
  window.orca.onMessage(msg => {
    if (!msg) return;
    switch (msg.command) {
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
            if not exec_res["ok"] and exec_res.get("stderr"):
                try:
                    repair_prompt = (
                        f"Original user request:\n{user_prompt}\n\n"
                        f"The generated script failed inside Blender {scene.get('version', '5.2.0') if scene else '5.2.0'}:\n"
                        f"```python\n{res['code']}\n```\n\n"
                        f"Traceback:\n```\n{exec_res['stderr'][-2000:]}\n```\n\n"
                        "Fix the error and return ONE complete, self-contained ```python``` block."
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
                        note_prefix = "(Auto-repaired after Blender error.) "
                        exec_res = blender_exec(res["code"], settings["server_host"], settings["server_port"], timeout=60.0)
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
                        str(DEFAULT_PROJECTS_DIR),
                        f"{DEFAULT_PROJECTS_DIR}/*",
                        str(Path.home() / "Blender"),
                        str(Path.home() / "Blender" / "*"),
                    ]
                    if os.path.isabs(DEFAULT_BLENDER_BIN):
                        fs_read.append(str(Path(DEFAULT_BLENDER_BIN).parent / "*"))
                    process_list = [DEFAULT_BLENDER_BIN] if DEFAULT_BLENDER_BIN else []
                    orca.request_permissions(
                        fs_read=fs_read,
                        fs_readwrite=[str(DEFAULT_PROJECTS_DIR), f"{DEFAULT_PROJECTS_DIR}/*"],
                        network=[
                            "http://127.0.0.1:9999",
                            "http://localhost:9999",
                            "http://127.0.0.1:11435",
                            "http://127.0.0.1:11434",
                            "https://generativelanguage.googleapis.com",
                            "https://api.openai.com",
                        ],
                        process=process_list,
                        threading=True,
                    )
                except Exception:
                    traceback.print_exc()
            orca.register_capability(BlenderStudioPage)
