# Blender Studio for OrcaSlicer

<p align="center">
  <img src="assets/hub_hero_banner.png" alt="Blender Studio for OrcaSlicer" width="100%">
</p>

<p align="center">
  <b>Live Blender (:9999) Procedural CAD, Diagnostic Render-Inspect-Refine Studio, Hardware Mockups, AI CAD Assistant, and One-Click Send to Plate — directly inside an OrcaSlicer tab.</b>
</p>

<p align="center">
  <a href="QUICK_REFERENCE.md">Quick Reference</a> &bull;
  <a href="USER_GUIDE.md">Full User Guide</a> &bull;
  <a href="CHANGELOG.md">Changelog</a> &bull;
  <a href="standalone/server_bootstrap.py">Standalone Server</a> &bull;
  <a href="LICENSE">MIT License</a>
</p>

---

**Blender Studio** is a zero-dependency, single-file **OrcaSlicer Pages Plugin** (`v1.2.5`) compatible with OrcaSlicer v2.5.0 and v2.5.1+ that connects OrcaSlicer directly to a live Blender instance over a main-thread TCP bridge (port `9999`). Instead of bouncing manually between Blender, file explorers, and your slicer, you can design or tweak 3D-printable parts procedurally with the built-in `cad` helper or `bpy` (or a local Ollama two-stage Vision + Coder / Gemini 3.8 Flash AI assistant with reference CAD files, images & spec documents), inspect clearances with multi-angle and exploded EEVEE diagnostic renders, run a watertight manifold audit, and send binary STLs straight onto your active OrcaSlicer build plate in one click.

<p align="center">
  <img src="assets/diagnostic_views_showcase.png" alt="Diagnostic Views Showcase" width="100%">
</p>

*Sample enclosure (`Studio_Test_Pod.blend`) rendered inside Blender Studio — showing printable parts (`CAD_Printable_Parts`) alongside reference hardware mockups (`M_PCB_Board`, `M_USB_Port`, and `Mockup_Mag_0..3`) in Hero and Exploded diagnostic views.*

---

## Key Features

| Area | Capabilities |
| :--- | :--- |
| **Live Blender Bridge (`:9999`)** | Start a silent **Headless Daemon** (`--background`), launch the full **Blender GUI**, or connect to a **remote/Tailscale** Blender instance over TCP port `9999`. |
| **Zero-Dependency Single File** | Pure Python 3.10+ stdlib (`blender_studio.py`). No `pip` or `uv` package installs during OrcaSlicer startup. |
| **Printable vs. Mockup Separation** | Automatically separates printable CAD meshes (`Test_Pod_Base`, `Test_Pod_Lid`, etc.) from reference mockups (`M_*`, `Mockup_*`, `Vis_*`, `Plug_*`, `Hardware_Mockups`) with live `X × Y × Z mm` dimensions. |
| **Render-Inspect-Refine Viewport** | One-click **Hero**, **Top**, **Front**, **Isometric**, **Exploded**, and **Scene Cam** diagnostic renders via `BLENDER_EEVEE`, plus zoom controls and a saved project render filmstrip. |
| **AI CAD Assistant** | Injects live scene bounding boxes and hardware mockups into prompts for **Google Gemini 3.8 Flash**, **Local Ollama** (`qwen2.5-coder:7b`, `qwen3.5-9b-vision`), or any **OpenAI-Compatible** endpoint. Attach reference **CAD models** (`.step`, `.stp`, `.3mf`, `.stl`), **pictures**, and **documents** via **`+ File / CAD`**, drag-and-drop, or `Ctrl+V` paste, with closed-loop geometry audit, live animated progress wheel and pulse bar, and auto-repair (`islands == 1`, `non_manifold == 0`, `min_Z >= 0.0`). |
| **Host System Stability & Low CPU Footprint** | Thread-capped local inference (`num_thread: 4`) and non-blocking asynchronous socket bridge to keep CPU fans quiet and preserve system stability. |
| **Procedural CAD Helpers (`cad.*`)** | High-level parametric primitives: `cad.box`, `cad.rounded_plate`, `cad.cylinder`, `cad.tube`, `cad.strut` (with flat-bottom FDM support), `cad.oriented_box` (local pocket/aperture cuts before aiming), `cad.counterbore_hole`, `cad.slot`, `cad.magnet_pocket`, `cad.m3_hole`, `cad.place_on_bed`, and `cad.audit_geometry`. |
| **Built-in 3D Printing Macros** | One-click macros for **3-Point Studio Rig**, **6×2 mm Magnet Pocket Mockup** (`Ø6.25 × 2.20 mm`), **M3 Boss & Captive Nut**, **SG90 Servo Mockup**, and **Manifold / Scale / Z-Floor Audit**. |
| **Cross-Platform Send to Plate** | Exports binary STL via `wm.stl_export` (`1 BU = 1 mm`) and delivers it straight onto the active **Prepare** build plate via Linux D-Bus (`AnotherInstance`), Windows `WM_COPYDATA`, or macOS single-instance handoff. |

---

## 60-Second Installation

### Prerequisites
- **OrcaSlicer v2.5.0+** (with Python `Pages` plugin support enabled)
- **Blender 4.2+ / 5.x** (tested on **Blender 5.2.0 LTS**)

