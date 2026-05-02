import logging
import os
import textwrap
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

CANVAS_W = 1000
CANVAS_H = 1000
BANNER_H = 280

# Brand colours
BRAND_DARK = (30, 30, 40)
BRAND_ACCENT = (220, 80, 60)   # warm red
TEXT_WHITE = (255, 255, 255)
TEXT_LIGHT = (230, 230, 230)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates = [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "C:/Windows/Fonts/verdanab.ttf",
        ]
    else:
        candidates = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/verdana.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _download_image(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            r = client.get(url)
            r.raise_for_status()
            from io import BytesIO
            return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception as e:
        logger.warning("Image download failed (%s): %s", url, e)
        return None


def _fit_image(img: Image.Image, width: int, height: int) -> Image.Image:
    """Fit image inside canvas without cropping — letterbox with dark bg."""
    canvas = Image.new("RGB", (width, height), BRAND_DARK)
    img.thumbnail((width, height), Image.LANCZOS)
    w, h = img.size
    x = (width - w) // 2
    y = (height - h) // 2
    canvas.paste(img, (x, y))
    return canvas


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)


def compose_sales_image(
    title: str,
    image_url: str,
    output_path: str,
) -> bool:
    """
    Compose a 1000x1000 Pinterest-optimised sales image.
    Returns True on success.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # --- Background ---
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BRAND_DARK)

    # --- Product image (upper portion) ---
    product_img = _download_image(image_url)
    img_area_h = CANVAS_H - BANNER_H  # 720px

    if product_img:
        fitted = _fit_image(product_img, CANVAS_W, img_area_h)
        canvas.paste(fitted, (0, 0))
    else:
        # Placeholder gradient
        placeholder = Image.new("RGB", (CANVAS_W, img_area_h))
        draw_ph = ImageDraw.Draw(placeholder)
        for y in range(img_area_h):
            val = int(40 + (y / img_area_h) * 60)
            draw_ph.line([(0, y), (CANVAS_W, y)], fill=(val, val, val + 20))
        canvas.paste(placeholder, (0, 0))

    # Gradient fade at bottom of image area into banner
    fade_h = 120
    fade_start = img_area_h - fade_h
    for y in range(fade_h):
        alpha = int(255 * (y / fade_h) ** 1.5)
        r, g, b = BRAND_DARK
        draw_line = ImageDraw.Draw(canvas)
        draw_line.line(
            [(0, fade_start + y), (CANVAS_W, fade_start + y)],
            fill=(r, g, b, alpha),
        )

    draw = ImageDraw.Draw(canvas)

    # --- Bottom banner ---
    banner_y = CANVAS_H - BANNER_H
    draw.rectangle([(0, banner_y), (CANVAS_W, CANVAS_H)], fill=BRAND_DARK)

    # Accent bar at top of banner
    draw.rectangle([(0, banner_y), (CANVAS_W, banner_y + 4)], fill=BRAND_ACCENT)

    # --- KDP badge ---
    badge_font = _load_font(20, bold=True)
    badge_text = "KDP INTERIOR"
    _draw_rounded_rect(draw, (40, banner_y + 20, 210, banner_y + 52), radius=6, fill=BRAND_ACCENT)
    draw.text((50, banner_y + 24), badge_text, font=badge_font, fill=TEXT_WHITE)

    # --- Title ---
    title_font = _load_font(38, bold=True)
    title_clean = title.strip()
    # Wrap to ~30 chars per line
    wrapped = textwrap.wrap(title_clean, width=30)[:3]  # max 3 lines
    title_y = banner_y + 70
    for line in wrapped:
        draw.text((40, title_y), line, font=title_font, fill=TEXT_WHITE)
        title_y += 46

    # --- CTA button ---
    cta_font = _load_font(26, bold=True)
    cta_text = "Shop on Creative Fabrica →"
    btn_y = CANVAS_H - 70
    _draw_rounded_rect(draw, (40, btn_y - 10, 560, btn_y + 44), radius=10, fill=BRAND_ACCENT)
    draw.text((58, btn_y), cta_text, font=cta_font, fill=TEXT_WHITE)

    # --- Small watermark ---
    wm_font = _load_font(18)
    draw.text((CANVAS_W - 260, CANVAS_H - 28), "creativefabrica.com", font=wm_font, fill=(120, 120, 130))

    try:
        canvas.save(output_path, "PNG", optimize=True)
        logger.info("Saved sales image: %s", output_path)
        return True
    except Exception as e:
        logger.error("Failed to save image %s: %s", output_path, e)
        return False


def generate_for_product(product: dict, images_dir: str) -> str:
    """Generate sales image for a product. Returns local relative web path."""
    slug = product["slug"]
    filename = f"{slug}.png"
    output_path = os.path.join(images_dir, filename)

    if os.path.exists(output_path):
        return f"/static/images/{filename}"

    ok = compose_sales_image(
        title=product["title"],
        image_url=product.get("image_url", ""),
        output_path=output_path,
    )
    return f"/static/images/{filename}" if ok else ""
