from __future__ import annotations
"""
table_parser.py — Atease/Facilis Color Graphics Daily Leaderboard email parser.

Email structure (confirmed from live HTML):
  Table 8  (3 cells)  — YTD summary
  Table 11 (9 cells)  — KPI block (cells 5-8 hold the data)
  Table 27 (30 cells) — Sales Targets (6 header + N×6 data rows)

Finder strategy: keyword match + take innermost (fewest cells) table.
"""

import logging
import re
from typing import Optional, Tuple, List

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

TARGETS_HEADERS = [
    "SALES REP", "MONTHLY TARGET", "MTD TARGET",
    "BOOKED SALES", "% OF TARGET", "SUBMITTED SALES",
]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_dollar(raw: str) -> str:
    """'130,000.00' → '$130,000'  |  '' → ''"""
    s = str(raw).strip().replace("$", "").replace(",", "")
    m = re.match(r"^(-?\d+)(?:\.\d+)?$", s)
    if not m:
        return raw.strip()
    n = int(m.group(1))
    return f"-${abs(n):,}" if n < 0 else f"${n:,}"


def _last_number(text: str) -> str:
    """Extract the last numeric token (inc. minus, %) from a string."""
    hits = re.findall(r"-?[\d,]+(?:\.\d+)?%?", text)
    return hits[-1] if hits else ""


def _cells(table: Tag) -> list:
    return table.find_all(["td", "th"])


# ---------------------------------------------------------------------------
# Table finders  (innermost = fewest cells = least nesting)
# ---------------------------------------------------------------------------

def _find_ytd_table(soup: BeautifulSoup) -> Optional[list]:
    """3-cell table: SALES YTD | GROWTH | SALES PRIOR YTD"""
    candidates = []
    for t in soup.find_all("table"):
        cs = _cells(t)
        if len(cs) != 3:
            continue
        txt = " ".join(c.get_text(" ", strip=True).upper() for c in cs)
        if "SALES YTD" in txt and "GROWTH" in txt:
            candidates.append(cs)
    # Prefer the one whose combined text is shortest (innermost)
    return min(candidates, key=lambda cs: sum(len(c.get_text()) for c in cs)) if candidates else None


def _find_kpi_table(soup: BeautifulSoup) -> Optional[list]:
    """Innermost table containing SALES MTD + CALLS MTD + VICTORIES MTD."""
    for t in sorted(soup.find_all("table"), key=lambda t: len(_cells(t))):
        txt = t.get_text(" ", strip=True).upper()
        if "SALES MTD" in txt and "CALLS MTD" in txt and "VICTORIES MTD" in txt:
            return _cells(t)
    return None


def _find_targets_table(soup: BeautifulSoup) -> Optional[list]:
    """Innermost table whose first 6 cells match the expected column headers."""
    for t in sorted(soup.find_all("table"), key=lambda t: len(_cells(t))):
        cs = _cells(t)
        if len(cs) < 12:
            continue
        first_six = [cs[i].get_text(" ", strip=True).upper().strip() for i in range(6)]
        if first_six == TARGETS_HEADERS:
            return cs
    return None


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_ytd(cells: list) -> dict:
    result = {"Sales YTD": "", "Growth": "", "Prior Year YTD": ""}
    for cell in cells:
        text = cell.get_text(" ", strip=True)
        upper = text.upper()
        val = _last_number(text)
        if "SALES YTD" in upper:
            result["Sales YTD"] = _fmt_dollar(val)
        elif "GROWTH" in upper:
            result["Growth"] = val          # keep as-is: "-1.1%"
        elif "PRIOR YTD" in upper or ("SALES PRIOR" in upper):
            result["Prior Year YTD"] = _fmt_dollar(val)
    log.debug("YTD: %s", result)
    return result


