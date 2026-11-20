#!/usr/bin/env python3
"""Build and package distribution assets for Blender Studio."""
import hashlib
import math
import os
from pathlib import Path
import py_compile
import re
import shutil
import sys
import zipfile
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.generate_changelog import generate_changelog
from scripts.generate_media import generate_all_media
SRC_PY = ROOT / "blender_studio.py"
DIST_DIR = ROOT / "dist"
ASSETS_DIR = ROOT / "assets"


def get_version() -> str:
    content = SRC_PY.read_text(encoding="utf-8")
    m = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        return m.group(1)
    return "1.2.6"


def render_icons() -> None:
    scale = 4
    S = 512 * scale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    pad = 18 * scale
    rad = 108 * scale
    rect_box = [pad, pad, S - pad, S - pad]

    base_mask = Image.new("L", (S, S), 0)
    d_mask = ImageDraw.Draw(base_mask)
    d_mask.rounded_rectangle(rect_box, radius=rad, fill=255)

    grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d_grad = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        r = int(29 * (1 - t) + 11 * t)
        g = int(34 * (1 - t) + 13 * t)
        b = int(45 * (1 - t) + 17 * t)
        d_grad.line([(0, y), (S, y)], fill=(r, g, b, 255))

    img = Image.composite(grad, img, base_mask)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(rect_box, radius=rad, outline=(58, 68, 86, 255), width=int(4 * scale))
    inner_pad = pad + int(5 * scale)
    draw.rounded_rectangle(
        [inner_pad, inner_pad, S - inner_pad, S - inner_pad],
        radius=rad - int(5 * scale),
        outline=(255, 255, 255, 22),
        width=int(2 * scale),
    )

    cx, cy = 256 * scale, 274 * scale
    R = 155 * scale
    cos30 = math.cos(math.radians(30))
    sin30 = math.sin(math.radians(30))

    p_top = (cx, cy - R)
    p_tr = (cx + R * cos30, cy - R * sin30)
    p_br = (cx + R * cos30, cy + R * sin30)
    p_bot = (cx, cy + R)
    p_bl = (cx - R * cos30, cy + R * sin30)
    p_tl = (cx - R * cos30, cy - R * sin30)
    p_mid = (cx, cy)

    # Top Face: OrcaSlicer Teal
    draw.polygon([p_top, p_tr, p_mid, p_tl], fill=(19, 61, 58, 255))
    grid_steps = 4
    for i in range(1, grid_steps):
        f = i / grid_steps
        s1 = (p_top[0] + (p_tr[0] - p_top[0]) * f, p_top[1] + (p_tr[1] - p_top[1]) * f)
        e1 = (p_tl[0] + (p_mid[0] - p_tl[0]) * f, p_tl[1] + (p_mid[1] - p_tl[1]) * f)
        draw.line([s1, e1], fill=(38, 166, 154, 110), width=int(1.8 * scale))
        s2 = (p_top[0] + (p_tl[0] - p_top[0]) * f, p_top[1] + (p_tl[1] - p_top[1]) * f)
        e2 = (p_tr[0] + (p_mid[0] - p_tr[0]) * f, p_tr[1] + (p_mid[1] - p_tr[1]) * f)
        draw.line([s2, e2], fill=(38, 166, 154, 110), width=int(1.8 * scale))

    # Left Face: Slate CAD
    draw.polygon([p_tl, p_mid, p_bot, p_bl], fill=(22, 28, 38, 255))
    for i in range(1, grid_steps):
        f = i / grid_steps
        s = (p_tl[0] + (p_bl[0] - p_tl[0]) * f, p_tl[1] + (p_bl[1] - p_tl[1]) * f)
        e = (p_mid[0] + (p_bot[0] - p_mid[0]) * f, p_mid[1] + (p_bot[1] - p_mid[1]) * f)
        draw.line([s, e], fill=(74, 96, 125, 80), width=int(1.5 * scale))

    # Right Face: Blender Dark Ember
    draw.polygon([p_tr, p_br, p_bot, p_mid], fill=(42, 28, 18, 255))
    for i in range(1, grid_steps):
        f = i / grid_steps
        s = (p_tr[0] + (p_br[0] - p_tr[0]) * f, p_tr[1] + (p_br[1] - p_tr[1]) * f)
        e = (p_mid[0] + (p_bot[0] - p_mid[0]) * f, p_mid[1] + (p_bot[1] - p_mid[1]) * f)
        draw.line([s, e], fill=(232, 125, 13, 80), width=int(1.5 * scale))

    # Cube Wireframe Accents
    draw.line([p_top, p_tl, p_bl, p_bot], fill=(38, 166, 154, 255), width=int(4.5 * scale))
    draw.line([p_top, p_tr, p_br, p_bot], fill=(232, 125, 13, 255), width=int(4.5 * scale))
    draw.line([p_mid, p_top], fill=(78, 226, 208, 240), width=int(3.5 * scale))
    draw.line([p_mid, p_bl], fill=(59, 130, 246, 240), width=int(3.5 * scale))
    draw.line([p_mid, p_br], fill=(255, 167, 51, 240), width=int(3.5 * scale))

    # Nozzle Tip
    nz_x, nz_y = p_top[0], p_top[1] - 18 * scale
    nz_pts = [
        (nz_x - 14 * scale, nz_y - 24 * scale),
        (nz_x + 14 * scale, nz_y - 24 * scale),
        (nz_x + 8 * scale, nz_y - 6 * scale),
        (nz_x + 3 * scale, nz_y),
        (nz_x - 3 * scale, nz_y),
        (nz_x - 8 * scale, nz_y - 6 * scale),
    ]
    draw.polygon(nz_pts, fill=(229, 165, 10, 255), outline=(255, 213, 79, 255), width=int(2 * scale))
    draw.ellipse([p_top[0] - 6 * scale, p_top[1] - 6 * scale, p_top[0] + 6 * scale, p_top[1] + 6 * scale], fill=(78, 226, 208, 255))

    # Blender Spiral & Eye
    ey_cx, ey_cy = cx, cy
    for ang, length in [(-125, 82 * scale), (-40, 86 * scale), (92, 80 * scale)]:
        rad_a = math.radians(ang)
        x2 = ey_cx + length * math.cos(rad_a)
        y2 = ey_cy + length * math.sin(rad_a)
        draw.line([(ey_cx, ey_cy), (x2, y2)], fill=(245, 130, 15, 255), width=int(11 * scale))
        draw.ellipse([x2 - 10 * scale, y2 - 10 * scale, x2 + 10 * scale, y2 + 10 * scale], fill=(255, 167, 51, 255))

    r_out = 44 * scale
    draw.ellipse([ey_cx - r_out, ey_cy - r_out, ey_cx + r_out, ey_cy + r_out], fill=(232, 125, 13, 255), outline=(255, 195, 80, 255), width=int(3 * scale))
    r_mid = 29 * scale
    draw.ellipse([ey_cx - r_mid, ey_cy - r_mid, ey_cx + r_mid, ey_cy + r_mid], fill=(18, 21, 27, 255), outline=(255, 167, 51, 220), width=int(2 * scale))
    r_core = 18 * scale
    draw.ellipse([ey_cx - r_core, ey_cy - r_core, ey_cx + r_core, ey_cy + r_core], fill=(38, 166, 154, 255), outline=(92, 229, 214, 255), width=int(2 * scale))
    draw.ellipse([ey_cx - 10 * scale, ey_cy - 10 * scale, ey_cx - 2 * scale, ey_cy - 2 * scale], fill=(255, 255, 255, 255))

    # Multi-resolution export
    sizes = [(512, "icon_512x512.png"), (256, "icon_256x256.png"), (128, "icon_128x128.png"), (64, "icon_64x64.png"), (32, "icon_32x32.png")]
    for sz, fn in sizes:
        resized = img.resize((sz, sz), Image.Resampling.LANCZOS)
        for dest_dir in (ASSETS_DIR, DIST_DIR):
            resized.save(dest_dir / fn, "PNG", optimize=True)
            if sz == 512:
                resized.save(dest_dir / "icon.png", "PNG", optimize=True)


