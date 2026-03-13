"""
AutoTube v2 — Thumbnail Generator
Creates a YouTube-style thumbnail (1280x720) using Pillow.
No external APIs needed.
"""

import textwrap
from pathlib import Path


def create_thumbnail(
    title: str,
    output_path: Path,
    background_image: str = None,
    width: int = 1280,
    height: int = 720
) -> str:
    """
    Generate a YouTube thumbnail with title text overlay.

    Args:
        title:            video title text
        output_path:      where to save thumbnail (PNG)
        background_image: optional path to a background image
        width, height:    thumbnail dimensions

    Returns:
        path to saved thumbnail
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
    except ImportError:
        raise RuntimeError("Pillow not installed. Run: pip install Pillow")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Background ─────────────────────────────────────────────────────────────
    if background_image and Path(background_image).exists():
        bg = Image.open(background_image).convert("RGB")
        # Crop to 16:9
        bg_ratio = bg.width / bg.height
        target   = width / height
        if bg_ratio > target:
            new_w = int(bg.height * target)
            x     = (bg.width - new_w) // 2
            bg    = bg.crop((x, 0, x + new_w, bg.height))
        else:
            new_h = int(bg.width / target)
            y     = (bg.height - new_h) // 2
            bg    = bg.crop((0, y, bg.width, y + new_h))
        bg = bg.resize((width, height), Image.LANCZOS)
        # Darken + blur for text legibility
        bg = ImageEnhance.Brightness(bg).enhance(0.45)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=3))
    else:
        # Gradient background (dark blue → dark purple)
        bg = Image.new("RGB", (width, height), "#0d0d1a")
        draw_bg = ImageDraw.Draw(bg)
        for y in range(height):
            ratio = y / height
            r = int(13  + ratio * 40)
            g = int(13  + ratio * 10)
            b = int(26  + ratio * 60)
            draw_bg.line([(0, y), (width, y)], fill=(r, g, b))

    # ── Decorative accent bar ──────────────────────────────────────────────────
    draw = ImageDraw.Draw(bg)
    draw.rectangle([0, height - 10, width, height], fill="#ff3c5a")
    draw.rectangle([0, 0, 8, height], fill="#ff3c5a")

    # ── Title text ─────────────────────────────────────────────────────────────
    # Load fonts (try system fonts, fall back to default)
    def load_font(size):
        for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    font_large = load_font(72)
    font_small = load_font(32)

    # Wrap title to fit
    wrapped = textwrap.fill(title, width=22)
    lines   = wrapped.split("\n")

    # Shadow + text
    total_h = len(lines) * 85
    y_start = (height - total_h) // 2 - 20

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font_large)
        lw   = bbox[2] - bbox[0]
        x    = (width - lw) // 2
        y    = y_start + i * 88
        # Shadow
        draw.text((x + 3, y + 3), line, font=font_large, fill="#00000088")
        # Main text
        draw.text((x, y), line, font=font_large, fill="#ffffff")

    # ── "WATCH NOW" badge ──────────────────────────────────────────────────────
    badge_text = "▶  WATCH NOW"
    badge_bbox = draw.textbbox((0, 0), badge_text, font=font_small)
    bw         = badge_bbox[2] - badge_bbox[0] + 40
    bh         = badge_bbox[3] - badge_bbox[1] + 20
    bx         = (width - bw) // 2
    by         = y_start + total_h + 20
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill="#ff3c5a")
    draw.text((bx + 20, by + 10), badge_text, font=font_small, fill="white")

    # ── Save ───────────────────────────────────────────────────────────────────
    bg.save(str(output_path), "PNG", optimize=True)
    print(f"[Thumbnail] Saved: {output_path.name} ({width}x{height})")
    return str(output_path)
