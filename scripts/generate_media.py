#!/usr/bin/env python3
"""Generate high-resolution storefront banners, workflow diagrams, and showcase media for Orca Plugin Hub & GitHub."""
from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DIST = ROOT / "dist"

FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"


def get_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def create_hub_hero_banner() -> None:
    W, H = 1280, 640
    img = Image.new("RGBA", (W, H), (14, 17, 23, 255))
    draw = ImageDraw.Draw(img)

    # 1. Subtle radial ambient gradient
    for y in range(H):
        for x in range(0, W, 4):
            dx = (x - W * 0.3) / W
            dy = (y - H * 0.5) / H
            dist = math.sqrt(dx * dx + dy * dy)
            alpha = max(0.0, 1.0 - dist * 1.4)
            r = int(14 + 18 * alpha)
            g = int(17 + 36 * alpha)
            b = int(23 + 46 * alpha)
            draw.rectangle([x, y, x + 3, y], fill=(r, g, b, 255))

    # 2. Isometric grid lines
    grid_color = (0, 174, 157, 18)
    for x in range(-W, W * 2, 60):
        draw.line([(x, 0), (x + H, H)], fill=grid_color, width=1)
        draw.line([(x, H), (x + H, 0)], fill=grid_color, width=1)

    # 3. Blender Studio Icon (top-left)
    icon_path = ASSETS / "icon_128x128.png"
    if icon_path.is_file():
        icon_img = Image.open(icon_path).convert("RGBA")
        img.paste(icon_img, (70, 60), icon_img)

    f_title = get_font(FONT_BOLD, 46)
    f_sub = get_font(FONT_REG, 20)
    f_badge = get_font(FONT_BOLD, 13)
    f_body = get_font(FONT_REG, 15)

    # Title & Tagline
    draw.text((220, 65), "BLENDER STUDIO", font=f_title, fill=(240, 244, 249, 255))
    draw.text((222, 122), "Live Procedural CAD & Diagnostic EEVEE Studio for OrcaSlicer", font=f_sub, fill=(0, 210, 190, 255))

    # Compatibility tag
    draw.rounded_rectangle([222, 160, 480, 190], radius=15, fill=(30, 36, 48, 200), outline=(0, 174, 157, 120), width=1)
    draw.text((236, 166), "Official Community Plugin  v1.2.5", font=f_badge, fill=(167, 243, 208, 255))

    # Feature Bullet Chips (left column)
    chips = [
        ("⚡ Live TCP :9999 Bridge", "Connects OrcaSlicer to Blender 4.2+ / 5.x LTS seamlessly"),
        ("🤖 AI CAD Assistant", "Gemini 3.8 Flash & Local Ollama vision/coder with closed-loop audit"),
        ("📐 Parametric cad.* Helpers", "High-level procedural primitives: boxes, rounded plates, magnet pockets, m3"),
        ("🔍 Diagnostic EEVEE Viewport", "Hero, Top, Front, Isometric, Exploded & Scene Cam inspection"),
        ("🚀 One-Click Send to Plate", "Watertight binary STL export straight into active OrcaSlicer prepare plate"),
    ]

    y_chip = 220
    for title, desc in chips:
        draw.rounded_rectangle([70, y_chip, 590, y_chip + 64], radius=8, fill=(24, 29, 40, 220), outline=(255, 255, 255, 22), width=1)
        draw.text((86, y_chip + 12), title, font=f_badge, fill=(255, 178, 64, 255) if "AI" in title or "Parametric" in title else (0, 220, 200, 255))
        draw.text((86, y_chip + 34), desc, font=f_body, fill=(160, 174, 192, 255))
        y_chip += 76

    # Inset Hero & Exploded Render Previews (right column)
    hero_path = ASSETS / "hero_render.png"
    exploded_path = ASSETS / "exploded_render.png"

    rx = 640
    if hero_path.is_file():
        h_img = Image.open(hero_path).convert("RGBA")
        h_thumb = h_img.resize((560, 315), Image.Resampling.LANCZOS)
        # Drop shadow & card border
        draw.rounded_rectangle([rx - 6, 60 - 6, rx + 566, 60 + 321], radius=12, fill=(12, 14, 18, 255), outline=(0, 174, 157, 100), width=2)
        img.paste(h_thumb, (rx, 60))
        # Label badge
        draw.rounded_rectangle([rx + 14, 74, rx + 240, 104], radius=6, fill=(14, 18, 24, 230), outline=(255, 255, 255, 40), width=1)
        draw.text((rx + 24, 80), "✨ Hero Diagnostic Render (1280x720)", font=get_font(FONT_BOLD, 11), fill=(240, 244, 249, 255))

    if exploded_path.is_file():
        e_img = Image.open(exploded_path).convert("RGBA")
        e_thumb = e_img.resize((560, 220), Image.Resampling.LANCZOS)
        # Crop middle portion for clean exploded preview
        e_crop = e_thumb.crop((0, 20, 560, 220))
        draw.rounded_rectangle([rx - 6, 395 - 6, rx + 566, 395 + 186], radius=12, fill=(12, 14, 18, 255), outline=(234, 118, 0, 100), width=2)
        img.paste(e_crop, (rx, 395))
        # Label badge
        draw.rounded_rectangle([rx + 14, 407, rx + 265, 437], radius=6, fill=(14, 18, 24, 230), outline=(255, 255, 255, 40), width=1)
        draw.text((rx + 24, 413), "💥 Exploded Hardware Mockups (M_*)", font=get_font(FONT_BOLD, 11), fill=(255, 178, 64, 255))

    out_path = ASSETS / "hub_hero_banner.png"
    img.save(out_path, "PNG", optimize=True)
    img.save(DIST / "hub_hero_banner.png", "PNG", optimize=True)
    print(f"  [OK] Generated {out_path.name} ({out_path.stat().st_size:,} bytes)")