### Method 1: Subscribe via Orca Cloud Plugin Hub (Recommended)
1. Subscribe directly in your browser or OrcaSlicer via the official [**Orca Cloud Plugin Hub**](https://cloud.orcaslicer.com/app/plugins/shared-plugins/461ebb05-bc36-4247-bb6d-a8e92c32354b).
2. Open **OrcaSlicer** &rarr; open **Plugins** &rarr; click **Sync** / **Install**.
3. On first load, approve the upfront permissions dialog (local socket `:9999`, designs folder, Blender binary).
4. Click the new **Blender Studio** tab in OrcaSlicer's top navigation bar!

> **100% Self-Contained:** Plugin Hub subscribers do not need to download extra files from GitHub to connect existing Blender sessions. Simply open **Settings (⚙)** inside the Blender Studio tab and click **📋 Copy Bootstrap Script** to paste directly into Blender's Scripting tab, or click **💾 Export server_bootstrap.py** into your designs directory.

### Method 2: Install via Local Plugin File (.py)
1. Download [`blender_studio.py`](blender_studio.py) (or `blender_studio_any.py` from [`dist/`](dist/)).
2. Open **OrcaSlicer** &rarr; open **Plugins** &rarr; click **Install local plugin** and select the `.py` file.
3. Restart OrcaSlicer (or reload plugins) and approve permissions.

### Method 3: Manual Side-Load Install
Copy `blender_studio.py` into your OrcaSlicer plugins directory under a matching folder name:
- **Linux:** `~/.config/OrcaSlicer/orca_plugins/blender_studio/blender_studio.py`
- **Windows:** `%APPDATA%\OrcaSlicer\orca_plugins\blender_studio\blender_studio.py`
- **macOS:** `~/Library/Application Support/OrcaSlicer/orca_plugins/blender_studio/blender_studio.py`

---

### Upfront Permissions Notice
OrcaSlicer 2.5.0+ introduces fine-grained plugin permission security. To prevent repetitive runtime prompts on every render or AI query, Blender Studio declares its permissions upfront inside `register_capabilities()`:
- **Local Sockets & Network (`network_socket`, `network_http`):** Bridge connection to Blender on `127.0.0.1:9999`, local Ollama on `127.0.0.1:11434-11435`, and user-configured AI endpoints (`generativelanguage.googleapis.com`, OpenAI-compatible).
- **Filesystem Access (`fs_read`, `fs_readwrite`):** Your configured `3D Designs` project directory, OrcaSlicer `blender_studio` configuration and render caches, and temporary STL export directories.
- **Process Spawning (`process`):** Execution of the local Blender executable (`blender`) and build plate handoff helpers (`gdbus`, `xdg-open`, `open`).
- **Threading (`threading`):** Background worker threads for responsive UI and non-blocking renders.

All child processes run with sanitized, allowlisted environment variables (`PATH`, `HOME`, `DISPLAY`, etc.)—never leaking full system environment dictionaries or secrets.

---

## Architecture & Workflow

<p align="center">
  <img src="assets/workflow_pipeline.png" alt="Blender Studio Workflow Pipeline" width="100%">
</p>

```
+-----------------------------------------------------------------------+
|  OrcaSlicer (Top-Bar Pages Plugin: blender_studio.py)                 |
|  +------------------+  +--------------------+  +-------------------+  |
|  | Scene / Projects |  | Render Viewport    |  | AI CAD & bpy IDE  |  |
|  | & STL Library    |  | (Hero/Ortho/Expl.) |  | + Print Macros    |  |
|  +--------+---------+  +---------+----------+  +---------+---------+  |
|           |                      |                       |            |
|           +----------------------+-----------------------+            |
|                                  | TCP :9999 (4-byte len + UTF-8 JSON)|
+----------------------------------|------------------------------------+
                                   v
+-----------------------------------------------------------------------+
|  Blender 4.x / 5.x (server_bootstrap.py)                              |
|  - Headless Mode: Direct main-thread socket listener (--background)   |
|  - GUI Mode: Persistent bpy.app.timers main-thread queue (survives    |
|    bpy.ops.wm.open_mainfile across .blend switches)                   |
|  - Units: Metric 1 Blender Unit = 1.0 mm                              |
+-----------------------------------------------------------------------+
                                   |
                                   v  Binary STL -> D-Bus / WM_COPYDATA
                      [ OrcaSlicer Prepare Plate ]
```

---

## Documentation
- **[Quick Reference (`QUICK_REFERENCE.md`)](QUICK_REFERENCE.md)** — Cheat sheet for buttons, camera presets, macros, 3D-printing clearance tolerances, `bpy` snippets, and the `:9999` TCP protocol.
- **[Full User Guide (`USER_GUIDE.md`)](USER_GUIDE.md)** — Complete walkthrough covering setup, the Render-Inspect-Refine workflow, mockup conventions, AI CAD prompting, remote/Tailscale Blender servers, and troubleshooting.

---

## License
Released under the [MIT License](LICENSE) — Copyright (c) 2026 Gabrial McCauley (GDME LLC).
