"""Parser for single-page OCR CSV files.

Accepted shapes (headers are matched case-insensitively and may be Chinese or
English, with spaces/underscores/punctuation ignored):

   框号,文字,x1,y1,x2,y2
   框号,文字,矩形坐标
   框号,文字,left,top,right,bottom
   框号,文字,x,y,宽,高

The rectangle coordinates may be written as four separate columns or packed
into one column (``1,2,100,50`` / ``[1,2,100,50]``).  A CSV without a header
is read positionally as 框号, 文字, x1, y1, x2, y2.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

BOX_ALIASES = {
    "框号", "框", "框编号", "编号", "区块号", "box", "boxid", "boxno",
    "boxnumber", "boxindex", "id", "no", "index",
}
TEXT_ALIASES = {
    "文字", "文本", "内容", "识别文字", "ocr文字", "text", "content",
    "ocr", "ocrtext", "word", "words", "value",
}
RECT_ALIASES = {
    "矩形坐标", "矩形", "坐标", "四点坐标", "框坐标", "位置", "rect",
    "rectangle", "bbox", "boxcoords", "coords", "coordinates",
    "boundingbox", "rectcoords",
}
X1_ALIASES = {"x", "x1", "left", "xmin", "x0", "leftx", "左", "左边界", "左上x", "minx", "startx"}
Y1_ALIASES = {"y", "y1", "top", "ymin", "y0", "topy", "上", "上边界", "左上y", "miny", "starty"}
X2_ALIASES = {"x2", "right", "xmax", "右", "右边界", "右下x", "maxx", "endx"}
Y2_ALIASES = {"y2", "bottom", "ymax", "下", "下边界", "右下y", "maxy", "endy"}
W_ALIASES = {"w", "width", "宽", "宽度"}
H_ALIASES = {"h", "height", "高", "高度"}

ENCODINGS = ("utf-8-sig", "gb18030", "utf-8", "big5")

NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


class CsvError(ValueError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = list(details or [])


@dataclass
class ParsedCsv:
    boxes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    headers: list = field(default_factory=list)


def _normalise(name):
    text = str(name or "").strip().lower()
    text = text.replace("﻿", "")
    return re.sub(r"[\s_\-:：()（）\[\]【】.]+", "", text)


def _decode(raw):
    if isinstance(raw, str):
        return raw
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _find_column(normalised, aliases):
    for index, name in enumerate(normalised):
        if name in aliases:
            return index
    return None


def _parse_numbers(value):
    return [float(match) for match in NUMBER_RE.findall(str(value or ""))]


def _looks_like_header(row):
    """A header row contains no numbers in its coordinate-ish columns."""
    if not row:
        return False
    joined = " ".join(str(cell) for cell in row)
    if NUMBER_RE.search(joined) and not any(
        _normalise(cell) in (BOX_ALIASES | TEXT_ALIASES | RECT_ALIASES)
        for cell in row
    ):
        # All-numeric rows are data; a single numeric token (like a 框号) does
        # not disqualify a real header, hence the alias check above.
        numeric_cells = sum(1 for cell in row if NUMBER_RE.fullmatch(str(cell).strip()))
        if numeric_cells >= max(2, len(row) - 1):
            return False
    return any(_normalise(cell) in (BOX_ALIASES | TEXT_ALIASES | RECT_ALIASES)
               for cell in row)


def _column_map(header):
    normalised = [_normalise(cell) for cell in header]
    mapping = {
        "box": _find_column(normalised, BOX_ALIASES),
        "text": _find_column(normalised, TEXT_ALIASES),
        "rect": _find_column(normalised, RECT_ALIASES),
        "x1": _find_column(normalised, X1_ALIASES),
        "y1": _find_column(normalised, Y1_ALIASES),
        "x2": _find_column(normalised, X2_ALIASES),
        "y2": _find_column(normalised, Y2_ALIASES),
        "w": _find_column(normalised, W_ALIASES),
        "h": _find_column(normalised, H_ALIASES),
    }
    if mapping["text"] is None:
        raise CsvError("CSV 表头中找不到“文字”列",
                       [f"已识别表头：{', '.join(map(str, header))}"])
    has_rect = mapping["rect"] is not None
    has_xy = all(mapping[key] is not None for key in ("x1", "y1", "x2", "y2"))
    has_xywh = all(mapping[key] is not None for key in ("x1", "y1", "w", "h"))
    if not (has_rect or has_xy or has_xywh):
        raise CsvError(
            "CSV 表头中找不到矩形坐标列",
            ["需要“矩形坐标”一列（4 个数字），"
             "或 x1/y1/x2/y2、left/top/right/bottom、x/y/宽/高 四列"],
        )
    mapping["mode"] = "rect" if has_rect else ("xy" if has_xy else "xywh")
    return mapping


def _rect_from_row(row, mapping, line_number):
    def cell(key):
        index = mapping.get(key)
        if index is None or index >= len(row):
            return None
        return row[index]

    if mapping["mode"] == "rect":
        numbers = _parse_numbers(cell("rect"))
        if len(numbers) != 4:
            raise CsvError(f"第 {line_number} 行的矩形坐标不是 4 个数字")
        x1, y1, x2, y2 = numbers
    elif mapping["mode"] == "xy":
        values = [cell("x1"), cell("y1"), cell("x2"), cell("y2")]
        try:
            x1, y1, x2, y2 = (float(str(value).strip()) for value in values)
        except (TypeError, ValueError):
            raise CsvError(f"第 {line_number} 行的坐标不是有效数字") from None
    else:
        try:
            x = float(str(cell("x1")).strip())
            y = float(str(cell("y1")).strip())
            width = float(str(cell("w")).strip())
            height = float(str(cell("h")).strip())
        except (TypeError, ValueError):
            raise CsvError(f"第 {line_number} 行的坐标不是有效数字") from None
        x1, y1, x2, y2 = x, y, x + width, y + height

    if any(value != value or abs(value) == float("inf") for value in (x1, y1, x2, y2)):
        raise CsvError(f"第 {line_number} 行的坐标无效")
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def parse_csv(raw):
    """Parse OCR CSV bytes/str into normalised box rows.

    Raises CsvError when the file has no usable rows or the header is
    unrecognisable.  Recoverable per-row problems become warnings and the row
    is skipped, so one malformed line does not lose an entire OCR page.
    """
    text = _decode(raw)
    if not text.strip():
        raise CsvError("文件为空")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    try:
        rows = list(reader)
    except csv.Error as error:
        raise CsvError(f"CSV 解析失败：{error}") from None

    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not rows:
        raise CsvError("文件为空")

    first = rows[0]
    if _looks_like_header(first):
        header = first
        data_rows = rows[1:]
        mapping = _column_map(header)
        offset = 2
    else:
        header = ["框号", "文字", "x1", "y1", "x2", "y2"]
        data_rows = rows
        mapping = {"box": 0, "text": 1, "rect": None,
                   "x1": 2, "y1": 3, "x2": 4, "y2": 5,
                   "w": None, "h": None, "mode": "xy"}
        offset = 1

    boxes = []
    warnings = []
    for index, row in enumerate(data_rows):
        line_number = index + offset
        if not any(str(cell).strip() for cell in row):
            continue
        try:
            x1, y1, x2, y2 = _rect_from_row(row, mapping, line_number)
        except CsvError as error:
            warnings.append(error.message)
            continue
        box_index = mapping.get("box")
        box_ref = ""
        if box_index is not None and box_index < len(row):
            box_ref = str(row[box_index]).strip()
        text_index = mapping.get("text")
        content = ""
        if text_index is not None and text_index < len(row):
            content = str(row[text_index]).strip()
        if not box_ref:
            box_ref = str(len(boxes) + 1)
        if not content:
            warnings.append(f"第 {line_number} 行文字为空，已保留为待排除框")
        boxes.append({
            "row_no": len(boxes) + 1,
            "box_ref": box_ref,
            "text": content,
            "x1": round(x1, 2),
            "y1": round(y1, 2),
            "x2": round(x2, 2),
            "y2": round(y2, 2),
        })

    if not boxes:
        raise CsvError("没有可用的文字框", warnings)
    return ParsedCsv(boxes=boxes, warnings=warnings, headers=[str(c) for c in header])
