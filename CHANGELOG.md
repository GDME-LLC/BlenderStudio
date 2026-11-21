# Changelog

All notable changes to the **Blender Studio** plugin for OrcaSlicer are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.8] - 2026-11-21
### Dynamic AI Model Discovery & Custom Model Manager (Ollama, Gemini, OpenAI)

#### Added & Enhanced
- Dynamic Local Ollama Model Discovery: Automatically queries local Ollama (`/api/tags`) and lists all installed models (`qwen2.5-coder`, `deepseek-coder`, `llama3`, `dolphin-mistral`, `moondream`, etc.) in the AI CAD dropdown.
- Interactive `+ Model` & `➕ Add / Try New Model` Dialog: Add any custom local or cloud model on the fly across Local Ollama, Google Gemini, or OpenAI-compatible endpoints (LM Studio, OpenRouter, DeepSeek, vLLM).
- Persistent Custom Models: Custom models are saved directly to settings and persist across OrcaSlicer sessions with friendly labels.
- Custom Models Manager in Settings: View, organize, and remove experimental custom models in a dedicated settings panel.
- Arbitrary OpenAI & Gemini Model Routing: Ensured custom model identifiers selected in the dropdown pass through directly to OpenAI/Gemini chat endpoints without preset restrictions.

#### Fixed & Security
- Fixed model override precedence in OpenAI chat caller so user-selected custom models take precedence over default settings.

## [1.2.7] - 2026-11-21
### Interactive Model Cutting & Multi-Plate Part Separation (✂ Cut Macro & Modal)

#### Added & Enhanced
- Interactive Cut & Separate Modal (`✂ Cut`): Effortlessly split complex 3D models into watertight pieces or separate loose disconnected shells for independent build plates directly from the mesh sidebar.
- Watertight Manifold Bisect & Capping: Advanced planar bisecting with automated hole filling, face normal recalculation, and guaranteed 0 non-manifold edges.
- Automatic Z-Floor Grounding & Part Spacing: Automatically aligns cut parts flat against the build bed (Z=0) with configurable spacing (default 25mm) for instant collision-free multi-plate slicing.
- Built-in `✂ Cut` Macro (`cut_split`): Instant macro button in the bpy script bar with fallback execution directly in Blender Python editor.
- Procedural CAD Helpers: Added `cad.cut_parts(...)` and `cad.separate_loose(...)` to the embedded `cad` library and exposed `bpy.cad`.

#### Fixed & Security
- Ensured full coordinate transform application (`location=True, rotation=True, scale=True`) prior to bisecting to guarantee 100% accurate planar positioning.

## [1.2.6] - 2026-11-20
### PolyForm Shield License 1.0.0 & Dynamic Metadata Validation

#### Added & Enhanced
- Licensed under PolyForm Shield License 1.0.0 to protect against competitive commercial misuse while keeping full free use for users.
- Dedicated GNU GPL v3.0 (`GPL-3.0-or-later`) license for `standalone/server_bootstrap.py` for Blender `bpy` compatibility.
- Updated PEP 723 metadata with `PolyForm-Shield-1.0.0` license identifier and dynamic semver validation.

#### Fixed & Security
- Replaced hardcoded version test assertions with dynamic semver and consistency verification.

## [1.2.5] - 2026-11-20
### Orca Cloud Plugin Hub Certification & Upfront Security Permissions

#### Added & Enhanced
- Declared upfront capabilities via `orca.request_permissions(...)` for a single combined approval dialog on load (`network_socket`, `network_http`, `fs_read`, `fs_readwrite`, `process`, `threading`).
- 100% Self-Contained Plugin Hub installation: Added **📋 Copy Bootstrap Script** and **💾 Export server_bootstrap.py** buttons inside Settings modal.
- Automated GitHub Actions release pipeline triggering on push to `main` with multi-OS distribution assets.
- Automatic Changelog generation integrated into build and release pipelines.

#### Fixed & Security
- Sanitized child process environments with `_ALLOWED_CHILD_ENV_KEYS`, eliminating sensitive `os.environ` credential leakage.
- Hardened outbound network requests with `_validate_safe_url` and `_safe_urlopen` restricting calls strictly to HTTP/HTTPS schemes with host validation.
- Resolved duplicate target conflict in Orca Cloud Plugin Hub releases.

## [1.2.4] - 2026-11-18
### Procedural CAD Helpers, CAD Reference Attachments, & Geometry Audit

#### Added & Enhanced
- Parametric procedural CAD helper library (`cad.*`): `box`, `rounded_plate`, `cylinder`, `tube`, `strut`, `oriented_box`, `counterbore_hole`, `slot`, `magnet_pocket`, `m3_hole`, `place_on_bed`, and `audit_geometry`.
- Multi-format CAD attachments: Attach `.step`, `.stp`, `.3mf`, and `.stl` files in the AI CAD Assistant via `+ File / CAD`, drag-and-drop, or clipboard.
- Closed-loop geometry audit: Automatically inspects manifold status, isolated vertices/edges, scale normalization, and bed clearance with AI self-healing.
- Multi-resolution official icons (512, 256, 128, 64, 32) and target-OS suffixed release packages (`_any`, `_linux_x86_64`, `_win_x86_64`, `_macosx_arm64`, `_macosx_x86_64`).

#### Fixed & Security
- Thread-capped local Ollama inference (`num_thread: 4`) to prevent CPU fan noise and system freezes.
- Animated progress wheel and pulse bar on AI generation to provide instant visual feedback.

## [1.2.3] - 2026-11-18
### Google Gemini 3.x Migration & High-Performance Fallbacks

#### Added & Enhanced
- Integrated Gemini 3.x Flash (`gemini-3-flash-preview`, `gemini-3.8-flash-preview`) with automatic failover across candidate models.

#### Fixed & Security
- Replaced retired Gemini 2.5 preview endpoints with current Google AI catalog.

## [1.2.2] - 2026-11-18
### Two-Stage Ollama Vision Pipeline & Server Self-Healing

#### Added & Enhanced
- Two-stage local Ollama architecture: Vision model (`qwen3.5-9b-abliterated-vision`) analyzes reference images/CAD; Coder model (`qwen2.5-coder:7b`) generates clean `bpy` code.
- Built-in server self-healing: Automatically diagnoses and fixes `bpy` script exceptions before reporting to the user.

## [1.2.1] - 2026-11-18
### Blender 5.2 LTS Shims & Expanded Geometry Support

#### Added & Enhanced
- Added API compatibility shims for Blender 5.2 LTS and 4.2+ LTS releases.
- Added bounding box and clearance inspection for FONT and CURVE geometry types.

#### Fixed & Security
- Eliminated UI thread blocking during scene audits and enhanced Ollama query throughput.

## [1.2.0] - 2026-11-18
### Multi-Modal AI CAD Assistant

#### Added & Enhanced
- Added image, diagram, and specification document attachment support directly inside the AI CAD assistant prompt box.

## [1.1.0] - 2026-11-18
### Initial Release of Blender Studio for OrcaSlicer

#### Added & Enhanced
- Integrated top-bar OrcaSlicer Pages plugin connecting to Blender via TCP :9999.
- Interactive CAD inspection separating printable parts from hardware mockups (`M_*`).
- Multi-angle diagnostic EEVEE render studio (Hero, Top, Front, Isometric, Exploded).
- One-click binary STL export and direct build plate handoff via D-Bus, Windows WM_COPYDATA, and macOS open.