def create_workflow_diagram() -> None:
    W, H = 1200, 440
    img = Image.new("RGBA", (W, H), (14, 17, 23, 255))
    draw = ImageDraw.Draw(img)

    f_title = get_font(FONT_BOLD, 26)
    f_sub = get_font(FONT_REG, 15)
    f_card_h = get_font(FONT_BOLD, 14)
    f_card_b = get_font(FONT_REG, 12)

    # Title
    draw.text((50, 36), "Blender Studio Workflow Pipeline", font=f_title, fill=(240, 244, 249, 255))
    draw.text((50, 72), "From reference specs & AI prompting to live EEVEE verification and OrcaSlicer build plate delivery.", font=f_sub, fill=(0, 210, 190, 255))

    steps = [
        ("1. Reference Inputs", "Attach CAD files (.step, .3mf, .stl), reference photos, or spec documents (.md, .pdf) in AI prompt.", (0, 174, 157)),
        ("2. AI CAD Assistant", "Gemini 3.8 Flash / Ollama Vision & Coder generates parametric cad.* & bpy script.", (234, 118, 0)),
        ("3. EEVEE Viewport", "One-click Hero, Top, Front, Isometric, Exploded & Scene Cam diagnostic renders.", (59, 130, 246)),
        ("4. Geometry Audit", "Closed-loop check for manifold status, single shell, scale normalization, and bed floor clearance.", (168, 85, 247)),
        ("5. Send to Plate", "One-click binary STL export and direct build plate delivery via D-Bus / WM_COPYDATA.", (34, 197, 94)),
    ]

    card_w = 208
    card_h = 240
    y_card = 130
    x_start = 50

    for i, (title, desc, color) in enumerate(steps):
        cx = x_start + i * (card_w + 20)
        # Card body
        draw.rounded_rectangle([cx, y_card, cx + card_w, y_card + card_h], radius=10, fill=(22, 26, 36, 240), outline=(*color, 120), width=2)
        # Step tag
        draw.rounded_rectangle([cx + 12, y_card + 14, cx + card_w - 12, y_card + 42], radius=6, fill=(*color, 35), outline=(*color, 180), width=1)
        draw.text((cx + 20, y_card + 20), title, font=f_card_h, fill=(255, 255, 255, 255))

        # Description text wrapping
        words = desc.split()
        lines = []
        cur_line = []
        for w in words:
            if len(" ".join(cur_line + [w])) > 24:
                lines.append(" ".join(cur_line))
                cur_line = [w]
            else:
                cur_line.append(w)
        if cur_line:
            lines.append(" ".join(cur_line))

        y_txt = y_card + 62
        for line in lines:
            draw.text((cx + 16, y_txt), line, font=f_card_b, fill=(180, 192, 206, 255))
            y_txt += 20

        # Connecting Arrow to next step
        if i < len(steps) - 1:
            ax = cx + card_w + 4
            ay = y_card + card_h // 2
            draw.text((ax, ay - 12), "➜", font=get_font(FONT_BOLD, 18), fill=(0, 210, 190, 180))

    out_path = ASSETS / "workflow_pipeline.png"
    img.save(out_path, "PNG", optimize=True)
    img.save(DIST / "workflow_pipeline.png", "PNG", optimize=True)
    print(f"  [OK] Generated {out_path.name} ({out_path.stat().st_size:,} bytes)")


