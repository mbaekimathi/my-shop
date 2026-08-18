"""WhatsApp product-card images for catalogue shares."""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

CARD_WIDTH = 1080
CARD_PAD = 48
IMG_HEIGHT_SINGLE = 760
IMG_HEIGHT_MULTI = 520
TEXT_PAD_TOP = 36
LINE_GAP = 10
ITEM_GAP = 28
WHITE = "#ffffff"
INK = "#111b21"
MUTED = "#667781"
LINE = "#e9edef"
ACCENT = "#178f82"


def _kes(value) -> str:
    try:
        amount = Decimal(value or 0).quantize(Decimal("1"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"KSh {amount:,.0f}"


def _item_price(item) -> Decimal:
    try:
        if hasattr(item, "resolve_list_price"):
            return Decimal(item.resolve_list_price() or 0)
        if getattr(item, "shop_price", None) is not None:
            return Decimal(item.shop_price)
    except (InvalidOperation, TypeError, ValueError):
        pass
    return Decimal("0")


def _fonts():
    from PIL import ImageFont

    fonts_dir = Path(settings.BASE_DIR) / "static" / "fonts"
    regular = fonts_dir / "Manrope-Regular.ttf"
    bold = fonts_dir / "Manrope-Bold.ttf"
    return {
        "name": ImageFont.truetype(str(bold), 42),
        "name_sm": ImageFont.truetype(str(bold), 34),
        "price": ImageFont.truetype(str(bold), 40),
        "price_sm": ImageFont.truetype(str(bold), 32),
        "category": ImageFont.truetype(str(regular), 26),
        "category_sm": ImageFont.truetype(str(regular), 22),
        "brand": ImageFont.truetype(str(bold), 24),
    }


def _company_bits() -> dict:
    from shops.services import get_company_display_name, get_company_profile

    name = get_company_display_name() or "MY-SHOP"
    logo = None
    try:
        profile = get_company_profile()
        logo = profile.logo if profile.logo else None
    except Exception:
        logger.debug("Company profile unavailable for catalogue card")
    return {"name": name, "logo": logo}


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        if draw.textlength(trial, font=font) <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _open_item_image(item):
    from PIL import Image as PILImage

    image_field = getattr(item, "image", None)
    if not image_field:
        return None
    try:
        image_field.open("rb")
        return PILImage.open(image_field).convert("RGB")
    except Exception:
        return None


def _fit_contain(image, box_w: int, box_h: int):
    from PIL import Image as PILImage

    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return PILImage.new("RGB", (box_w, box_h), WHITE)
    scale = min(box_w / src_w, box_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = image.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
    canvas = PILImage.new("RGB", (box_w, box_h), WHITE)
    canvas.paste(resized, ((box_w - new_w) // 2, (box_h - new_h) // 2))
    if resized is not image:
        resized.close()
    return canvas


def _paste_logo(canvas, logo_field, *, x: int, y: int, max_width: int = 140):
    from PIL import Image as PILImage

    if not logo_field:
        return
    try:
        logo_field.open("rb")
        logo = PILImage.open(logo_field).convert("RGBA")
    except Exception:
        return
    try:
        ratio = min(max_width / max(logo.width, 1), 56 / max(logo.height, 1))
        size = (max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio)))
        logo = logo.resize(size, PILImage.Resampling.LANCZOS)
        canvas.paste(logo, (x, y), logo if logo.mode == "RGBA" else None)
    finally:
        try:
            logo.close()
        except Exception:
            pass


def _draw_item_block(
    canvas,
    draw,
    *,
    item,
    y: int,
    img_h: int,
    fonts: dict,
    company: dict,
    show_logo: bool,
) -> int:
    from PIL import Image as PILImage
    from PIL import ImageDraw

    inner_w = CARD_WIDTH - CARD_PAD * 2
    name = (getattr(item, "name", None) or "Item").strip() or "Item"
    category = (getattr(item, "category", None) or "").strip()
    price = _kes(_item_price(item))
    name_font = fonts["name"] if img_h >= IMG_HEIGHT_SINGLE else fonts["name_sm"]
    price_font = fonts["price"] if img_h >= IMG_HEIGHT_SINGLE else fonts["price_sm"]
    cat_font = fonts["category"] if img_h >= IMG_HEIGHT_SINGLE else fonts["category_sm"]

    photo = _open_item_image(item)
    if photo:
        fitted = _fit_contain(photo, inner_w, img_h)
        canvas.paste(fitted, (CARD_PAD, y))
        fitted.close()
        photo.close()
    else:
        placeholder = PILImage.new("RGB", (inner_w, img_h), "#f5f7f8")
        ph_draw = ImageDraw.Draw(placeholder)
        letter = (name[:1] or "?").upper()
        ph_draw.text(
            (inner_w // 2, img_h // 2),
            letter,
            font=fonts["name"],
            fill=MUTED,
            anchor="mm",
        )
        canvas.paste(placeholder, (CARD_PAD, y))
        placeholder.close()

    if show_logo:
        _paste_logo(canvas, company.get("logo"), x=CARD_PAD + 18, y=y + 18)

    text_y = y + img_h + TEXT_PAD_TOP
    for line in _wrap_text(name, name_font, inner_w, draw):
        draw.text((CARD_PAD, text_y), line, font=name_font, fill=INK)
        text_y += name_font.size + LINE_GAP
    draw.text((CARD_PAD, text_y), price, font=price_font, fill=INK)
    text_y += price_font.size + LINE_GAP
    if category:
        draw.text((CARD_PAD, text_y), category, font=cat_font, fill=MUTED)
        text_y += cat_font.size + LINE_GAP
    return text_y + CARD_PAD


def compose_catalogue_card(items):
    """JPEG product card(s) for WhatsApp media — photo, name, and price on one image."""
    from django.core.files.base import ContentFile
    from PIL import Image as PILImage
    from PIL import ImageDraw

    chosen = [item for item in (items or []) if item is not None]
    if not chosen:
        return None

    try:
        fonts = _fonts()
    except Exception:
        logger.exception("Could not load fonts for catalogue card")
        return None

    company = _company_bits()
    multi = len(chosen) > 1
    img_h = IMG_HEIGHT_MULTI if multi else IMG_HEIGHT_SINGLE

    # Estimate height: image + text blocks per item.
    dummy = PILImage.new("RGB", (CARD_WIDTH, 10), WHITE)
    draw = ImageDraw.Draw(dummy)
    total_h = CARD_PAD
    for index, item in enumerate(chosen):
        if index:
            total_h += ITEM_GAP
        name = (getattr(item, "name", None) or "Item").strip() or "Item"
        category = (getattr(item, "category", None) or "").strip()
        name_font = fonts["name_sm"] if multi else fonts["name"]
        price_font = fonts["price_sm"] if multi else fonts["price"]
        cat_font = fonts["category_sm"] if multi else fonts["category"]
        lines = _wrap_text(name, name_font, CARD_WIDTH - CARD_PAD * 2, draw)
        total_h += img_h + TEXT_PAD_TOP
        total_h += len(lines) * (name_font.size + LINE_GAP)
        total_h += price_font.size + LINE_GAP
        if category:
            total_h += cat_font.size + LINE_GAP
        total_h += CARD_PAD

    canvas = PILImage.new("RGB", (CARD_WIDTH, total_h), WHITE)
    draw = ImageDraw.Draw(canvas)
    y = CARD_PAD
    for index, item in enumerate(chosen):
        if index:
            draw.line(
                [(CARD_PAD, y), (CARD_WIDTH - CARD_PAD, y)],
                fill=LINE,
                width=2,
            )
            y += ITEM_GAP
        y = _draw_item_block(
            canvas,
            draw,
            item=item,
            y=y,
            img_h=img_h,
            fonts=fonts,
            company=company,
            show_logo=index == 0,
        )

    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=90, optimize=True)
    canvas.close()
    return ContentFile(buf.getvalue(), name="catalogue-card.jpg")