def build() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    version = get_version()
    print(f"Building Blender Studio v{version} distribution...")

    # 0. Synchronize changelog and generate showcase media
    generate_changelog()
    generate_all_media()
    print("  [OK] Synchronized CHANGELOG.md, release notes, and showcase media")

    # 1. Compile check source
    py_compile.compile(str(SRC_PY), doraise=True)
    print("  [OK] Syntax check passed on blender_studio.py")

    # 2. Render icons
    render_icons()
    print("  [OK] Rendered all icon resolutions (512, 256, 128, 64, 32)")

    # 3. Create target-OS suffixed release files
    targets = [
        "blender_studio_any.py",
        "blender_studio_linux_x86_64.py",
        "blender_studio_win_x86_64.py",
        "blender_studio_macosx_arm64.py",
        "blender_studio_macosx_x86_64.py",
    ]
    for t in targets:
        dest = DIST_DIR / t
        shutil.copyfile(SRC_PY, dest)
        py_compile.compile(str(dest), doraise=True)
        print(f"  [OK] Created {t} ({dest.stat().st_size:,} bytes)")

    # 4. Create release bundle zip
    zip_path = DIST_DIR / f"blender_studio_v{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(SRC_PY, arcname="blender_studio.py")
        zf.write(DIST_DIR / "blender_studio_any.py", arcname="blender_studio_any.py")
        zf.write(DIST_DIR / "icon.png", arcname="icon.png")
        zf.write(DIST_DIR / "icon.svg", arcname="icon.svg")
        for m in ("hub_hero_banner.png", "workflow_pipeline.png", "diagnostic_views_showcase.png"):
            mp = DIST_DIR / m
            if mp.is_file():
                zf.write(mp, arcname=f"media/{m}")
        for doc in ("README.md", "CHANGELOG.md", "LICENSE", "QUICK_REFERENCE.md", "USER_GUIDE.md"):
            p = ROOT / doc
            if p.is_file():
                zf.write(p, arcname=doc)
    print(f"  [OK] Packaged {zip_path.name} ({zip_path.stat().st_size:,} bytes)")

    # 5. Generate SHA256 checksums
    sums_path = DIST_DIR / "SHA256SUMS.txt"
    with open(sums_path, "w", encoding="utf-8") as sf:
        for f in sorted(DIST_DIR.iterdir()):
            if f.name == "SHA256SUMS.txt" or f.is_dir():
                continue
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            sf.write(f"{h}  {f.name}\n")
    print(f"  [OK] Generated {sums_path.name}")
    print("\nBuild complete. All release assets are ready in dist/.")


if __name__ == "__main__":
    build()
