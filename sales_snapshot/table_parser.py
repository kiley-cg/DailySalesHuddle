"""
table_parser.py
Parses the Color Graphics Daily Leaderboard email from Atease Systems.

Extracts three sections:
  1. YTD summary row  → metrics[0]  (Sales YTD, Growth, Prior Year YTD)
  2. KPI block        → metrics[1]  (Sales/MTD, Calls/MTD, Victories/MTD, Opportunities/MTD)
  3. Sales Targets table → headers + rows
     Columns: Sales Rep | Monthly Target | MTD Target | Booked Sales | % of Target | Submitted Sales
     MTD Target column: image indicator
       table_ontarget.gif  → on_target = True
       table_offtarget.gif → on_target = False
"""

import logging
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(node) -> str:
    """Return stripped text content of a BS4 node."""
    if node is None:
        return ""
    return node.get_text(separator=" ", strip=True)


def _clean(value: str) -> str:
    """Collapse whitespace and remove non-breaking spaces."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ").replace("&nbsp;", " ")).strip()


def _find_img_indicator(cell: Tag) -> bool | None:
    """
    Return True if the cell contains table_ontarget.gif,
    False if table_offtarget.gif, None if no indicator found.
    """
    img = cell.find("img")
    if img is None:
        return None
    src = img.get("src", "").lower()
    if "ontarget" in src:
        return True
    if "offtarget" in src:
        return False
    return None


def _find_img_and_text(cell: Tag) -> tuple[bool | None, str]:
    """
    Return (on_target_flag, dollar_text) from a MTD Target cell.
    The Atease email puts an indicator image AND a dollar value in this cell.
    """
    flag = _find_img_indicator(cell)

    # Remove the img tag temporarily to extract just the text
    for img in cell.find_all("img"):
        img.decompose()
    text = _clean(_text(cell))
    return flag, text


# ---------------------------------------------------------------------------
# Section 1 — YTD summary
# ---------------------------------------------------------------------------

def _extract_ytd_summary(soup: BeautifulSoup) -> dict:
    """
    Looks for a row/cell block containing labels like 'Sales YTD', 'Growth',
    'Prior Year YTD'.  Returns a dict with those keys.
    """
    result = {"Sales YTD": "", "Growth": "", "Prior Year YTD": ""}

    # Try to find by label text proximity
    for label_node in soup.find_all(string=re.compile(r"Sales\s+YTD", re.I)):
        parent_row = label_node.find_parent("tr")
        if parent_row:
            cells = parent_row.find_all(["td", "th"])
            # Grab the value cell immediately after the label cell
            for i, cell in enumerate(cells):
                if re.search(r"Sales\s+YTD", _text(cell), re.I):
                    if i + 1 < len(cells):
                        result["Sales YTD"] = _clean(_text(cells[i + 1]))
                elif re.search(r"Growth", _text(cell), re.I):
                    if i + 1 < len(cells):
                        result["Growth"] = _clean(_text(cells[i + 1]))
                elif re.search(r"Prior\s+Year", _text(cell), re.I):
                    if i + 1 < len(cells):
                        result["Prior Year YTD"] = _clean(_text(cells[i + 1]))
            if any(result.values()):
                break

    # Fallback: scan all text for dollar amounts near these labels
    if not any(result.values()):
        for tag in soup.find_all(["td", "th", "div", "span", "p"]):
            txt = _clean(_text(tag))
            if re.search(r"Sales\s+YTD", txt, re.I):
                # Try sibling
                sibling = tag.find_next_sibling()
                if sibling:
                    result["Sales YTD"] = _clean(_text(sibling))
            elif re.search(r"Prior\s+Year\s+YTD", txt, re.I):
                sibling = tag.find_next_sibling()
                if sibling:
                    result["Prior Year YTD"] = _clean(_text(sibling))
            elif re.search(r"\bGrowth\b", txt, re.I) and not result["Growth"]:
                sibling = tag.find_next_sibling()
                if sibling:
                    result["Growth"] = _clean(_text(sibling))

    log.debug("YTD summary: %s", result)
    return result


# ---------------------------------------------------------------------------
# Section 2 — KPI block
# ---------------------------------------------------------------------------

_KPI_LABELS = {
    # Today values
    "Sales Today":       re.compile(r"Sales\s+Today", re.I),
    "Calls Today":       re.compile(r"Calls\s+Today", re.I),
    "Victories Today":   re.compile(r"Victories\s+Today", re.I),
    "Opps Today":        re.compile(r"Opps?\s+Today|Opportunities\s+Today", re.I),
    # MTD values
    "Sales/MTD":         re.compile(r"Sales\s*(MTD|/\s*MTD)", re.I),
    "Calls/MTD":         re.compile(r"Calls\s*(MTD|/\s*MTD)", re.I),
    "Victories/MTD":     re.compile(r"Victories\s*(MTD|/\s*MTD)", re.I),
    "Opportunities/MTD": re.compile(r"Opps?\s*(MTD|/\s*MTD)|Opportunities\s*(MTD|/\s*MTD)", re.I),
}


def _extract_kpi_block(soup: BeautifulSoup) -> dict:
    """
    Extract KPI values — both Today and MTD — from the email KPI block.
    The Atease email typically lays these out in a table with label/value pairs
    arranged horizontally or vertically.
    """
    result = {k: "" for k in _KPI_LABELS}

    # Strategy 1: find each label as a text node and grab adjacent value
    for kpi_name, pattern in _KPI_LABELS.items():
        for node in soup.find_all(string=pattern):
            parent = node.find_parent(["td", "th", "div", "span"])
            if parent is None:
                continue
            for candidate in [
                parent.find_next_sibling(),
                parent.parent.find_next_sibling() if parent.parent else None,
            ]:
                if candidate:
                    val = _clean(_text(candidate))
                    if val and val != _clean(_text(parent)):
                        result[kpi_name] = val
                        break
            if result[kpi_name]:
                break

    # Strategy 2: scan all cells for known label+value in same cell (e.g. "Sales Today: $x")
    for kpi_name, pattern in _KPI_LABELS.items():
        if result[kpi_name]:
            continue
        for cell in soup.find_all(["td", "th", "div", "span", "p"]):
            txt = _clean(_text(cell))
            if pattern.search(txt):
                # Try to extract a number/dollar from the same cell text
                remainder = pattern.sub("", txt).strip().lstrip(":").strip()
                if remainder and re.search(r"[\d$]", remainder):
                    result[kpi_name] = remainder
                    break

    log.debug("KPI block: %s", result)
    return result


# ---------------------------------------------------------------------------
# Section 3 — Sales Targets table
# ---------------------------------------------------------------------------

_TARGETS_COLS = [
    "Sales Rep",
    "Monthly Target",
    "MTD Target",
    "Booked Sales",
    "% of Target",
    "Submitted Sales",
]

_COL_PATTERNS = {
    "Sales Rep": re.compile(r"Sales\s+Rep|Rep\s+Name|Name", re.I),
    "Monthly Target": re.compile(r"Monthly\s+Target|Month\s+Target", re.I),
    "MTD Target": re.compile(r"MTD\s+Target|On\s+Target", re.I),
    "Booked Sales": re.compile(r"Booked\s+Sales|Booked", re.I),
    "% of Target": re.compile(r"%\s+of\s+Target|Pct|Percent|%", re.I),
    "Submitted Sales": re.compile(r"Submitted\s+Sales|Submitted", re.I),
}


def _find_targets_table(soup: BeautifulSoup) -> Tag | None:
    """
    Locate the Sales Targets table by looking for a table that contains
    at least 3 of the expected column headers.
    """
    for table in soup.find_all("table"):
        header_text = _clean(_text(table)).lower()
        matches = sum(
            1
            for pat in _COL_PATTERNS.values()
            if pat.search(header_text)
        )
        if matches >= 3:
            return table

    # Fallback: largest table by cell count
    tables = soup.find_all("table")
    if tables:
        return max(tables, key=lambda t: len(t.find_all(["td", "th"])))
    return None


def _map_columns(header_cells: list[Tag]) -> dict[str, int]:
    """Return {canonical_col_name: column_index} for recognised headers."""
    col_map = {}
    for i, cell in enumerate(header_cells):
        txt = _clean(_text(cell))
        # Check for image-only header (MTD Target column)
        img = cell.find("img")
        if img and not txt:
            col_map["MTD Target"] = i
            continue
        for col_name, pat in _COL_PATTERNS.items():
            if pat.search(txt) and col_name not in col_map:
                col_map[col_name] = i
                break
    return col_map


def _extract_targets_table(soup: BeautifulSoup) -> tuple[list[str], list[dict]]:
    """Return (headers_list, rows_list_of_dicts) for the Sales Targets table."""
    table = _find_targets_table(soup)
    if table is None:
        log.warning("No Sales Targets table found.")
        return _TARGETS_COLS, []

    # Separate header rows from data rows
    all_rows = table.find_all("tr")
    header_row = None
    data_rows = []

    for row in all_rows:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        # Identify header row: contains known column names or all <th>
        row_text = " ".join(_clean(_text(c)) for c in cells)
        matches = sum(1 for pat in _COL_PATTERNS.values() if pat.search(row_text))
        if matches >= 3 and header_row is None:
            header_row = cells
        elif header_row is not None:
            data_rows.append(cells)

    if header_row is None:
        # Try first row as header
        if all_rows:
            header_row = all_rows[0].find_all(["th", "td"])
            data_rows = [r.find_all(["th", "td"]) for r in all_rows[1:] if r.find_all(["th", "td"])]

    col_map = _map_columns(header_row) if header_row else {}
    log.debug("Column map: %s", col_map)

    rows = []
    for cells in data_rows:
        if not cells:
            continue
        row_dict: dict[str, Any] = {}
        for col_name in _TARGETS_COLS:
            idx = col_map.get(col_name)
            if idx is None or idx >= len(cells):
                row_dict[col_name] = "" if col_name != "MTD Target" else None
                continue
            cell = cells[idx]
            if col_name == "MTD Target":
                flag, dollar_text = _find_img_and_text(cell)
                row_dict["MTD Target"] = dollar_text          # e.g. "$125,806"
                row_dict["MTD Target_flag"] = flag            # True/False/None
            else:
                row_dict[col_name] = _clean(_text(cell))

        # Skip empty rows (no rep name and no values)
        if not any(str(v).strip() for v in row_dict.values()):
            continue
        rows.append(row_dict)

    log.info("Extracted %d rows from Sales Targets table.", len(rows))
    return _TARGETS_COLS, rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(html_body: str) -> dict:
    """
    Parse the Atease leaderboard email HTML.

    Returns:
    {
        "metrics": {
            "ytd": {"Sales YTD": str, "Growth": str, "Prior Year YTD": str},
            "kpi": {"Sales/MTD": str, "Calls/MTD": str,
                    "Victories/MTD": str, "Opportunities/MTD": str},
        },
        "headers": [str, ...],
        "rows": [
            {
                "Sales Rep": str,
                "Monthly Target": str,
                "MTD Target": bool | None,   # True=on target, False=behind
                "Booked Sales": str,
                "% of Target": str,
                "Submitted Sales": str,
            },
            ...
        ],
    }
    """
    soup = BeautifulSoup(html_body, "lxml")

    ytd = _extract_ytd_summary(soup)
    kpi = _extract_kpi_block(soup)
    headers, rows = _extract_targets_table(soup)

    return {
        "metrics": {"ytd": ytd, "kpi": kpi},
        "headers": headers,
        "rows": rows,
    }
