#!/usr/bin/env python3
"""Automatically generate and synchronize CHANGELOG.md and release notes."""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_MD = ROOT / "CHANGELOG.md"
DIST_DIR = ROOT / "dist"
SRC_PY = ROOT / "blender_studio.py"

# Curated release history with detailed categorization
RELEASE_HISTORY: dict[str, dict[str, Any]] = {
    "1.2.8": {
        "date": "2026-11-21",
        "title": "Dynamic AI Model Discovery & Custom Model Manager (Ollama, Gemini, OpenAI)",
        "features": [
            "Dynamic Local Ollama Model Discovery: Automatically queries local Ollama (`/api/tags`) and lists all installed models (`qwen2.5-coder`, `deepseek-coder`, `llama3`, `dolphin-mistral`, `moondream`, etc.) in the AI CAD dropdown.",
            "Interactive `+ Model` & `➕ Add / Try New Model` Dialog: Add any custom local or cloud model on the fly across Local Ollama, Google Gemini, or OpenAI-compatible endpoints (LM Studio, OpenRouter, DeepSeek, vLLM).",
            "Persistent Custom Models: Custom models are saved directly to settings and persist across OrcaSlicer sessions with friendly labels.",
            "Custom Models Manager in Settings: View, organize, and remove experimental custom models in a dedicated settings panel.",
            "Arbitrary OpenAI & Gemini Model Routing: Ensured custom model identifiers selected in the dropdown pass through directly to OpenAI/Gemini chat endpoints without preset restrictions.",
        ],
        "fixes": [
            "Fixed model override precedence in OpenAI chat caller so user-selected custom models take precedence over default settings.",
        ],
    },
    "1.2.7": {
        "date": "2026-11-21",
        "title": "Interactive Model Cutting & Multi-Plate Part Separation (✂ Cut Macro & Modal)",
        "features": [
            "Interactive Cut & Separate Modal (`✂ Cut`): Effortlessly split complex 3D models into watertight pieces or separate loose disconnected shells for independent build plates directly from the mesh sidebar.",
            "Watertight Manifold Bisect & Capping: Advanced planar bisecting with automated hole filling, face normal recalculation, and guaranteed 0 non-manifold edges.",
            "Automatic Z-Floor Grounding & Part Spacing: Automatically aligns cut parts flat against the build bed (Z=0) with configurable spacing (default 25mm) for instant collision-free multi-plate slicing.",
            "Built-in `✂ Cut` Macro (`cut_split`): Instant macro button in the bpy script bar with fallback execution directly in Blender Python editor.",
            "Procedural CAD Helpers: Added `cad.cut_parts(...)` and `cad.separate_loose(...)` to the embedded `cad` library and exposed `bpy.cad`.",
        ],
        "fixes": [
            "Ensured full coordinate transform application (`location=True, rotation=True, scale=True`) prior to bisecting to guarantee 100% accurate planar positioning.",
        ],
    },
    "1.2.6": {
        "date": "2026-11-20",
        "title": "PolyForm Shield License 1.0.0 & Dynamic Metadata Validation",
        "features": [
            "Licensed under PolyForm Shield License 1.0.0 to protect against competitive commercial misuse while keeping full free use for users.",
            "Dedicated GNU GPL v3.0 (`GPL-3.0-or-later`) license for `standalone/server_bootstrap.py` for Blender `bpy` compatibility.",
            "Updated PEP 723 metadata with `PolyForm-Shield-1.0.0` license identifier and dynamic semver validation.",
        ],
        "fixes": [
            "Replaced hardcoded version test assertions with dynamic semver and consistency verification.",
        ],
    },
    "1.2.5": {
        "date": "2026-11-20",
        "title": "Orca Cloud Plugin Hub Certification & Upfront Security Permissions",
        "features": [
            "Declared upfront capabilities via `orca.request_permissions(...)` for a single combined approval dialog on load (`network_socket`, `network_http`, `fs_read`, `fs_readwrite`, `process`, `threading`).",
            "100% Self-Contained Plugin Hub installation: Added **📋 Copy Bootstrap Script** and **💾 Export server_bootstrap.py** buttons inside Settings modal.",
            "Automated GitHub Actions release pipeline triggering on push to `main` with multi-OS distribution assets.",
            "Automatic Changelog generation integrated into build and release pipelines.",
        ],
        "fixes": [
            "Sanitized child process environments with `_ALLOWED_CHILD_ENV_KEYS`, eliminating sensitive `os.environ` credential leakage.",
            "Hardened outbound network requests with `_validate_safe_url` and `_safe_urlopen` restricting calls strictly to HTTP/HTTPS schemes with host validation.",
            "Resolved duplicate target conflict in Orca Cloud Plugin Hub releases.",
        ],
    },
    "1.2.4": {
        "date": "2026-11-18",
        "title": "Procedural CAD Helpers, CAD Reference Attachments, & Geometry Audit",
        "features": [
            "Parametric procedural CAD helper library (`cad.*`): `box`, `rounded_plate`, `cylinder`, `tube`, `strut`, `oriented_box`, `counterbore_hole`, `slot`, `magnet_pocket`, `m3_hole`, `place_on_bed`, and `audit_geometry`.",
            "Multi-format CAD attachments: Attach `.step`, `.stp`, `.3mf`, and `.stl` files in the AI CAD Assistant via `+ File / CAD`, drag-and-drop, or clipboard.",
            "Closed-loop geometry audit: Automatically inspects manifold status, isolated vertices/edges, scale normalization, and bed clearance with AI self-healing.",
            "Multi-resolution official icons (512, 256, 128, 64, 32) and target-OS suffixed release packages (`_any`, `_linux_x86_64`, `_win_x86_64`, `_macosx_arm64`, `_macosx_x86_64`).",
        ],
        "fixes": [
            "Thread-capped local Ollama inference (`num_thread: 4`) to prevent CPU fan noise and system freezes.",
            "Animated progress wheel and pulse bar on AI generation to provide instant visual feedback.",
        ],
    },
    "1.2.3": {
        "date": "2026-11-18",
        "title": "Google Gemini 3.x Migration & High-Performance Fallbacks",
        "features": [
            "Integrated Gemini 3.x Flash (`gemini-3-flash-preview`, `gemini-3.8-flash-preview`) with automatic failover across candidate models.",
        ],
        "fixes": [
            "Replaced retired Gemini 2.5 preview endpoints with current Google AI catalog.",
        ],
    },
    "1.2.2": {
        "date": "2026-11-18",
        "title": "Two-Stage Ollama Vision Pipeline & Server Self-Healing",
        "features": [
            "Two-stage local Ollama architecture: Vision model (`qwen3.5-9b-abliterated-vision`) analyzes reference images/CAD; Coder model (`qwen2.5-coder:7b`) generates clean `bpy` code.",
            "Built-in server self-healing: Automatically diagnoses and fixes `bpy` script exceptions before reporting to the user.",
        ],
    },
    "1.2.1": {
        "date": "2026-11-18",
        "title": "Blender 5.2 LTS Shims & Expanded Geometry Support",
        "features": [
            "Added API compatibility shims for Blender 5.2 LTS and 4.2+ LTS releases.",
            "Added bounding box and clearance inspection for FONT and CURVE geometry types.",
        ],
        "fixes": [
            "Eliminated UI thread blocking during scene audits and enhanced Ollama query throughput.",
        ],
    },
    "1.2.0": {
        "date": "2026-11-18",
        "title": "Multi-Modal AI CAD Assistant",
        "features": [
            "Added image, diagram, and specification document attachment support directly inside the AI CAD assistant prompt box.",
        ],
    },
    "1.1.0": {
        "date": "2026-11-18",
        "title": "Initial Release of Blender Studio for OrcaSlicer",
        "features": [
            "Integrated top-bar OrcaSlicer Pages plugin connecting to Blender via TCP :9999.",
            "Interactive CAD inspection separating printable parts from hardware mockups (`M_*`).",
            "Multi-angle diagnostic EEVEE render studio (Hero, Top, Front, Isometric, Exploded).",
            "One-click binary STL export and direct build plate handoff via D-Bus, Windows WM_COPYDATA, and macOS open.",
        ],
    },
}