def create_diagnostic_views_showcase() -> None:
    W, H = 1200, 560
    img = Image.new("RGBA", (W, H), (14, 17, 23, 255))
    draw = ImageDraw.Draw(img)

    f_title = get_font(FONT_BOLD, 26)
    f_sub = get_font(FONT_REG, 15)
    f_badge = get_font(FONT_BOLD, 12)

    draw.text((50, 30), "EEVEE Diagnostic Inspection: Hero vs. Exploded Views", font=f_title, fill=(240, 244, 249, 255))
    draw.text((50, 66), "Inspect real mm clearances between 3D printable CAD parts and reference hardware mockups (screws, magnets, PCBs, USB ports).", font=f_sub, fill=(0, 210, 190, 255))

    hero_path = ASSETS / "hero_render.png"
    exploded_path = ASSETS / "exploded_render.png"

    if hero_path.is_file() and exploded_path.is_file():
        h_img = Image.open(hero_path).convert("RGBA").resize((530, 360), Image.Resampling.LANCZOS)
        e_img = Image.open(exploded_path).convert("RGBA").resize((530, 360), Image.Resampling.LANCZOS)

        # Left Card (Hero)
        draw.rounded_rectangle([45, 115, 45 + 540, 115 + 370], radius=12, fill=(12, 14, 18, 255), outline=(0, 174, 157, 140), width=2)
        img.paste(h_img, (50, 120))
        draw.rounded_rectangle([65, 135, 330, 168], radius=6, fill=(14, 18, 24, 230), outline=(0, 174, 157, 180), width=1)
        draw.text((78, 142), "Hero View · Printable CAD Assembled", font=f_badge, fill=(167, 243, 208, 255))

        # Right Card (Exploded)
        draw.rounded_rectangle([615, 115, 615 + 540, 115 + 370], radius=12, fill=(12, 14, 18, 255), outline=(234, 118, 0, 140), width=2)
        img.paste(e_img, (620, 120))
        draw.rounded_rectangle([635, 135, 930, 168], radius=6, fill=(14, 18, 24, 230), outline=(234, 118, 0, 180), width=1)
        draw.text((648, 142), "Exploded View · Hardware Mockup Audit", font=f_badge, fill=(255, 190, 90, 255))

        # Bottom feature highlights
        draw.text((50, 510), "✔ Metric: 1 Blender Unit = 1.0 mm", font=f_sub, fill=(160, 174, 192, 255))
        draw.text((380, 510), "✔ Non-printable parts prefixed with M_* or Mockup_*", font=f_sub, fill=(160, 174, 192, 255))
        draw.text((790, 510), "✔ One-click binary STL handoff to OrcaSlicer plate", font=f_sub, fill=(160, 174, 192, 255))

    out_path = ASSETS / "diagnostic_views_showcase.png"
    img.save(out_path, "PNG", optimize=True)
    img.save(DIST / "diagnostic_views_showcase.png", "PNG", optimize=True)
    print(f"  [OK] Generated {out_path.name} ({out_path.stat().st_size:,} bytes)")


def generate_all_media() -> None:
    print("Generating storefront and showcase media for Blender Studio...")
    ASSETS.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    create_hub_hero_banner()
    create_workflow_diagram()
    create_diagnostic_views_showcase()
    print("All media assets generated successfully in assets/ and dist/.")


if __name__ == "__main__":
    generate_all_media()