def _parse_kpi(cells: list) -> dict:
    """
    Non-empty cells contain patterns:
      "SALES 29,908.24 SALES MTD 386,797.31"
      "CALLS 0 CALLS MTD 28"
      "VICTORIES 0 VICTORIES MTD 5"
      "OPPORTUNITIES (WEIGHTED) 0 (0.00) OPPORTUNITIES MTD (WEIGHTED) 46 (61,135.00)"
    """
    result = {
        "Sales Today": "", "Sales/MTD": "",
        "Calls Today": "", "Calls/MTD": "",
        "Victories Today": "", "Victories/MTD": "",
        "Opps Today": "", "Opportunities/MTD": "",
    }

    for cell in cells:
        text = cell.get_text(" ", strip=True)
        if not text:
            continue
        up = text.upper()

        if "SALES" in up and "CALLS" not in up and "VICTORIES" not in up and "OPPORTUNITIES" not in up:
            # "SALES 29,908.24 SALES MTD 386,797.31"
            m1 = re.search(r"SALES\s+([\d,]+(?:\.\d+)?)", text, re.I)
            m2 = re.search(r"SALES\s+MTD\s+([\d,]+(?:\.\d+)?)", text, re.I)
            if m1:
                result["Sales Today"] = _fmt_dollar(m1.group(1))
            if m2:
                result["Sales/MTD"] = _fmt_dollar(m2.group(1))

        elif "CALLS" in up:
            # "CALLS 0 CALLS MTD 28"
            m1 = re.search(r"^CALLS\s+(\d+)", text, re.I)
            m2 = re.search(r"CALLS\s+MTD\s+(\d+)", text, re.I)
            if m1:
                result["Calls Today"] = m1.group(1)
            if m2:
                result["Calls/MTD"] = m2.group(1)

        elif "VICTORIES" in up:
            # "VICTORIES 0 VICTORIES MTD 5"
            m1 = re.search(r"^VICTORIES\s+(\d+)", text, re.I)
            m2 = re.search(r"VICTORIES\s+MTD\s+(\d+)", text, re.I)
            if m1:
                result["Victories Today"] = m1.group(1)
            if m2:
                result["Victories/MTD"] = m2.group(1)

        elif "OPPORTUNITIES" in up:
            # "OPPORTUNITIES (WEIGHTED) 0 (0.00) OPPORTUNITIES MTD (WEIGHTED) 46 (61,135.00)"
            m1 = re.search(r"OPPORTUNITIES\s+(?:\(WEIGHTED\)\s+)?(\d+)", text, re.I)
            m2 = re.search(
                r"OPPORTUNITIES\s+MTD\s+(?:\(WEIGHTED\)\s+)?(\d+)\s+\(([\d,]+(?:\.\d+)?)\)",
                text, re.I,
            )
            if m1:
                result["Opps Today"] = m1.group(1)
            if m2:
                count = m2.group(1)
                weighted = _fmt_dollar(m2.group(2))
                result["Opportunities/MTD"] = f"{count} | {weighted}"

    log.debug("KPI: %s", result)
    return result


def _parse_targets(cells: list) -> Tuple[List[str], List[dict]]:
    """
    cells[0:6]  = header row
    cells[6:]   = data rows in groups of 6
    Column order: Sales Rep | Monthly Target | MTD Target | Booked Sales | % of Target | Submitted Sales
    MTD Target cell contains on/off-target image.
    """
    headers = [
        "Sales Rep", "Monthly Target", "MTD Target",
        "Booked Sales", "% of Target", "Submitted Sales",
    ]
    rows = []

    data = cells[6:]
    for i in range(0, len(data) - 5, 6):
        chunk = data[i : i + 6]
        rep = chunk[0].get_text(" ", strip=True)
        if not rep:
            continue

        # On/off-target flag from MTD cell image
        img = chunk[2].find("img")
        if img:
            src = img.get("src", "").lower()
            flag = True if "ontarget" in src else (False if "offtarget" in src else None)
        else:
            flag = None

        mtd_val = chunk[2].get_text(" ", strip=True)

        rows.append({
            "Sales Rep":       rep,
            "Monthly Target":  _fmt_dollar(chunk[1].get_text(strip=True)),
            "MTD Target":      _fmt_dollar(mtd_val) if mtd_val else "",
            "MTD Target_flag": flag,
            "Booked Sales":    _fmt_dollar(chunk[3].get_text(strip=True)),
            "% of Target":     chunk[4].get_text(strip=True),
            "Submitted Sales": _fmt_dollar(chunk[5].get_text(strip=True)),
        })

    log.info("Parsed %d sales rep rows.", len(rows))
    return headers, rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(html_body: str) -> dict:
    """
    Parse the Atease/Facilis daily leaderboard email.

    Returns:
    {
        "metrics": {
            "ytd": {"Sales YTD": str, "Growth": str, "Prior Year YTD": str},
            "kpi": {
                "Sales Today": str, "Sales/MTD": str,
                "Calls Today": str, "Calls/MTD": str,
                "Victories Today": str, "Victories/MTD": str,
                "Opps Today": str, "Opportunities/MTD": str,
            },
        },
        "headers": [str × 6],
        "rows": [{Sales Rep, Monthly Target, MTD Target, MTD Target_flag,
                  Booked Sales, % of Target, Submitted Sales}, ...],
    }
    """
    soup = BeautifulSoup(html_body, "lxml")

    ytd_cells = _find_ytd_table(soup)
    kpi_cells = _find_kpi_table(soup)
    targets_cells = _find_targets_table(soup)

    ytd = _parse_ytd(ytd_cells) if ytd_cells else {}
    kpi = _parse_kpi(kpi_cells) if kpi_cells else {}
    headers, rows = _parse_targets(targets_cells) if targets_cells else ([], [])

    return {"metrics": {"ytd": ytd, "kpi": kpi}, "headers": headers, "rows": rows}
