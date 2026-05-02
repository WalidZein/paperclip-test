"""
Pillow-based data card image generator for Format B.

Supported card types:
  - tier_list:        Tier rows (S/A/B/C) with item names per tier
  - comparison_table: Two-column table (Feature | Before | After) with zebra striping
  - ranked_list:      Numbered ranked items with optional scores
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CARD_W = 1000
ROW_H = 68
TITLE_H = 88
PADDING = 28
FONT_PATH_BOLD = None   # resolved at runtime
FONT_PATH_REG = None

# Zebra stripe colors (dark theme)
STRIPE_DARK = (30, 34, 50)
STRIPE_LIGHT = (42, 47, 68)
TITLE_BG = (18, 22, 36)
TEXT_PRIMARY = (240, 240, 240)
TEXT_SECONDARY = (180, 185, 205)
ACCENT = (90, 140, 255)

TIER_COLORS = {
    "S": (255, 71, 71),
    "A": (255, 163, 51),
    "B": (255, 220, 50),
    "C": (100, 200, 100),
    "D": (150, 150, 200),
    "F": (130, 130, 130),
}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_rounded_rect(
    draw: ImageDraw.Draw,
    xy: Tuple[int, int, int, int],
    radius: int,
    fill: Tuple,
) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def generate_tier_list_card(
    title: str,
    tiers: Dict[str, List[str]],
    bg_color: Tuple[int, int, int] = (22, 26, 42),
    max_rows: int = 8,
) -> Image.Image:
    """
    Generate a tier list card image.

    Args:
        title: Card title displayed at the top.
        tiers: Mapping of tier label → list of item names.
               e.g. {"S": ["ChatGPT", "Claude"], "A": ["Gemini"]}
        bg_color: Background RGB color.
        max_rows: Maximum number of tier rows to render.
    """
    rows = list(tiers.items())[:max_rows]
    card_h = TITLE_H + len(rows) * ROW_H + PADDING * 2
    img = Image.new("RGBA", (CARD_W, card_h), (*bg_color, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(36, bold=True)
    font_tier = _load_font(30, bold=True)
    font_items = _load_font(26, bold=False)

    # Title bar
    draw.rectangle([0, 0, CARD_W, TITLE_H], fill=TITLE_BG)
    draw.text((PADDING, TITLE_H // 2 - 18), title, font=font_title, fill=TEXT_PRIMARY)

    y = TITLE_H + PADDING
    for i, (tier_label, items) in enumerate(rows):
        stripe = STRIPE_LIGHT if i % 2 == 0 else STRIPE_DARK
        row_y1 = y + ROW_H
        draw.rectangle([0, y, CARD_W, row_y1], fill=stripe)

        # Tier badge
        tier_color = TIER_COLORS.get(tier_label.upper(), ACCENT)
        badge_x1 = PADDING + 52
        _draw_rounded_rect(draw, (PADDING, y + 10, badge_x1, row_y1 - 10), 8, tier_color)
        draw.text(
            (PADDING + 6, y + ROW_H // 2 - 15),
            tier_label.upper(),
            font=font_tier,
            fill=(255, 255, 255),
        )

        # Items
        items_text = "  ·  ".join(items)
        draw.text((badge_x1 + 14, y + ROW_H // 2 - 13), items_text, font=font_items, fill=TEXT_PRIMARY)
        y += ROW_H

    return img


def generate_comparison_table_card(
    title: str,
    rows: List[Dict[str, str]],
    headers: Tuple[str, str] = ("Before", "After"),
    bg_color: Tuple[int, int, int] = (22, 26, 42),
    max_rows: int = 8,
) -> Image.Image:
    """
    Generate a comparison table card image.

    Args:
        title: Card title.
        rows: List of dicts with keys "feature", "before", "after".
        headers: Column header labels for before/after columns.
        bg_color: Background RGB color.
        max_rows: Maximum data rows to show.
    """
    data_rows = rows[:max_rows]
    card_h = TITLE_H + ROW_H + len(data_rows) * ROW_H + PADDING
    img = Image.new("RGBA", (CARD_W, card_h), (*bg_color, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(36, bold=True)
    font_header = _load_font(24, bold=True)
    font_cell = _load_font(24, bold=False)
    font_bold_cell = _load_font(24, bold=True)

    # Column widths: feature=38%, before=28%, after=34%
    col_feature = int(CARD_W * 0.38)
    col_before = int(CARD_W * 0.28)

    # Title bar
    draw.rectangle([0, 0, CARD_W, TITLE_H], fill=TITLE_BG)
    draw.text((PADDING, TITLE_H // 2 - 18), title, font=font_title, fill=TEXT_PRIMARY)

    y = TITLE_H

    # Header row
    draw.rectangle([0, y, CARD_W, y + ROW_H], fill=(25, 30, 50))
    draw.text((PADDING, y + ROW_H // 2 - 12), "Feature", font=font_header, fill=ACCENT)
    draw.text((col_feature + PADDING, y + ROW_H // 2 - 12), headers[0], font=font_header, fill=(220, 80, 80))
    draw.text((col_feature + col_before + PADDING, y + ROW_H // 2 - 12), headers[1], font=font_header, fill=(80, 200, 120))
    y += ROW_H

    for i, row in enumerate(data_rows):
        stripe = STRIPE_LIGHT if i % 2 == 0 else STRIPE_DARK
        draw.rectangle([0, y, CARD_W, y + ROW_H], fill=stripe)
        draw.text((PADDING, y + ROW_H // 2 - 12), row.get("feature", ""), font=font_cell, fill=TEXT_SECONDARY)
        draw.text((col_feature + PADDING, y + ROW_H // 2 - 12), row.get("before", ""), font=font_cell, fill=(200, 80, 80))
        draw.text((col_feature + col_before + PADDING, y + ROW_H // 2 - 12), row.get("after", ""), font=font_bold_cell, fill=(80, 210, 130))

        # Divider lines between columns
        draw.line([(col_feature, y), (col_feature, y + ROW_H)], fill=(60, 65, 90), width=1)
        draw.line([(col_feature + col_before, y), (col_feature + col_before, y + ROW_H)], fill=(60, 65, 90), width=1)
        y += ROW_H

    return img


def generate_ranked_list_card(
    title: str,
    items: List[Union[str, Dict]],
    bg_color: Tuple[int, int, int] = (22, 26, 42),
    max_rows: int = 8,
) -> Image.Image:
    """
    Generate a ranked list card image.

    Args:
        title: Card title.
        items: List of strings or dicts with "name" and optional "score"/"label".
        bg_color: Background RGB color.
        max_rows: Maximum items to show.
    """
    data_items = items[:max_rows]
    card_h = TITLE_H + len(data_items) * ROW_H + PADDING * 2
    img = Image.new("RGBA", (CARD_W, card_h), (*bg_color, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(36, bold=True)
    font_rank = _load_font(28, bold=True)
    font_item = _load_font(26, bold=False)
    font_score = _load_font(22, bold=True)

    # Rank badge colors for top 3
    rank_colors = [(255, 200, 0), (180, 180, 180), (190, 115, 60)]

    draw.rectangle([0, 0, CARD_W, TITLE_H], fill=TITLE_BG)
    draw.text((PADDING, TITLE_H // 2 - 18), title, font=font_title, fill=TEXT_PRIMARY)

    y = TITLE_H + PADDING
    for i, item in enumerate(data_items):
        if isinstance(item, dict):
            name = item.get("name", str(item))
            score = item.get("score", item.get("label", ""))
        else:
            name = str(item)
            score = ""

        stripe = STRIPE_LIGHT if i % 2 == 0 else STRIPE_DARK
        draw.rectangle([0, y, CARD_W, y + ROW_H], fill=stripe)

        # Rank badge
        rank_num = i + 1
        badge_color = rank_colors[i] if i < 3 else (70, 80, 120)
        badge_x1 = PADDING + 48
        _draw_rounded_rect(draw, (PADDING, y + 10, badge_x1, y + ROW_H - 10), 8, badge_color)
        rank_text = str(rank_num)
        draw.text((PADDING + 6 + (20 - len(rank_text) * 8), y + ROW_H // 2 - 14), rank_text, font=font_rank, fill=(255, 255, 255))

        draw.text((badge_x1 + 14, y + ROW_H // 2 - 13), name, font=font_item, fill=TEXT_PRIMARY)

        if score:
            score_str = str(score)
            draw.text((CARD_W - PADDING - len(score_str) * 14, y + ROW_H // 2 - 11), score_str, font=font_score, fill=ACCENT)

        y += ROW_H

    return img


def render_card(
    card_type: str,
    title: str,
    data: Union[Dict, List],
    bg_color: Tuple[int, int, int] = (22, 26, 42),
    output_path: Optional[str] = None,
    max_rows: int = 8,
) -> Image.Image:
    """
    Unified card renderer entry point.

    Args:
        card_type: "tier_list", "comparison_table", or "ranked_list"
        title: Card title text.
        data: Card-type-specific data structure:
              - tier_list: Dict[str, List[str]]
              - comparison_table: List[Dict[str, str]] with "feature"/"before"/"after"
              - ranked_list: List[str | Dict]
        bg_color: Background RGB tuple.
        output_path: If provided, save the card PNG here.
        max_rows: Maximum data rows.

    Returns:
        PIL Image of the card.
    """
    if card_type == "tier_list":
        img = generate_tier_list_card(title, data, bg_color=bg_color, max_rows=max_rows)
    elif card_type == "comparison_table":
        img = generate_comparison_table_card(title, data, bg_color=bg_color, max_rows=max_rows)
    elif card_type == "ranked_list":
        img = generate_ranked_list_card(title, data, bg_color=bg_color, max_rows=max_rows)
    else:
        raise ValueError(f"Unknown card_type: {card_type!r}. Use 'tier_list', 'comparison_table', or 'ranked_list'.")

    if output_path:
        img.save(output_path)
        logger.info("Card saved: %s (%dx%d)", output_path, img.width, img.height)

    return img
