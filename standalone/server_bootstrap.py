"""Dual-mode Blender TCP socket server bootstrap (GUI timer + headless daemon)."""
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
