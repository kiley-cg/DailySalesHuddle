from typing import Optional
"""
renderer.py
Renders a 1152×1080 branded dashboard PNG from parsed leaderboard data.
Matches the Color Graphics Daily Sales Leaderboard design reference.
"""

import logging
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
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def _hex_a(color: str, alpha: int = 255) -> tuple:
    return _hex(color) + (alpha,)


def _muted(alpha: int = 178) -> tuple:
    """rgba(255,255,255,0.7) → (255,255,255,178)"""
    return (255, 255, 255, alpha)


# ---------------------------------------------------------------------------
# Font loader
# ---------------------------------------------------------------------------

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        log.warning("Font not found: %s — using default.", path)
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Draw helpers
# ---------------------------------------------------------------------------

def _tw(draw, text, font) -> int:
    """Text width in pixels."""
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _th(draw, text, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def _draw_centered(draw, cx, y, text, font, fill):
    w = _tw(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=fill)


def _draw_right(draw, rx, y, text, font, fill):
    w = _tw(draw, text, font)
    draw.text((rx - w, y), text, font=font, fill=fill)


def _pct_value(s: str) -> Optional[float]:
    """Extract numeric percentage from a string like '126.8%' or '88.0%'."""
    m = re.search(r"([\d.]+)%", str(s))
    return float(m.group(1)) if m else None


def _is_dollar(s: str) -> bool:
    return bool(re.match(r"^\$[\d,. ]+$", str(s).strip()))


# ---------------------------------------------------------------------------
# Section 1 — Header
# ---------------------------------------------------------------------------

def _draw_header(img: Image.Image, draw: ImageDraw.Draw, cfg: dict, W: int, now: datetime) -> int:
    brand = cfg["brand"]
    H_HDR = 68

    draw.rectangle([0, 0, W, H_HDR], fill=_hex(brand["background_header"]))

    # Logo — top-left, height 44px
    logo_h = 44
    try:
        logo = Image.open(cfg.get("logo_path", "assets/CG_Primary.png")).convert("RGBA")
        lw = int(logo_h * logo.width / logo.height)
        logo = logo.resize((lw, logo_h), Image.LANCZOS)
        img.paste(logo, (14, (H_HDR - logo_h) // 2), logo)
    except Exception:
        log.warning("Logo not found.")

    # Title — top-right
    f_title = _font(brand["font_bold"], 24)
    f_date  = _font(brand.get("font_regular", brand["font_bold"]), 11)
    date_str = now.strftime("%A, %B %-d, %Y") + now.strftime("  •  %-I:%M %p")
    _draw_right(draw, W - 20, 12, "Daily Sales Leaderboard", f_title, _hex(brand["text_white"]))
    _draw_right(draw, W - 20, 42, date_str, f_date, _muted())

    # Bottom border: 4px red + 3px teal
    draw.rectangle([0, H_HDR - 7, W, H_HDR - 3], fill=_hex(brand["cg_red"]))
    draw.rectangle([0, H_HDR - 3, W, H_HDR],     fill=_hex(brand["cg_teal"]))

    return H_HDR


# ---------------------------------------------------------------------------
# Section 2 — YTD Cards (3 full-width red cards)
# ---------------------------------------------------------------------------

def _draw_ytd_cards(draw: ImageDraw.Draw, cfg: dict, W: int, y: int, ytd: dict) -> int:
    brand = cfg["brand"]
    PAD = 14
    GAP = 6
    H_CARD = 108
    card_w = (W - 2 * PAD - 2 * GAP) // 3

    cards = [
        ("SALES YTD",      ytd.get("Sales YTD",      "—")),
        ("YTD GROWTH",     ytd.get("Growth",          "—")),
        ("PRIOR YEAR YTD", ytd.get("Prior Year YTD",  "—")),
    ]

    f_label = _font(brand.get("font_semibold", brand["font_bold"]), 11)
    f_value = _font(brand["font_bold"], 36)

    y0 = y + 10
    for i, (label, value) in enumerate(cards):
        x0 = PAD + i * (card_w + GAP)
        x1 = x0 + card_w
        cx = (x0 + x1) // 2

        draw.rectangle([x0, y0, x1, y0 + H_CARD], fill=_hex(brand["cg_red"]))

        # Label centered, muted
        lw = _tw(draw, label, f_label)
        draw.text((cx - lw // 2, y0 + 14), label, font=f_label, fill=_muted(200))

        # Value centered, white bold
        vw = _tw(draw, value, f_value)
        draw.text((cx - vw // 2, y0 + 34), value, font=f_value, fill=_hex(brand["text_white"]))

        # Vertical divider (right edge of card 0 and 1)
        if i < 2:
            draw.rectangle([x1 + 1, y0, x1 + GAP - 1, y0 + H_CARD], fill=_hex(brand["background_main"]))

    return y0 + H_CARD + 10


# ---------------------------------------------------------------------------
# Section 3 — KPI Row (4 panels, each with Today + MTD rows)
# ---------------------------------------------------------------------------

def _draw_kpi_row(draw: ImageDraw.Draw, cfg: dict, W: int, y: int, kpi: dict) -> int:
    brand = cfg["brand"]
    PAD = 14
    GAP = 0          # panels share a divider line
    H_KPI = 78
    panel_w = (W - 2 * PAD) // 4

    # Map kpi keys from parser to display labels
    panels = [
        {
            "today_label": "SALES TODAY",
            "today_value": kpi.get("Sales Today", kpi.get("Sales/MTD", "—")),
            "mtd_label":   "SALES MTD",
            "mtd_value":   kpi.get("Sales/MTD", "—"),
        },
        {
            "today_label": "CALLS TODAY",
            "today_value": kpi.get("Calls Today", kpi.get("Calls/MTD", "—")),
            "mtd_label":   "CALLS MTD",
            "mtd_value":   kpi.get("Calls/MTD", "—"),
        },
        {
            "today_label": "VICTORIES TODAY",
            "today_value": kpi.get("Victories Today", kpi.get("Victories/MTD", "—")),
            "mtd_label":   "VICTORIES MTD",
            "mtd_value":   kpi.get("Victories/MTD", "—"),
        },
        {
            "today_label": "OPPS TODAY",
            "today_value": kpi.get("Opps Today", kpi.get("Opportunities/MTD", "—")),
            "mtd_label":   "OPPS MTD (WTD)",
            "mtd_value":   kpi.get("Opportunities/MTD", "—"),
        },
    ]

    f_label = _font(brand.get("font_regular", brand["font_bold"]), 10)
    f_today = _font(brand["font_bold"], 18)
    f_mtd   = _font(brand["font_bold"], 13)

    bg = _hex(brand.get("background_cards", "#686868"))
    draw.rectangle([PAD, y, W - PAD, y + H_KPI], fill=bg)

    for i, panel in enumerate(panels):
        x0 = PAD + i * panel_w
        x1 = x0 + panel_w

        # Vertical divider
        if i > 0:
            draw.rectangle([x0, y + 8, x0 + 1, y + H_KPI - 8],
                           fill=_hex(brand["md_gray"]) + (80,) if False else _muted(60))

        # Today row
        draw.text((x0 + 10, y + 10), panel["today_label"], font=f_label, fill=_muted(160))
        _draw_right(draw, x1 - 10, y + 8, str(panel["today_value"]),
                    f_today, _hex(brand["text_white"]))

        # Divider line within panel
        draw.rectangle([x0 + 8, y + H_KPI // 2, x1 - 8, y + H_KPI // 2 + 1],
                       fill=_muted(40))

        # MTD row
        draw.text((x0 + 10, y + H_KPI // 2 + 5), panel["mtd_label"], font=f_label, fill=_muted(160))
        _draw_right(draw, x1 - 10, y + H_KPI // 2 + 3, str(panel["mtd_value"]),
                    f_mtd, _hex(brand["accent_teal"]))

    return y + H_KPI + 10


# ---------------------------------------------------------------------------
# Section 4 — Sales Targets Table
# ---------------------------------------------------------------------------

def _draw_table(draw: ImageDraw.Draw, cfg: dict, W: int, y: int, H: int,
                headers: list, rows: list) -> int:
    brand = cfg["brand"]
    PAD = 14

    # Section label
    f_sec = _font(brand.get("font_semibold", brand["font_bold"]), 10)
    draw.text((PAD, y), "SALES TARGETS — BREAKDOWN BY REP", font=f_sec, fill=_muted(160))
    y += 20

    table_w = W - 2 * PAD

    # Auto-scale row font
    n = len(rows)
    available_h = H - y - 7 - 35  # pattern strip + footer
    header_h = 32
    max_row_h = min(60, max(36, (available_h - header_h) // max(n, 1)))
    font_size = max(10, min(14, max_row_h - 22))

    f_hdr      = _font(brand["font_bold"], 11)
    f_rep      = _font(brand["font_bold"], font_size)
    f_cell     = _font(brand.get("font_regular", brand["font_bold"]), font_size)
    f_cell_b   = _font(brand["font_bold"], font_size)

    # Column layout — proportional widths
    # SALES REP | MO. TARGET | MTD TARGET | BOOKED SALES | % OF TARGET | SUBMITTED
    col_names   = ["Sales Rep", "Monthly Target", "MTD Target", "Booked Sales", "% of Target", "Submitted Sales"]
    col_labels  = ["SALES REP", "MO. TARGET", "MTD TARGET", "BOOKED SALES", "% OF TARGET", "SUBMITTED"]
    col_ratios  = [0.30, 0.13, 0.14, 0.14, 0.13, 0.16]
    col_widths  = [int(table_w * r) for r in col_ratios]
    # Fix rounding
    col_widths[-1] = table_w - sum(col_widths[:-1])

    # ---- Header row ----
    draw.rectangle([PAD, y, PAD + table_w, y + header_h], fill=_hex(brand["cg_red"]))
    x = PAD
    for label, cw in zip(col_labels, col_widths):
        if col_labels.index(label) == 0:
            draw.text((x + 10, y + (header_h - 11) // 2), label, font=f_hdr,
                      fill=_hex(brand["text_white"]))
        else:
            lw = _tw(draw, label, f_hdr)
            draw.text((x + cw // 2 - lw // 2, y + (header_h - 11) // 2), label,
                      font=f_hdr, fill=_hex(brand["text_white"]))
        x += cw
    y += header_h

    # ---- Data rows ----
    for ri, row in enumerate(rows):
        row_bg = brand["background_rows_odd"] if ri % 2 == 0 else brand["background_rows_even"]
        draw.rectangle([PAD, y, PAD + table_w, y + max_row_h], fill=_hex(row_bg))

        x = PAD
        for ci, (col_name, cw) in enumerate(zip(col_names, col_widths)):
            value = str(row.get(col_name, "") or "")
            cy = y + (max_row_h - font_size) // 2

            if ci == 0:
                # Sales Rep — bold white, left-aligned
                draw.text((x + 10, cy), value, font=f_rep, fill=_hex(brand["text_white"]))

            elif col_name == "MTD Target":
                # Color by on_target flag; value is the dollar string
                on_target = row.get("MTD Target_flag")  # True/False/None
                if on_target is True:
                    color = _hex(brand["accent_teal"])
                elif on_target is False:
                    color = _hex(brand["cg_red"])
                else:
                    color = _hex(brand["text_white"])
                vw = _tw(draw, value, f_cell)
                draw.text((x + cw // 2 - vw // 2, cy), value, font=f_cell, fill=color)

            elif col_name == "% of Target":
                # Color teal if ≥100%, red if below
                pct = _pct_value(value)
                if pct is not None:
                    color = _hex(brand["accent_teal"]) if pct >= 100 else _hex(brand["cg_red"])
                else:
                    color = _hex(brand["text_white"])
                vw = _tw(draw, value, f_cell_b)
                draw.text((x + cw // 2 - vw // 2, cy), value, font=f_cell_b, fill=color)

            else:
                # All other columns — white, centered
                vw = _tw(draw, value, f_cell)
                draw.text((x + cw // 2 - vw // 2, cy), value, font=f_cell,
                          fill=_hex(brand["text_white"]))

            x += cw

        y += max_row_h
        if y >= H - 50:
            log.warning("Table clipped — not enough vertical space.")
            break

    return y


# ---------------------------------------------------------------------------
# Pattern strip
# ---------------------------------------------------------------------------

def _draw_pattern(draw: ImageDraw.Draw, W: int, y: int) -> int:
    H_STRIP = 7
    pattern = [("#E01B2B", 14), ("#757575", 4), ("#00A8B0", 14), ("#757575", 4)]
    x = 0
    while x < W:
        for color, w in pattern:
            draw.rectangle([x, y, x + w, y + H_STRIP], fill=_hex(color))
            x += w
            if x >= W:
                break
    return y + H_STRIP


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def _draw_footer(draw: ImageDraw.Draw, cfg: dict, W: int, y_top: int, H: int):
    brand = cfg["brand"]
    draw.rectangle([0, y_top, W, H], fill=_hex(brand["background_footer"]))
    f = _font(brand.get("font_regular", brand["font_bold"]), 10)
    text = "ColorGraphicsWA.com  •  360-352-3970  •  Updated automatically each weekday morning"
    fh = H - y_top
    draw.text((16, y_top + (fh - 10) // 2), text, font=f, fill=_muted(160))

    # Live dot
    dot_x, dot_y = W - 55, y_top + fh // 2
    r = 4
    draw.ellipse([dot_x - r, dot_y - r, dot_x + r, dot_y + r],
                 fill=_hex(brand["accent_teal"]))
    draw.text((dot_x + r + 4, dot_y - 5), "Live", font=f,
              fill=_hex(brand["accent_teal"]))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render(data: dict, cfg: dict, output_path: Optional[str] = None) -> str:
    """
    Render the 1152×1080 branded dashboard PNG.
    Returns path to saved file.
    """
    brand = cfg["brand"]
    W = cfg.get("canvas_width",  1152)
    H = cfg.get("canvas_height", 1080)
    now = datetime.now()

    out_dir = Path(cfg.get("output_folder", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        output_path = str(out_dir / f"sales_snapshot_{now.strftime('%Y-%m-%d')}.png")

    img  = Image.new("RGBA", (W, H), _hex_a(brand["background_main"]))
    draw = ImageDraw.Draw(img, "RGBA")

    y = _draw_header(img, draw, cfg, W, now)
    y = _draw_ytd_cards(draw, cfg, W, y, data.get("metrics", {}).get("ytd", {}))
    y = _draw_kpi_row(draw, cfg, W, y, data.get("metrics", {}).get("kpi", {}))

    footer_h   = 35
    strip_h    = 7
    strip_y    = H - footer_h - strip_h

    _draw_table(draw, cfg, W, y, strip_y, data.get("headers", []), data.get("rows", []))
    _draw_pattern(draw, W, strip_y)
    _draw_footer(draw, cfg, W, strip_y + strip_h, H)

    out = img.convert("RGB")
    out.save(output_path, "PNG", optimize=True)
    out.save(str(out_dir / "latest.png"), "PNG")
    log.info("Saved: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# CLI — render with dummy data matching the reference screenshot
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, logging as _logging
    _logging.basicConfig(level=logging.INFO)

    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        print("Run from the sales_snapshot/ directory.")
        sys.exit(1)

    with open(cfg_path) as f:
        cfg = __import__("yaml").safe_load(f)

    DUMMY = {
        "metrics": {
            "ytd": {
                "Sales YTD":      "$844,229",
                "Growth":         "-1.1%",
                "Prior Year YTD": "$853,719",
            },
            "kpi": {
                "Sales Today":        "$29,908",
                "Sales/MTD":          "$396,797",
                "Calls Today":        "0",
                "Calls/MTD":          "28",
                "Victories Today":    "0",
                "Victories/MTD":      "5",
                "Opps Today":         "0",
                "Opportunities/MTD":  "48 | $91.13%",
            },
        },
        "headers": [
            "Sales Rep", "Monthly Target", "MTD Target",
            "Booked Sales", "% of Target", "Submitted Sales",
        ],
        "rows": [
            {
                "Sales Rep":       "Heidi Lopez-Mix",
                "Monthly Target":  "$130,000",
                "MTD Target":      "$125,806",
                "MTD Target_flag": True,
                "Booked Sales":    "$164,876",
                "% of Target":     "126.8%",
                "Submitted Sales": "$76,874",
            },
            {
                "Sales Rep":       "Kiley Gustafson",
                "Monthly Target":  "$50,000",
                "MTD Target":      "$48,387",
                "MTD Target_flag": True,
                "Booked Sales":    "$89,945",
                "% of Target":     "179.9%",
                "Submitted Sales": "$29,805",
            },
            {
                "Sales Rep":       "Tricia Carlson",
                "Monthly Target":  "$60,000",
                "MTD Target":      "$58,064",
                "MTD Target_flag": False,
                "Booked Sales":    "$36,036",
                "% of Target":     "60.1%",
                "Submitted Sales": "$18,919",
            },
            {
                "Sales Rep":       "Voshte Demmert-G.",
                "Monthly Target":  "$109,000",
                "MTD Target":      "$105,483",
                "MTD Target_flag": False,
                "Booked Sales":    "$95,938",
                "% of Target":     "88.0%",
                "Submitted Sales": "$80,943",
            },
        ],
    }

    out = render(DUMMY, cfg)
    print(f"Preview: {out}")
