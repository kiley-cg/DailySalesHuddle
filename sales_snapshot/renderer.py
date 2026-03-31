"""
renderer.py
Renders a 1152×1080 branded dashboard PNG from parsed leaderboard data.
"""

import logging
import math
import os
import re
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _hex(color: str) -> tuple:
    """Convert '#RRGGBB' to (R, G, B)."""
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def _hex_a(color: str, alpha: int = 255) -> tuple:
    """Return (R, G, B, A) from hex."""
    return _hex(color) + (alpha,)


def _rgba_str(css: str) -> tuple:
    """Parse 'rgba(255,255,255,0.7)' → (R,G,B,A 0-255)."""
    m = re.match(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", css)
    if m:
        r, g, b = int(m[1]), int(m[2]), int(m[3])
        a = int(float(m[4]) * 255)
        return (r, g, b, a)
    return _hex_a(css)


# ---------------------------------------------------------------------------
# Font loader
# ---------------------------------------------------------------------------

def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        log.warning("Font not found at %s — using default.", path)
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Draw helpers
# ---------------------------------------------------------------------------

def _rounded_rect(draw: ImageDraw.Draw, xy, radius: int, fill):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    r = radius
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.ellipse([x0, y0, x0 + 2 * r, y0 + 2 * r], fill=fill)
    draw.ellipse([x1 - 2 * r, y0, x1, y0 + 2 * r], fill=fill)
    draw.ellipse([x0, y1 - 2 * r, x0 + 2 * r, y1], fill=fill)
    draw.ellipse([x1 - 2 * r, y1 - 2 * r, x1, y1], fill=fill)


def _text_bbox(draw, text, font):
    """Return (width, height) of rendered text."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_text_right(draw, x_right, y, text, font, fill):
    """Draw text right-aligned to x_right."""
    w, _ = _text_bbox(draw, text, font)
    draw.text((x_right - w, y), text, font=font, fill=fill)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _draw_header(img: Image.Image, draw: ImageDraw.Draw, cfg: dict, W: int, now: datetime):
    brand = cfg["brand"]
    header_h = 90

    # Header background
    draw.rectangle([0, 0, W, header_h], fill=_hex(brand["background_header"]))

    # Logo
    logo_path = cfg.get("logo_path", "assets/CG_Primary.png")
    logo_y_center = header_h // 2
    logo_target_h = 56
    try:
        logo = Image.open(logo_path).convert("RGBA")
        aspect = logo.width / logo.height
        logo_w = int(logo_target_h * aspect)
        logo = logo.resize((logo_w, logo_target_h), Image.LANCZOS)
        logo_y = logo_y_center - logo_target_h // 2
        img.paste(logo, (24, logo_y), logo)
    except (OSError, FileNotFoundError):
        log.warning("Logo not found at %s", logo_path)

    # Right side text
    font_title = _load_font(brand["font_bold"], 22)
    font_sub = _load_font(brand.get("font_regular", brand["font_bold"]), 11)
    date_str = now.strftime("%A, %B %-d, %Y")
    time_str = now.strftime("Updated %-I:%M %p")
    muted = _rgba_str("rgba(255,255,255,0.7)")
    _draw_text_right(draw, W - 24, 22, "Sales Huddle", font_title, _hex(brand["text_white"]))
    _draw_text_right(draw, W - 24, 48, date_str, font_sub, muted)
    _draw_text_right(draw, W - 24, 62, time_str, font_sub, muted)

    # Bottom border: 3px red + 4px teal
    draw.rectangle([0, header_h - 7, W, header_h - 4], fill=_hex(brand["cg_red"]))
    draw.rectangle([0, header_h - 4, W, header_h], fill=_hex(brand["cg_teal"]))

    return header_h


def _draw_metric_cards(
    draw: ImageDraw.Draw,
    cfg: dict,
    W: int,
    y_start: int,
    metrics: dict,
) -> int:
    brand = cfg["brand"]
    card_h = 110
    margin = 16
    gap = 10
    card_w = (W - 2 * margin - 2 * gap) // 3
    y = y_start + 12

    ytd = metrics.get("ytd", {})
    kpi = metrics.get("kpi", {})

    card_data = [
        {
            "label": "SALES YTD",
            "value": ytd.get("Sales YTD", "—"),
            "delta": f"vs {ytd.get('Prior Year YTD', '—')} prior year",
            "delta_color": _hex(brand["accent_teal"]),
            "border_color": brand["cg_red"],
        },
        {
            "label": "SALES / MTD",
            "value": kpi.get("Sales/MTD", "—"),
            "delta": f"Calls: {kpi.get('Calls/MTD', '—')}",
            "delta_color": _hex(brand["accent_teal"]),
            "border_color": brand["cg_teal"],
        },
        {
            "label": "VICTORIES / OPPS",
            "value": kpi.get("Victories/MTD", "—"),
            "delta": f"Opps: {kpi.get('Opportunities/MTD', '—')}",
            "delta_color": _hex(brand["accent_teal"]),
            "border_color": brand["cg_teal"],
        },
    ]

    font_label = _load_font(brand.get("font_semibold", brand["font_bold"]), 10)
    font_value = _load_font(brand["font_bold"], 30)
    font_delta = _load_font(brand.get("font_regular", brand["font_bold"]), 10)
    muted = _rgba_str("rgba(255,255,255,0.7)")

    for i, card in enumerate(card_data):
        x0 = margin + i * (card_w + gap)
        x1 = x0 + card_w
        _rounded_rect(draw, (x0, y, x1, y + card_h), radius=6, fill=_hex(brand["background_cards"]))
        # Left border
        draw.rectangle([x0, y, x0 + 3, y + card_h], fill=_hex(card["border_color"]))

        # Label (ALL-CAPS, muted, 10px)
        draw.text((x0 + 12, y + 10), card["label"], font=font_label, fill=muted)
        # Value (white, 30px bold)
        draw.text((x0 + 12, y + 26), card["value"], font=font_value, fill=_hex(brand["text_white"]))
        # Delta (teal, 10px)
        draw.text((x0 + 12, y + 82), card["delta"], font=font_delta, fill=card["delta_color"])

        # Growth badge on first card
        if i == 0:
            growth = ytd.get("Growth", "")
            if growth:
                draw.text((x0 + 12, y + 96), f"Growth: {growth}", font=font_delta, fill=muted)

    return y + card_h + 12


def _draw_table(
    draw: ImageDraw.Draw,
    cfg: dict,
    W: int,
    y_start: int,
    H: int,
    headers: list,
    rows: list,
) -> int:
    brand = cfg["brand"]
    margin = 16
    table_w = W - 2 * margin

    # Section label
    font_section = _load_font(brand.get("font_semibold", brand["font_bold"]), 10)
    muted = _rgba_str("rgba(255,255,255,0.7)")
    draw.text((margin, y_start), "SALES TARGETS — MONTH TO DATE", font=font_section, fill=muted)
    y = y_start + 18

    # Auto-scale font size based on row count
    n_rows = len(rows)
    if n_rows <= 8:
        font_size = 14
    elif n_rows <= 12:
        font_size = 12
    else:
        font_size = max(10, 14 - (n_rows - 8) // 2)

    font_header = _load_font(brand["font_bold"], 11)
    font_cell = _load_font(brand.get("font_regular", brand["font_bold"]), font_size)
    font_cell_bold = _load_font(brand["font_bold"], font_size)

    # Column widths (proportional)
    col_ratios = [0.22, 0.16, 0.10, 0.16, 0.14, 0.22]
    if len(headers) != len(col_ratios):
        col_ratios = [1 / len(headers)] * len(headers)
    col_widths = [int(table_w * r) for r in col_ratios]

    row_h = font_size + 14

    # Header row
    header_row_h = 26
    draw.rectangle([margin, y, margin + table_w, y + header_row_h], fill=_hex(brand["cg_red"]))
    x = margin
    for col_i, (hdr, cw) in enumerate(zip(headers, col_widths)):
        label = hdr.upper()
        draw.text((x + 6, y + 7), label, font=font_header, fill=_hex(brand["text_white"]))
        x += cw
    y += header_row_h

    # Data rows
    for row_i, row in enumerate(rows):
        row_color = brand["background_rows_odd"] if row_i % 2 == 0 else brand["background_rows_even"]
        draw.rectangle([margin, y, margin + table_w, y + row_h], fill=_hex(row_color))

        x = margin
        for col_i, (col_name, cw) in enumerate(zip(headers, col_widths)):
            value = row.get(col_name, "")

            if col_name == "MTD Target":
                # Draw on-target / off-target indicator
                on_target = row.get("MTD Target")
                indicator_x = x + cw // 2 - 6
                indicator_y = y + row_h // 2 - 6
                if on_target is True:
                    # Teal circle = on target
                    draw.ellipse(
                        [indicator_x, indicator_y, indicator_x + 12, indicator_y + 12],
                        fill=_hex(brand["cg_teal"]),
                    )
                elif on_target is False:
                    # Red circle = behind
                    draw.ellipse(
                        [indicator_x, indicator_y, indicator_x + 12, indicator_y + 12],
                        fill=_hex(brand["cg_red"]),
                    )
                else:
                    # Grey dash
                    draw.rectangle(
                        [indicator_x + 1, indicator_y + 5, indicator_x + 11, indicator_y + 7],
                        fill=_hex(brand["md_gray"]),
                    )
            elif col_name == "Sales Rep":
                draw.text(
                    (x + 6, y + (row_h - font_size) // 2),
                    str(value),
                    font=font_cell_bold,
                    fill=_hex(brand["text_white"]),
                )
            elif "$" in str(value) or re.search(r"^\$[\d,.]", str(value)):
                draw.text(
                    (x + 6, y + (row_h - font_size) // 2),
                    str(value),
                    font=font_cell,
                    fill=_hex(brand["accent_teal"]),
                )
            else:
                draw.text(
                    (x + 6, y + (row_h - font_size) // 2),
                    str(value),
                    font=font_cell,
                    fill=_hex(brand["text_white"]),
                )
            x += cw

        y += row_h
        if y > H - 60:
            log.warning("Table clipped — too many rows to fit.")
            break

    return y


def _draw_pattern_strip(draw: ImageDraw.Draw, W: int, y: int):
    """7px repeating brand-color stripe."""
    strip_h = 7
    pattern = [
        ("#E01B2B", 14),
        ("#757575", 4),
        ("#00A8B0", 14),
        ("#757575", 4),
    ]
    x = 0
    while x < W:
        for color, w in pattern:
            draw.rectangle([x, y, x + w, y + strip_h], fill=_hex(color))
            x += w
            if x >= W:
                break
    return y + strip_h


def _draw_footer(draw: ImageDraw.Draw, cfg: dict, W: int, H: int, y: int):
    brand = cfg["brand"]
    footer_h = H - y
    draw.rectangle([0, y, W, H], fill=_hex(brand["background_footer"]))

    font_footer = _load_font(brand.get("font_regular", brand["font_bold"]), 10)
    muted = _rgba_str("rgba(255,255,255,0.7)")
    text = "ColorGraphicsWA.com  •  360-352-3970  •  Updated automatically each weekday morning"
    draw.text((16, y + (footer_h - 10) // 2), text, font=font_footer, fill=muted)

    # Live indicator — teal dot + "Live"
    dot_r = 4
    dot_x = W - 60
    dot_y = y + footer_h // 2
    draw.ellipse(
        [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
        fill=_hex(brand["accent_teal"]),
    )
    draw.text(
        (dot_x + dot_r + 4, dot_y - 5),
        "Live",
        font=font_footer,
        fill=_hex(brand["accent_teal"]),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render(data: dict, cfg: dict, output_path: str | None = None) -> str:
    """
    Render the branded dashboard PNG.

    Args:
        data: output of table_parser.parse()
        cfg:  loaded config.yaml dict
        output_path: override output path (default: output/sales_snapshot_YYYY-MM-DD.png)

    Returns:
        Path to the saved PNG file.
    """
    brand = cfg["brand"]
    W = cfg.get("canvas_width", 1152)
    H = cfg.get("canvas_height", 1080)
    now = datetime.now()

    # Ensure output dir exists
    out_dir = Path(cfg.get("output_folder", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        date_str = now.strftime("%Y-%m-%d")
        output_path = str(out_dir / f"sales_snapshot_{date_str}.png")

    # Create canvas
    img = Image.new("RGBA", (W, H), _hex_a(brand["background_main"]))
    draw = ImageDraw.Draw(img)

    # Draw sections
    header_bottom = _draw_header(img, draw, cfg, W, now)
    cards_bottom = _draw_metric_cards(draw, cfg, W, header_bottom, data.get("metrics", {}))

    # Table fills from cards_bottom to pattern strip
    footer_h = 30
    strip_h = 7
    table_bottom_limit = H - footer_h - strip_h

    _draw_table(
        draw,
        cfg,
        W,
        cards_bottom,
        table_bottom_limit,
        data.get("headers", []),
        data.get("rows", []),
    )

    strip_y = H - footer_h - strip_h
    _draw_pattern_strip(draw, W, strip_y)
    _draw_footer(draw, cfg, W, H, strip_y + strip_h)

    # Save
    img_rgb = img.convert("RGB")
    img_rgb.save(output_path, "PNG", optimize=True)
    log.info("Saved dashboard PNG: %s", output_path)

    # Always overwrite latest.png
    latest_path = str(out_dir / "latest.png")
    img_rgb.save(latest_path, "PNG")
    log.info("Saved latest.png: %s", latest_path)

    return output_path


# ---------------------------------------------------------------------------
# CLI: render with dummy data for preview
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        print("Run from the sales_snapshot/ directory.")
        sys.exit(1)

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    DUMMY_DATA = {
        "metrics": {
            "ytd": {
                "Sales YTD": "$1,423,890",
                "Growth": "+8.4%",
                "Prior Year YTD": "$1,313,220",
            },
            "kpi": {
                "Sales/MTD": "$187,440",
                "Calls/MTD": "312",
                "Victories/MTD": "47",
                "Opportunities/MTD": "89",
            },
        },
        "headers": [
            "Sales Rep",
            "Monthly Target",
            "MTD Target",
            "Booked Sales",
            "% of Target",
            "Submitted Sales",
        ],
        "rows": [
            {
                "Sales Rep": "Alex Johnson",
                "Monthly Target": "$42,000",
                "MTD Target": True,
                "Booked Sales": "$38,450",
                "% of Target": "91%",
                "Submitted Sales": "$41,200",
            },
            {
                "Sales Rep": "Maria Santos",
                "Monthly Target": "$38,000",
                "MTD Target": True,
                "Booked Sales": "$40,110",
                "% of Target": "106%",
                "Submitted Sales": "$40,110",
            },
            {
                "Sales Rep": "Derek Chu",
                "Monthly Target": "$35,000",
                "MTD Target": False,
                "Booked Sales": "$22,870",
                "% of Target": "65%",
                "Submitted Sales": "$24,900",
            },
            {
                "Sales Rep": "Priya Nair",
                "Monthly Target": "$40,000",
                "MTD Target": True,
                "Booked Sales": "$39,800",
                "% of Target": "100%",
                "Submitted Sales": "$39,800",
            },
            {
                "Sales Rep": "Tom Waverly",
                "Monthly Target": "$32,000",
                "MTD Target": False,
                "Booked Sales": "$18,200",
                "% of Target": "57%",
                "Submitted Sales": "$20,100",
            },
            {
                "Sales Rep": "Keisha Brooks",
                "Monthly Target": "$45,000",
                "MTD Target": True,
                "Booked Sales": "$44,100",
                "% of Target": "98%",
                "Submitted Sales": "$44,100",
            },
        ],
    }

    out = render(DUMMY_DATA, cfg)
    print(f"Preview saved: {out}")
    print(f"Latest: output/latest.png")