def get_current_version() -> str:
    content = SRC_PY.read_text(encoding="utf-8")
    m = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']', content)
    return m.group(1) if m else "1.2.6"


def format_release_markdown(ver: str, info: dict[str, Any]) -> str:
    lines = [f"## [{ver}] - {info.get('date', 'Unknown Date')}"]
    if info.get("title"):
        lines.append(f"### {info['title']}\n")
    if info.get("features"):
        lines.append("#### Added & Enhanced")
        for item in info["features"]:
            lines.append(f"- {item}")
        lines.append("")
    if info.get("fixes"):
        lines.append("#### Fixed & Security")
        for item in info["fixes"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def get_git_commits_since_last_tag() -> list[str]:
    """Retrieve git commits since previous release tag or recent history."""
    try:
        tag_proc = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        last_tag = tag_proc.stdout.strip()
        cmd = ["git", "log", f"{last_tag}..HEAD", "--pretty=format:%s"] if last_tag else ["git", "log", "-n", "10", "--pretty=format:%s"]
        log_proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
        commits = [l.strip() for l in log_proc.stdout.splitlines() if l.strip()]
        return commits if commits else ["Automated update and stability improvements."]
    except Exception:
        return ["Automated maintenance and enhancements."]


def generate_changelog() -> tuple[str, str]:
    """Generate the full CHANGELOG.md and extract the latest version notes."""
    cur_ver = get_current_version()
    
    # If the current version is not explicitly in RELEASE_HISTORY, dynamically build it from commits
    if cur_ver not in RELEASE_HISTORY:
        commits = get_git_commits_since_last_tag()
        features = [c for c in commits if c.startswith(("feat", "add", "ui"))]
        fixes = [c for c in commits if c.startswith(("fix", "sec", "hard", "ci", "test"))]
        if not features and not fixes:
            features = commits
        RELEASE_HISTORY[cur_ver] = {
            "date": datetime.date.today().isoformat(),
            "title": f"Blender Studio v{cur_ver}",
            "features": features or ["Automated front-to-back release pipeline update."],
            "fixes": fixes or ["Maintenance and dependency validation updates."],
        }

    lines = [
        "# Changelog",
        "",
        "All notable changes to the **Blender Studio** plugin for OrcaSlicer are documented in this file.",
        "",
        "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),",
        "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).",
        "",
        "---",
        "",
    ]

    latest_notes = ""
    for ver, info in RELEASE_HISTORY.items():
        rel_md = format_release_markdown(ver, info)
        if ver == cur_ver or not latest_notes:
            latest_notes = rel_md
        lines.append(rel_md)

    full_md = "\n".join(lines)
    CHANGELOG_MD.write_text(full_md, encoding="utf-8")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    (DIST_DIR / "latest_changelog.md").write_text(latest_notes, encoding="utf-8")
    (DIST_DIR / "CHANGELOG.txt").write_text(full_md, encoding="utf-8")

    return full_md, latest_notes


if __name__ == "__main__":
    if "--version-only" in sys.argv:
        print(get_current_version())
        sys.exit(0)

    _, latest = generate_changelog()
    print("✓ Successfully generated CHANGELOG.md and dist/latest_changelog.md")
    print(f"\n--- Latest Release Notes (v{get_current_version()}) ---")
    print(latest)
