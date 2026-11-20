# Changelog

All notable changes to the **Blender Studio** plugin for OrcaSlicer are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
