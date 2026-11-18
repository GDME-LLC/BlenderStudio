# Blender Studio — Full User Guide

A complete guide to procedural 3D-print CAD, visual clearance verification, AI-assisted `bpy` modeling, and direct slicer integration with **Blender Studio** inside **OrcaSlicer**.

---

## Table of Contents
1. [System Requirements & Cross-Platform Installation](#1-system-requirements--cross-platform-installation)
2. [How Blender Studio Works Under the Hood](#2-how-blender-studio-works-under-the-hood)
3. [The "Render-Inspect-Refine" CAD Workflow](#3-the-render-inspect-refine-cad-workflow)
4. [Scene Organization: Printable Parts vs. Hardware Mockups](#4-scene-organization-printable-parts-vs-hardware-mockups)
5. [Interactive `bpy` Scripting & Built-in Macros](#5-interactive-bpy-scripting--built-in-macros)
6. [Using the AI CAD Assistant (Ollama, Gemini & OpenAI-Compatible)](#6-using-the-ai-cad-assistant-ollama-gemini--openai-compatible)
7. [Exporting STLs & Direct "Send to Plate" Handoff](#7-exporting-stls--direct-send-to-plate-handoff)
8. [Remote / Tailscale Blender & External AI Agent Setup](#8-remote--tailscale-blender--external-ai-agent-setup)
9. [Troubleshooting & FAQ](#9-troubleshooting--faq)

---

## 1. System Requirements & Cross-Platform Installation

### Requirements
- **OrcaSlicer v2.5.0+** (supports embedded Python `orca.pages` plugins)
- **Blender 4.2 or 5.x** (tested on **Blender 5.2.0 LTS**)
- **No external Python packages required** — `blender_studio.py` uses only Python 3.10+ standard library modules (`socket`, `json`, `urllib`, `ctypes`, `subprocess`, `threading`).

### Installing the Plugin
1. Download [`blender_studio.py`](blender_studio.py).
2. In OrcaSlicer, open **Plugins** &rarr; click **Install local plugin** &rarr; choose `blender_studio.py` (or place it in `<OrcaSlicerDataDir>/orca_plugins/blender_studio/blender_studio.py`).
3. Restart OrcaSlicer. On first load, OrcaSlicer may ask you to approve local permissions for:
   - Reading/writing your 3D designs folder
   - Local network access to `127.0.0.1:9999` (Blender), `127.0.0.1:11434/11435` (Ollama), and any cloud AI API host you use
   - Launching the Blender executable and background threads

### Auto-Detection Paths
On first launch, Blender Studio searches standard locations automatically:
- **Linux:** `~/Blender/blender-*/blender`, `blender` on `PATH`, `/usr/bin/blender`, `/snap/bin/blender`, Flatpak (`org.blender.Blender`)
- **Windows:** `%ProgramFiles%\Blender Foundation\Blender *\blender.exe`, Steam Blender, `blender.exe` on `PATH`
- **macOS:** `/Applications/Blender.app/Contents/MacOS/Blender`, `~/Applications/Blender.app/Contents/MacOS/Blender`

Your 3D Designs directory defaults to `~/projects/3D_Designs` (or `~/3D_Designs` / `~/Documents/3D_Designs` if present, otherwise `<OrcaSlicerDataDir>/blender_studio/projects`). You can override any path at any time via **Settings**.

---

## 2. How Blender Studio Works Under the Hood

Blender's Python API (`bpy`) is **not thread-safe** — mutating meshes or triggering renders from a raw background thread causes segmentation faults. Blender Studio solves this with [`standalone/server_bootstrap.py`](standalone/server_bootstrap.py), which adapts automatically to how Blender is launched:

1. **Headless Daemon Mode (`--background`):**
   - Runs a TCP listener directly on Blender's main thread on `0.0.0.0:9999`.
   - Ideal for fast procedural edits, AI code generation, `BLENDER_EEVEE` diagnostic renders, and binary STL exports without opening a 3D viewport window.
2. **Interactive GUI Mode:**
   - Spawns a background socket thread (`BlenderSocketServer`) that pushes incoming scripts onto a thread-safe `queue.Queue`.
   - Registers a **persistent** `bpy.app.timers` callback (`process_queue_timer`, firing every `0.05 s`) with `persistent=True` so the server survives `bpy.ops.wm.open_mainfile()` when switching between `.blend` projects.

---

## 3. The "Render-Inspect-Refine" CAD Workflow

Instead of guessing coordinates blindly or slicing broken meshes, use this tight loop inside the **Blender Studio** tab:

1. **Create or Open a Project:**
   - Open the **Projects** tab in the left column and click **+ New Project (.blend)** (creates a millimeter-calibrated scene where `1 Blender Unit = 1.0 mm`), or click **Open in Server** on any existing `.blend` file.
2. **Generate Geometry + Mockups:**
   - Write `bpy`/`bmesh` code in the right-hand editor (or prompt the AI CAD Assistant) to build both your printable parts and dummy mockups of real hardware (PCBs, magnets, servos, cables).
3. **Render Diagnostic Angles:**
   - Click **Hero** for an overall 3/4 view.
   - Click **Top** or **Front** (orthographic) to verify hole alignment, wall thickness, and connector clearances without perspective distortion.
   - Click **Exploded** to separate printable parts around the assembly center and verify internal bosses, registration lips, and concealed pockets (original coordinates are restored cleanly as soon as the render finishes).
4. **Run the Print Audit:**
   - Click **Audit** &rarr; **Run in Blender (`Ctrl+Enter`)** to verify every printable mesh is watertight (`non-manifold=0`), has positive outward normals, rests cleanly on `Z >= 0`, and has unscaled `(1.0, 1.0, 1.0)` transforms.
5. **Refine & Send to Plate:**
   - Adjust tolerances in `bpy`, re-render to verify, and click **Send to Plate** on any individual part in the **Scene** tab to drop it onto OrcaSlicer's **Prepare** plate.

---

## 4. Scene Organization: Printable Parts vs. Hardware Mockups

Blender Studio automatically classifies every mesh object in your scene so mockups never get accidentally exported into your printable STLs:

| Category | Classification Rules | Behavior in Blender Studio |
| :--- | :--- | :--- |
| **Printable Meshes** | Any `MESH` object that is **not** a mockup. Recommended collection: `CAD_Printable_Parts`. | Listed at the top of the **Scene** tab with `X × Y × Z mm` bounding box, face count, **Export STL**, **Send to Plate**, and included in **Export & Plate All** and **Audit**. |
| **Hardware Mockups** | `MESH` objects whose name starts with `M_`, `Mockup_`, `Vis_`, `Plug_`, `Ground_Shadow`, `Drawing_Paper`, or `Floor` — **or** placed solely in `Hardware_Mockups` / `Cable_Plug_Mockups` collections. | Grouped under **Hardware Mockups** (toggleable with **Show / Hide**), excluded from STL exports and manifold audits, and shown in **Hero / Exploded** inspection renders. |

---

## 5. Interactive `bpy` Scripting & Built-in Macros

The right column acts as a live Python REPL attached to Blender's main thread. Press **`Ctrl+Enter`** at any time to execute the editor buffer.

### Built-in Macros
- **`Rig`:** Adds or updates 3-point sun lighting (`KeyLight`, `FillLight`, `RimLight`) and `RenderCam` pointed at your assembly center.
- **`+Magnet`:** Inserts a `Ø6.25 × 2.20 mm` press-fit cutout/mockup cylinder (`Mockup_Mag_N`) for standard `6×2 mm` neodymium discs.
- **`+M3`:** Inserts an M3 clearance shaft (`Ø3.4 mm`) + DIN 934 captive hex nut pocket (`5.65 mm` across flats) mockup (`M_Screw_M3_N`).
- **`+SG90`:** Inserts an accurate `23.0 × 12.5 × 22.0 mm` TowerPro SG90 9g micro-servo mockup (`M_SG90_N`).
- **`Audit`:** Inspects every printable mesh using `bmesh` and logs `dims`, `min_Z`, non-manifold edges, loose vertices, inverted normals, and unapplied scale.

> **Blender 5.x `bmesh.ops.bevel` Tip:** In Blender 5.x, `bmesh.ops.bevel(...)` defaults `profile=0.0` (which creates a concave notch!). Always pass `profile=0.5` when creating rounded corners or chamfers programmatically.

---

## 6. Using the AI CAD Assistant (Ollama, Gemini & OpenAI-Compatible)

Every AI prompt automatically includes a structured summary of your active scene (`.blend` filename, Blender version, every printable mesh's exact `X × Y × Z mm` dimensions and world bounding box, plus all hardware mockups) so the LLM writes targeted `bpy` code.

### Supported Backends
1. **Local Ollama (Private & Free):**
   - Select `Ollama: qwen2.5-coder:7b` for fast `bpy` generation, or `Ollama: qwen3.5-9b-vision` when attaching a viewport render.
   - Configure primary/fallback URLs in **Settings** (defaults to `http://127.0.0.1:11435` and `http://127.0.0.1:11434`).
2. **Google Gemini:**
   - Open **Settings** &rarr; paste your **Gemini API Key** &rarr; choose `Gemini 3 Flash (Fast)`, `Gemini 3.5 Flash-Lite`, `Gemini 3.5 Flash`, `Gemini 3.6 Flash`, `Gemini 3.8 Flash`, or `Gemini 3.1 Flash-Lite` (with automatic failover if a model experiences demand spikes or is deprecated).
3. **OpenAI-Compatible (OpenAI, OpenRouter, LM Studio, DeepSeek, vLLM):**
   - Open **Settings** &rarr; configure **OpenAI-Compatible Base URL** (e.g., `http://127.0.0.1:1234/v1` for LM Studio or `https://openrouter.ai/api/v1`), **Model**, and **API Key** &rarr; pick `OpenAI-Compatible (Settings)` in the dropdown.

### Attaching Pictures & Reference Documents
Beyond live scene geometry, you can supply rich multi-modal context directly to the AI CAD Assistant using three equivalent methods:
1. Click **`+ Image / Doc`** below the prompt box to select one or more local files.
2. **Drag and drop** files directly onto the prompt box.
3. **Paste (`Ctrl+V`)** screenshots or copied images directly into the prompt textarea.

Each attachment appears as a removable chip showing a thumbnail (or document badge), filename, and size (per-file limit `15 MB`):
- **Reference Pictures (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`):**
  - Ideal for hand-drawn dimensional sketches, caliper measurements on real hardware, PCB top-down photos, or multi-view screenshots.
  - Images larger than `1536 px` are automatically downscaled client-side to keep payloads responsive for both cloud APIs and 8 GB VRAM local vision models.
  - If you leave Ollama on `qwen2.5-coder:7b` while images are attached, Blender Studio automatically checks your local Ollama tags and upgrades the request to an installed vision model (e.g., `lukey03/qwen3.5-9b-abliterated-vision:latest`) with a console note.
- **Reference Documents (`.md`, `.txt`, `.json`, `.csv`, `.py`, `.scad`, `.yaml`, `.yml`, `.xml`, `.svg`, `.html`, `.pdf`, `.log`, `.ini`, `.toml`):**
  - Great for design specs (`implementation.md`), dimension/BOM tables (`.csv`, `.json`), hardware datasheets (`.pdf` text extracted with built-in stdlib `zlib` `FlateDecode` support), vector outlines (`.svg`), or existing OpenSCAD / Python CAD scripts to port into `bpy`.
  - Document text is injected cleanly into the prompt (up to `24,000` chars per document / `64,000` chars total), so text-only coder models (`qwen2.5-coder:7b`) can follow exact specs without needing vision.

### Options
- **`Auto-run`:** Runs the generated script on `:9999` (with automatic traceback recovery using the pre-loaded `cad` module) and renders a fresh **Hero** diagnostic preview. For local Ollama with attached images, Blender Studio automatically runs a **two-stage Vision &rarr; Coder pipeline** (first extracting dimensions/features visually with the vision model, then generating reliable `cad` / `bpy` Python with `qwen2.5-coder:7b`).
- **`+Viewport`:** Sends the currently displayed viewport PNG alongside your prompt and uploaded files so multimodal vision models can inspect alignment, overhangs, or cosmetic tweaks visually.

---

## 7. Exporting STLs & Direct "Send to Plate" Handoff

- **Per-Part Export:** Click **Export STL** to write `<ObjectName>.stl` to your 3D designs folder, or **Send to Plate** to export and immediately load the STL onto OrcaSlicer's active **Prepare** build plate.
- **Batch Export:** Click **Export & Plate All** to export all printable meshes (excluding hardware mockups) into `<ProjectName>.stl` and send it to the plate.
- **STL / 3MF Library:** Any pre-existing `.stl`, `.3mf`, `.obj`, `.step`, or `.amf` files in your 3D designs folder can also be loaded straight onto the active plate from the **STL / 3MF** tab.
- **Single-Instance IPC:**
  - **Linux:** Speaks directly to OrcaSlicer's session D-Bus (`AnotherInstance`) socket using `<OrcaDataDir>/cache/<HASH>.lock`.
  - **Windows:** Posts `WM_COPYDATA` directly to this process's `wxWindowNR` handle.
  - **macOS:** Uses `open -a` bundle forwarding.

---

## 8. Remote / Tailscale Blender & External AI Agent Setup

Because `server_bootstrap.py` binds to `0.0.0.0:9999`, you can run Blender on a powerful workstation (e.g., an Ubuntu GPU box) and drive it from OrcaSlicer on your laptop over LAN or Tailscale:

1. On the **Blender host machine**, run:
   ```bash
   blender --background --python standalone/server_bootstrap.py
   ```
   *(Or open `standalone/server_bootstrap.py` inside Blender's Scripting tab and click **Run Script** for interactive GUI mode.)*
2. In **OrcaSlicer &rarr; Blender Studio &rarr; Settings**, change **Server Host** from `127.0.0.1` to your host machine's LAN or Tailscale IP (`100.x.y.z`), and click **Save Settings**.
3. External coding agents (VS Code Continue, Codex, Antigravity, Python scripts) can also share the exact same `:9999` socket simultaneously — edits made by an external script show up in the **Blender Studio** tab when you click **Refresh** or render a camera preset!

---

## 9. Troubleshooting & FAQ

- **The tab says `Disconnected (:9999)`:**
  - Click **Start Headless Server** (or **Launch Blender GUI**) in the top bar. If Blender doesn't start, open **Settings** and verify **Blender Executable** points to your actual `blender` / `blender.exe` binary.
- **Render viewport is completely dark or blank:**
  - Click the **`Rig`** macro button and press **`Ctrl+Enter`** to add 3-point lighting, or verify your printable meshes aren't hidden from render (`hide_render = True`).
- **A corner bevel or chamfer looks inverted (concave notch):**
  - In Blender 5.x, `bmesh.ops.bevel` defaults to `profile=0.0`. Explicitly pass `profile=0.5` for circular convex fillets.
- **Boolean modifier leaves non-manifold edges:**
  - Avoid exact coplanar faces between cutter and target (extend boolean cutters `0.2–1.0 mm` past outer faces), use `mod.solver = "EXACT"`, and verify with the **`Audit`** macro.
- **Ollama times out or fails on a small GPU (e.g. 8 GB VRAM):**
  - Use `qwen2.5-coder:7b` for code generation, and enable Flash Attention (`OLLAMA_FLASH_ATTENTION=1`) when running vision models alongside OrcaSlicer's 3D viewport.
- **OrcaSlicer prompts for permissions or blocks a path:**
  - On first use of a new external folder or remote IP, OrcaSlicer asks you to approve the grant. Never store plugin scratch files in folders named `config` or `secret` — OrcaSlicer's Python audit hook blocks those path keywords by design.
