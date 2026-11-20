"""Pure geometry, grid evaluation, decision persistence and export order.

This module has no Flask or SQLite imports so every rule can be unit-tested in
isolation.  The web layer is a thin shell around these functions.
"""

from __future__ import annotations

import math

EPS = 0.01
COORD_DIGITS = 2
MAX_LINES = 500

COLUMNS = ("name", "spec", "price")
COLUMN_LABELS = {"name": "品名", "spec": "规格", "price": "批发价"}

STATUS_LABELS = {
    "assigned_auto": "自动归位",
    "assigned_manual": "人工归位",
    "pending": "待核",
    "stale": "待重新确认",
    "excluded": "已排除",
}


class LayoutError(ValueError):
    """The submitted ruling lines cannot produce the required 3-column grid."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = list(details or [])


def fmt(value):
    """Stable text form of a coordinate, rounded so float noise is ignored."""
    number = round(float(value), COORD_DIGITS)
    if abs(number) < 0.5 * 10 ** -COORD_DIGITS:
        number = 0.0
    text = f"{number:.{COORD_DIGITS}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _as_number(value, label):
    if isinstance(value, bool):
        raise LayoutError(f"{label}必须是数字")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise LayoutError(f"{label}“{value}”不是有效数字") from None
    else:
        raise LayoutError(f"{label}必须是数字")
    if not math.isfinite(number):
        raise LayoutError(f"{label}必须是有限数字")
    if abs(number) > 1e9:
        raise LayoutError(f"{label}超出可处理范围")
    return round(number, COORD_DIGITS)


def normalize_positions(raw, label):
    """Validate a coordinate list and return sorted unique values."""
    if not isinstance(raw, (list, tuple)):
        raise LayoutError(f"{label}必须是数组")
    if len(raw) > MAX_LINES:
        raise LayoutError(f"{label}数量过多（最多 {MAX_LINES} 条）")
    return sorted({_as_number(item, label) for item in raw})


def normalize_layout(horizontal, vertical):
    """Validate the line set that defines the 3-column price table.

    Exactly four vertical lines are required (left border + two separators +
    right border).  Any number of horizontal row lines is allowed as long as
    at least two exist (page top and bottom / one row).
    """
    h = normalize_positions(horizontal, "横线")
    v = normalize_positions(vertical, "竖线")
    errors = []
    if len(v) != 4:
        errors.append("竖线必须恰好 4 条，才能划出品名、规格、批发价三列")
    if len(h) < 2:
        errors.append("至少需要 2 条横线才能形成行")
    if errors:
        raise LayoutError("格线无效：" + "；".join(errors), errors)
    return h, v


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def box_bounds(box):
    x1, y1, x2, y2 = (float(box["x1"]), float(box["y1"]),
                      float(box["x2"]), float(box["y2"]))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def overlap_size(box, cell):
    ax1, ay1, ax2, ay2 = box
    bx1, by1, bx2, by2 = cell
    width = min(ax2, bx2) - max(ax1, bx1)
    height = min(ay2, by2) - max(ay1, by1)
    if width <= EPS or height <= EPS:
        return 0.0
    return width * height


def overlap_ratio(box, cell):
    x1, y1, x2, y2 = box
    area = (x2 - x1) * (y2 - y1)
    if area <= EPS:
        return 0.0
    return overlap_size(box, cell) / area


def cells_overlap(box, cell):
    return overlap_size(box, cell) > EPS


class Grid:
    """Ruling-line grid.  Lines run the full width/height of the table."""

    def __init__(self, horizontal, vertical):
        self.h = list(horizontal)
        self.v = list(vertical)

    @property
    def rows(self):
        return max(len(self.h) - 1, 0)

    @property
    def cols(self):
        return max(len(self.v) - 1, 0)

    @property
    def valid(self):
        return self.rows >= 1 and self.cols == 3

    def cell_rect(self, row, col):
        return (self.v[col], self.h[row], self.v[col + 1], self.h[row + 1])

    @staticmethod
    def make_cell_key(left, top, right, bottom):
        return f"{fmt(left)}|{fmt(top)}|{fmt(right)}|{fmt(bottom)}"

    def cell_key(self, row, col):
        return self.make_cell_key(*self.cell_rect(row, col))

    def cells(self):
        result = []
        for row in range(self.rows):
            for col in range(self.cols):
                left, top, right, bottom = self.cell_rect(row, col)
                result.append({
                    "row": row,
                    "col": col,
                    "column": COLUMNS[col],
                    "key": self.cell_key(row, col),
                    "rect": [left, top, right, bottom],
                })
        return result

    def find_cell(self, cell_key):
        """Return (row, col) for a cell key, or None if that cell is gone."""
        for row in range(self.rows):
            for col in range(self.cols):
                if self.cell_key(row, col) == cell_key:
                    return row, col
        return None

    def containing_cell(self, box):
        """The single cell fully containing the box, if any.

        A zero-area box cannot "fall inside" anything meaningful: it is
        treated as unplaced so a human has to look at it.
        """
        x1, y1, x2, y2 = box
        if x2 - x1 <= EPS or y2 - y1 <= EPS:
            return None
        for row in range(self.rows):
            if y1 < self.h[row] - EPS or y2 > self.h[row + 1] + EPS:
                continue
            for col in range(self.cols):
                if x1 >= self.v[col] - EPS and x2 <= self.v[col + 1] + EPS:
                    return row, col
        return None

    def overlapping_cells(self, box):
        """All cells with positive overlap, ordered by overlap ratio desc.

        Ties are broken row-major so the result is deterministic.
        """
        found = []
        for row in range(self.rows):
            for col in range(self.cols):
                ratio = overlap_ratio(box, self.cell_rect(row, col))
                if ratio > 0.0:
                    found.append((ratio, row, col))
        found.sort(key=lambda item: (-item[0], item[1], item[2]))
        return found

    def overlapping_keys(self, box):
        """Sorted coordinate keys of every cell touching the box."""
        return sorted(self.cell_key(row, col)
                      for _ratio, row, col in self.overlapping_cells(box))


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(grid, boxes, decisions):
    """Annotate every box with its current placement.

    decisions maps box id -> dict(kind, target_cell, overlap_cells).

    Ordering of the rules:
      1. a human decision is reused only while the set of cells intersecting
         the box is byte-for-byte identical; otherwise it returns to pending;
      2. a box wholly inside one cell is auto-placed;
      3. everything else is pending and needs a human choice.

    Blank-text boxes are pending as well: silently dropping them would let an
    OCR miss slip through the export.  They can be excluded like any other box.
    """
    output = []
    for box in boxes:
        bounds = box_bounds(box)
        text = (box["text"] or "").strip()
        candidates = grid.overlapping_cells(bounds)
        item = {
            "box_id": box["id"],
            "box_ref": box["box_ref"],
            "text": text,
            "x1": bounds[0],
            "y1": bounds[1],
            "x2": bounds[2],
            "y2": bounds[3],
            "status": "pending",
            "status_label": STATUS_LABELS["pending"],
            "row": None,
            "col": None,
            "column": None,
            "cell_key": "",
            "candidates": [
                {
                    "row": row,
                    "col": col,
                    "column": COLUMNS[col],
                    "ratio": round(ratio, 4),
                    "key": grid.cell_key(row, col),
                    "rect": list(grid.cell_rect(row, col)),
                }
                for ratio, row, col in candidates
            ],
            "decision": None,
            "manual": False,
            "note": "",
        }

        decision = decisions.get(box["id"])

        # An exclusion is a deliberate statement about the content, so it
        # outranks auto-placement: a box the user excluded must never come
        # back into the export just because the lines around it changed.
        if decision is not None and decision.get("kind") == "exclude":
            item["decision"] = decision
            if grid.overlapping_keys(bounds) != sorted(decision.get("overlap_cells") or []):
                item["status"] = "stale"
                item["status_label"] = STATUS_LABELS["stale"]
                item["note"] = "格线已变动，请重新确认是否排除"
            else:
                item["status"] = "excluded"
                item["status_label"] = STATUS_LABELS["excluded"]
                item["manual"] = True
                item["note"] = "已人工排除"
            output.append(item)
            continue

        # A box that now sits wholly inside one cell needs no human input,
        # even if it once carried a cell decision: auto-placement is
        # unambiguous, so the old choice is simply superseded.
        containing = grid.containing_cell(bounds)
        if containing is not None:
            row, col = containing
            item.update(status="assigned_auto",
                        status_label=STATUS_LABELS["assigned_auto"],
                        row=row, col=col, column=COLUMNS[col],
                        cell_key=grid.cell_key(row, col))
            if decision is not None:
                item["decision"] = decision
                item["note"] = "格线调整后已完整落在一格内，原人工选择不再需要"
            output.append(item)
            continue

        if decision is not None:
            item["decision"] = decision
            current_keys = grid.overlapping_keys(bounds)
            stored_keys = sorted(decision.get("overlap_cells") or [])
            target_key = decision.get("target_cell") or ""
            if current_keys != stored_keys:
                item["status"] = "stale"
                item["status_label"] = STATUS_LABELS["stale"]
                item["note"] = "格线已变动，请重新选择格子或排除"
                output.append(item)
                continue
            position = grid.find_cell(target_key)
            if position is None or not cells_overlap(bounds, grid.cell_rect(*position)):
                item["status"] = "stale"
                item["status_label"] = STATUS_LABELS["stale"]
                item["note"] = "原选格已不存在，请重新选择格子或排除"
                output.append(item)
                continue
            row, col = position
            item.update(status="assigned_manual",
                        status_label=STATUS_LABELS["assigned_manual"],
                        row=row, col=col, column=COLUMNS[col],
                        cell_key=target_key, manual=True,
                        note="人工归位于第 %d 行·%s" % (row + 1, COLUMN_LABELS[COLUMNS[col]]))
            output.append(item)
            continue

        if candidates:
            item["note"] = "跨格或出界，请选择一个有面积重叠的格子，或排除"
        else:
            item["note"] = "与任何格子都没有面积重叠，只能排除"
        output.append(item)
    return output


def pending_count(evaluated):
    return sum(1 for item in evaluated if item["status"] in ("pending", "stale"))


# ---------------------------------------------------------------------------
# Reading order and export
# ---------------------------------------------------------------------------

def _same_visual_line(a, b):
    """True when two boxes sit on one visual line ("同高").

    Compares vertical centres and shared span.  The tolerance is deliberately
    tight: OCR boxes of one printed line differ by a couple of pixels, while
    adjacent lines of a price table are usually further apart than half a
    character height.
    """
    a_height = max(a["y2"] - a["y1"], EPS)
    b_height = max(b["y2"] - b["y1"], EPS)
    tolerance = max(1.0, 0.5 * min(a_height, b_height))
    a_mid = (a["y1"] + a["y2"]) / 2.0
    b_mid = (b["y1"] + b["y2"]) / 2.0
    if abs(a_mid - b_mid) <= tolerance:
        return True
    # Boxes sharing most of their vertical span also read as one line.
    top = max(a["y1"], b["y1"])
    bottom = min(a["y2"], b["y2"])
    return (bottom - top) >= 0.6 * min(a_height, b_height)


def reading_order(items):
    """Top to bottom; boxes on the same visual line left to right.

    The input is only ever the boxes placed in one cell, so this defines the
    concatenation order of a cell's text ("从上到下、同高从左到右").
    """
    remaining = sorted(items, key=lambda item: (item["y1"], item["x1"], item["box_id"]))
    ordered = []
    while remaining:
        line = [remaining.pop(0)]
        # Grow the line by centre proximity to its first member (primary) or
        # to any member (bridging boxes of equal height).
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                if _same_visual_line(candidate, line[0]) or any(
                    _same_visual_line(candidate, member) for member in line[1:]
                ):
                    line.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        line.sort(key=lambda entry: (entry["x1"], entry["y1"], entry["box_id"]))
        ordered.extend(line)
    return ordered


def build_rows(evaluated):
    """Compile placed boxes into the three-column output rows.

    Returns a list of {row, name, spec, price} in grid-row order.  Blank
    columns stay empty; rows without any text are dropped.
    """
    placed = [item for item in evaluated
              if item["status"] in ("assigned_auto", "assigned_manual")]
    rows = {}
    for item in placed:
        rows.setdefault(item["row"], []).append(item)

    output = []
    for row_index in sorted(rows):
        cells = rows[row_index]
        columns = {"name": [], "spec": [], "price": []}
        for item in cells:
            columns[item["column"]].append(item)
        row = {"row": row_index + 1}
        has_text = False
        for column in COLUMNS:
            text = "".join(entry["text"] for entry in reading_order(columns[column]))
            row[column] = text
            has_text = has_text or bool(text)
        if has_text:
            output.append(row)
    return output


# ---------------------------------------------------------------------------
# Decision validation
# ---------------------------------------------------------------------------


def make_decision(grid, item, kind, target_key=None):
    """Build a decision payload for a pending box, validating the input.

    Raises LayoutError when the chosen cell does not overlap the box.
    """
    bounds = (item["x1"], item["y1"], item["x2"], item["y2"])
    overlap_keys = grid.overlapping_keys(bounds)
    if kind == "exclude":
        return {
            "kind": "exclude",
            "target_cell": "",
            "overlap_cells": overlap_keys,
        }
    if kind != "cell":
        raise LayoutError("未知的人工处理方式")
    if not target_key:
        raise LayoutError("请选择一个格子")
    position = grid.find_cell(target_key)
    if position is None:
        raise LayoutError("所选格子已不存在，请刷新后重试")
    if not cells_overlap(bounds, grid.cell_rect(*position)):
        raise LayoutError("所选格子与该文字框没有面积重叠")
    return {
        "kind": "cell",
        "target_cell": target_key,
        "overlap_cells": overlap_keys,
    }

