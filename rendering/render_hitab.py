"""
HiTab rendering script

Usage:
    python render_hitab.py --input-dir /path/to/hitab --output-dir ./outputs/hitab
"""

import argparse
import hashlib
import html
import os
import re
import json
import math
import random
from typing import List, Tuple, Dict, Any

import PIL
from PIL import Image, ImageDraw, ImageFont



TABLES_DIR = ""
OUTPUT_DIR = "./outputs/hitab"

OUTPUT_IMAGE_EXT = ".jpg"

ARTIFACT_DIR = os.path.join(OUTPUT_DIR, "artifacts")
MODIFIED_JSON_DIR = os.path.join(ARTIFACT_DIR, "modified_json")
MODIFIED_HTML_DIR = os.path.join(ARTIFACT_DIR, "modified_html")
EDIT_META_DIR = os.path.join(ARTIFACT_DIR, "edit_meta")


RANDOM_SEED = 42

PAGE_MARGIN_X = 26
PAGE_MARGIN_Y = 24

CELL_PAD_X = 12
CELL_PAD_Y = 8


FONT_PATH_REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/times.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "C:/Windows/Fonts/times.ttf",
    "C:/Windows/Fonts/timesnewroman.ttf",
]

FONT_PATH_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/timesbd.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
    "C:/Windows/Fonts/timesnewromanbd.ttf",
]

HEADER_FONT_SIZE = 19
BODY_FONT_SIZE = 19
MULTILINE_SPACING = 4

MIN_COL_WIDTH = 95
MIN_FIRST_COL_WIDTH = 150
MIN_HEADER_ROW_HEIGHT = 38
MIN_DATA_ROW_HEIGHT = 36

PAGE_BG_COLOR = (255, 255, 255)
TEXT_COLOR = (0, 0, 0)

BORDER_COLOR = (70, 70, 70)
BORDER_WIDTH = 2
HEADER_LINE_COLOR = (85, 85, 85)
HEADER_LINE_WIDTH = 2
LEFT_HEADER_LINE_COLOR = (95, 95, 95)
LEFT_HEADER_LINE_WIDTH = 2

COLOR_FILL_PROB = 0.80
BODY_FILL_CELL_MIN = 2
BODY_FILL_CELL_MAX = 8

BRIGHT_FILL_PALETTE = [
    (255, 236, 179),
    (255, 205, 210),
    (197, 225, 165),
    (178, 223, 219),
    (187, 222, 251),
    (209, 196, 233),
    (255, 224, 178),
    (255, 245, 157),
    (128, 222, 234),
    (220, 237, 200),
    (244, 143, 177),
    (129, 199, 132),
    (100, 181, 246),
]

ERASE_RATIO = 0.40
MIN_ERASE_ONE_IF_POSSIBLE = True

NUMERIC_RATIO_THRESHOLD = 0.70
MIN_NONEMPTY_CELLS_FOR_NUMERIC_COL = 3

def configure_output(output_dir: str):
    global OUTPUT_DIR, ARTIFACT_DIR, MODIFIED_JSON_DIR, MODIFIED_HTML_DIR, EDIT_META_DIR
    OUTPUT_DIR = os.path.abspath(output_dir)
    ARTIFACT_DIR = os.path.join(OUTPUT_DIR, "artifacts")
    MODIFIED_JSON_DIR = os.path.join(ARTIFACT_DIR, "modified_json")
    MODIFIED_HTML_DIR = os.path.join(ARTIFACT_DIR, "modified_html")
    EDIT_META_DIR = os.path.join(ARTIFACT_DIR, "edit_meta")


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def normalize_space(text: Any) -> str:
    s = "" if text is None else str(text)
    s = s.replace("\u00a0", " ")
    s = s.replace("\ufeff", "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u2028", "\n").replace("\u2029", "\n")
    s = re.sub(r"[^\S\n]+", " ", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def save_json(obj: Any, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_table_structure(texts, merged_regions, header_rows, left_header_cols):
    if not isinstance(texts, list) or not texts or not all(isinstance(row, list) for row in texts):
        raise ValueError("texts must contain nonempty table rows.")
    n_cols = max((len(row) for row in texts), default=0)
    if n_cols == 0:
        raise ValueError("The table has no columns.")
    if not 0 <= header_rows <= len(texts):
        raise ValueError("The inferred top-header row count is outside the table.")
    if not 0 <= left_header_cols <= n_cols:
        raise ValueError("The left-header column count is outside the table.")
    if not isinstance(merged_regions, list):
        raise ValueError("merged_regions must be a list.")


def try_load_font(candidates: List[str], size: int):
    for p in candidates:
        try:
            if p and os.path.exists(p):
                return ImageFont.truetype(p, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def load_fonts():
    return {
        "header_bold": try_load_font(FONT_PATH_BOLD_CANDIDATES, HEADER_FONT_SIZE),
        "body_bold": try_load_font(FONT_PATH_BOLD_CANDIDATES, BODY_FONT_SIZE),
    }


def text_bbox(draw: ImageDraw.ImageDraw, text: str, font):
    if "\n" in (text or ""):
        return draw.multiline_textbbox(
            (0, 0), text, font=font, spacing=MULTILINE_SPACING, align="center"
        )
    return draw.textbbox((0, 0), text or "", font=font)


def measure_text(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    bb = text_bbox(draw, text, font)
    return math.ceil(bb[2] - bb[0]), math.ceil(bb[3] - bb[1])


def list_json_files(folder: str) -> List[str]:
    files = []
    for name in os.listdir(folder):
        if name.lower().endswith(".json"):
            files.append(os.path.join(folder, name))
    files.sort()
    return files


def read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def stem_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def draw_centered_text(draw: ImageDraw.ImageDraw, rect, text: str, font, fill):
    x1, y1, x2, y2 = rect
    text = text or ""
    bb = text_bbox(draw, text, font)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    if "\n" in text:
        tx = x1 + max(0, (x2 - x1 - tw) // 2) - bb[0]
        ty = y1 + max(0, (y2 - y1 - th) // 2) - bb[1]
        draw.multiline_text(
            (tx, ty), text, font=font, fill=fill,
            spacing=MULTILINE_SPACING, align="center"
        )
        return
    tx = x1 + max(0, (x2 - x1 - tw) // 2)
    ty = y1 + max(0, (y2 - y1 - th) // 2) - 1
    draw.text((tx, ty), text, font=font, fill=fill)


def draw_rect_outline(draw: ImageDraw.ImageDraw, rect, color, width=1):
    x1, y1, x2, y2 = rect
    for i in range(width):
        draw.rectangle((x1 + i, y1 + i, x2 - i, y2 - i), outline=color)


def is_numeric_like(text: str) -> bool:
    s = re.sub(r"\s+", " ", normalize_space(text)).lower()
    if s == "":
        return False

    patterns = [
        r"^[+-]?\d+$",
        r"^[+-]?\d+\.\d+$",
        r"^[+-]?\d+(,\d{3})+(\.\d+)?$",
        r"^[+-]?\d+(\.\d+)?%$",
        r"^[+-]?\d+\.\d+\s*[a-z]?$",
        r"^\d{4}$",
        r"^n/?a$",
        r"^none$",
    ]
    for p in patterns:
        if re.match(p, s):
            return True

    s2 = s.replace(",", "")
    try:
        float(s2)
        return True
    except Exception:
        return False


def collect_leaf_rows(node: dict) -> List[int]:
    children = node.get("children", [])
    if not children:
        row_idx = node.get("row_index", -1)
        return [row_idx] if row_idx is not None else []
    rows = []
    for ch in children:
        rows.extend(collect_leaf_rows(ch))
    return rows


def infer_true_top_header_rows_num(table_obj: dict) -> int:
    left_root = table_obj.get("left_root", {})
    leaf_rows = [r for r in collect_leaf_rows(left_root) if r is not None and r >= 0]

    if leaf_rows:
        return min(leaf_rows)

    return int(table_obj.get("top_header_rows_num", 0))


def build_merge_maps(
    n_rows: int,
    n_cols: int,
    merged_regions: List[Dict[str, int]]
):
    anchor_to_region = {}
    covered_to_anchor = {}

    for reg in merged_regions:
        fr = reg["first_row"]
        lr = reg["last_row"]
        fc = reg["first_column"]
        lc = reg["last_column"]

        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (fr, lr, fc, lc)):
            raise ValueError("Merged-cell coordinates must be integers.")
        if not (0 <= fr <= lr < n_rows and 0 <= fc <= lc < n_cols):
            raise ValueError(f"Merged region is outside the table: {reg}")
        if any((r, c) in covered_to_anchor for r in range(fr, lr + 1) for c in range(fc, lc + 1)):
            raise ValueError(f"Overlapping merged regions: {reg}")

        anchor = (fr, fc)
        anchor_to_region[anchor] = reg

        for r in range(fr, lr + 1):
            for c in range(fc, lc + 1):
                covered_to_anchor[(r, c)] = anchor

    for r in range(n_rows):
        for c in range(n_cols):
            if (r, c) not in covered_to_anchor:
                covered_to_anchor[(r, c)] = (r, c)
                if (r, c) not in anchor_to_region:
                    anchor_to_region[(r, c)] = {
                        "first_row": r,
                        "last_row": r,
                        "first_column": c,
                        "last_column": c,
                    }

    return anchor_to_region, covered_to_anchor


def is_anchor_cell(r: int, c: int, covered_to_anchor: Dict[Tuple[int, int], Tuple[int, int]]) -> bool:
    return covered_to_anchor[(r, c)] == (r, c)


def detect_numeric_start_col(
    texts: List[List[str]],
    top_header_rows_num: int,
    left_header_columns_num: int
) -> int:
    if not texts:
        return left_header_columns_num

    n_rows = len(texts)
    n_cols = max(len(r) for r in texts)
    start_row = min(top_header_rows_num, n_rows)

    best_col = None
    for c in range(left_header_columns_num, n_cols):
        nonempty = 0
        numeric = 0
        for r in range(start_row, n_rows):
            cell = normalize_space(texts[r][c]) if c < len(texts[r]) else ""
            if cell == "":
                continue
            nonempty += 1
            if is_numeric_like(cell):
                numeric += 1

        if nonempty < MIN_NONEMPTY_CELLS_FOR_NUMERIC_COL:
            continue

        ratio = numeric / max(nonempty, 1)
        if ratio >= NUMERIC_RATIO_THRESHOLD:
            best_col = c
            break

    if best_col is None:
        return left_header_columns_num
    return best_col


def erase_data_cells_hitab(
    texts: List[List[str]],
    top_header_rows_num: int,
    numeric_start_col: int,
    covered_to_anchor: Dict[Tuple[int, int], Tuple[int, int]]
) -> List[Dict[str, Any]]:
    n_rows = len(texts)
    n_cols = max((len(r) for r in texts), default=0)

    candidates = []
    for r in range(top_header_rows_num, n_rows):
        for c in range(numeric_start_col, n_cols):
            if not is_anchor_cell(r, c, covered_to_anchor):
                continue
            txt = normalize_space(texts[r][c])
            if txt == "":
                continue
            candidates.append((r, c))

    if not candidates:
        return []

    k = int(math.floor(len(candidates) * ERASE_RATIO))
    if k <= 0 and MIN_ERASE_ONE_IF_POSSIBLE:
        k = 1
    k = min(k, len(candidates))

    chosen = random.sample(candidates, k)
    erased = []
    for r, c in chosen:
        old_text = texts[r][c]
        texts[r][c] = ""
        erased.append({
            "row": r,
            "col": c,
            "old_text": old_text,
            "new_text": "",
        })

    return erased


def compute_col_widths(
    texts: List[List[str]],
    top_header_rows_num: int,
    draw: ImageDraw.ImageDraw,
    fonts
) -> List[int]:
    n_rows = len(texts)
    n_cols = max((len(r) for r in texts), default=0)
    widths = [MIN_COL_WIDTH] * n_cols
    if n_cols > 0:
        widths[0] = max(widths[0], MIN_FIRST_COL_WIDTH)

    for c in range(n_cols):
        max_w = widths[c]
        for r in range(n_rows):
            txt = normalize_space(texts[r][c]) if c < len(texts[r]) else ""
            font = fonts["header_bold"] if r < top_header_rows_num else fonts["body_bold"]
            w, _ = measure_text(draw, txt, font)
            max_w = max(max_w, w + CELL_PAD_X * 2)
        widths[c] = max_w

    return widths


def compute_row_heights(
    texts: List[List[str]],
    top_header_rows_num: int,
    draw: ImageDraw.ImageDraw,
    fonts
) -> List[int]:
    row_heights = []
    for r, row in enumerate(texts):
        row_h = MIN_HEADER_ROW_HEIGHT if r < top_header_rows_num else MIN_DATA_ROW_HEIGHT
        for cell in row:
            txt = normalize_space(cell)
            font = fonts["header_bold"] if r < top_header_rows_num else fonts["body_bold"]
            _, h = measure_text(draw, txt, font)
            row_h = max(row_h, h + CELL_PAD_Y * 2)
        row_heights.append(row_h)
    return row_heights


def choose_hitab_color_plan(
    texts: List[List[str]],
    top_header_rows_num: int,
    numeric_start_col: int,
    covered_to_anchor: Dict[Tuple[int, int], Tuple[int, int]]
) -> Dict[str, Any]:
    enabled = random.random() < COLOR_FILL_PROB
    if not enabled:
        return {
            "enabled": False,
            "header_fill": None,
            "left_fill": None,
            "body_fill": {},
        }

    header_fill = None
    left_fill = None
    body_fill = {}

    if top_header_rows_num > 0 and random.random() < 0.75:
        header_fill = random.choice(BRIGHT_FILL_PALETTE)

    if numeric_start_col > 0 and random.random() < 0.75:
        left_fill = random.choice(BRIGHT_FILL_PALETTE)

    n_rows = len(texts)
    n_cols = max((len(r) for r in texts), default=0)

    body_candidates = []
    for r in range(top_header_rows_num, n_rows):
        for c in range(numeric_start_col, n_cols):
            if not is_anchor_cell(r, c, covered_to_anchor):
                continue
            body_candidates.append((r, c))

    if body_candidates:
        k = random.randint(
            min(BODY_FILL_CELL_MIN, len(body_candidates)),
            min(BODY_FILL_CELL_MAX, len(body_candidates))
        )
        chosen = random.sample(body_candidates, k)
        for rc in chosen:
            body_fill[rc] = random.choice(BRIGHT_FILL_PALETTE)

    return {
        "enabled": True,
        "header_fill": header_fill,
        "left_fill": left_fill,
        "body_fill": body_fill,
    }


def build_html(
    texts: List[List[str]],
    merged_regions: List[Dict[str, int]],
    top_header_rows_num: int,
    numeric_start_col: int,
) -> str:
    n_rows = len(texts)
    n_cols = max((len(row) for row in texts), default=0)
    texts = [row + [""] * (n_cols - len(row)) for row in texts]
    anchors, covered = build_merge_maps(n_rows, n_cols, merged_regions)
    lines = ["<table>"]
    for r in range(n_rows):
        lines.append("  <tr>")
        for c in range(n_cols):
            if not is_anchor_cell(r, c, covered):
                continue
            region = anchors[(r, c)]
            rowspan = region["last_row"] - r + 1
            colspan = region["last_column"] - c + 1
            tag = "th" if r < top_header_rows_num or c < numeric_start_col else "td"
            attrs = []
            if rowspan > 1:
                attrs.append(f'rowspan="{rowspan}"')
            if colspan > 1:
                attrs.append(f'colspan="{colspan}"')
            attr_text = " " + " ".join(attrs) if attrs else ""
            content = html.escape(normalize_space(texts[r][c])).replace("\n", "<br>")
            lines.append(f"    <{tag}{attr_text}>{content}</{tag}>")
        lines.append("  </tr>")
    lines.append("</table>")
    return "\n".join(lines)


def build_modified_json(
    table_obj: dict,
    texts: List[List[str]],
    erased_cells: List[Dict[str, Any]],
    numeric_start_col: int,
    color_plan: Dict[str, Any],
    raw_top_header_rows_num: int,
    top_header_rows_num: int
) -> Dict[str, Any]:
    return {
        "title": table_obj.get("title"),
        "top_root": table_obj.get("top_root"),
        "left_root": table_obj.get("left_root"),
        "texts": texts,
        "merged_regions": table_obj.get("merged_regions", []),
        "top_header_rows_num_raw": raw_top_header_rows_num,
        "top_header_rows_num_used": top_header_rows_num,
        "left_header_columns_num": table_obj.get("left_header_columns_num", 0),
        "numeric_start_col": numeric_start_col,
        "erased_cells": erased_cells,
        "style": {
            "line_policy": "fixed_hitab_headers",
            "random_line_selection": False,
            "title_rendered": False,
            "background": "white_with_optional_colored_regions",
            "outer_border": True,
            "header_lines": True,
            "left_header_lines": numeric_start_col > 1,
            "single_left_header_no_lines": numeric_start_col == 1,
            "body_grid": False,
            "data_region_grid": False,
            "header_bold": True,
            "body_bold": True,
            "alignment": "center",
            "color_fill_enabled": color_plan.get("enabled", False),
        }
    }


def build_edit_meta(
    json_filename: str,
    table_obj: dict,
    texts: List[List[str]],
    erased_cells: List[Dict[str, Any]],
    numeric_start_col: int,
    color_plan: Dict[str, Any],
    raw_top_header_rows_num: int,
    top_header_rows_num: int,
    render_info: Dict[str, Any] = None,
) -> Dict[str, Any]:
    return {
        "batch_random_seed": RANDOM_SEED,
        "erase_ratio": ERASE_RATIO,
        "review_status": "pending_manual_review",
        "render_info": render_info or {},
        "json_filename": json_filename,
        "output_image_name": f"HiTab_{stem_name(json_filename)}{OUTPUT_IMAGE_EXT}",
        "title": table_obj.get("title"),
        "title_rendered": False,
        "table_shape": {
            "rows": len(texts),
            "cols": max((len(r) for r in texts), default=0),
        },
        "top_header_rows_num_raw": raw_top_header_rows_num,
        "top_header_rows_num_used": top_header_rows_num,
        "left_header_columns_num": table_obj.get("left_header_columns_num", 0),
        "numeric_start_col": numeric_start_col,
        "left_header_line_enabled": numeric_start_col > 1,
        "merged_regions_count": len(table_obj.get("merged_regions", [])),
        "erased_cells": erased_cells,
        "style": {
            "line_policy": "fixed_hitab_headers",
            "random_line_selection": False,
            "outer_border": True,
            "header_lines": True,
            "left_header_lines": numeric_start_col > 1,
            "single_left_header_no_lines": numeric_start_col == 1,
            "body_grid": False,
            "data_region_grid": False,
            "body_bold": True,
            "alignment": "center",
            "color_fill_enabled": color_plan.get("enabled", False),
        },
        "color_plan": {
            "enabled": color_plan.get("enabled", False),
            "header_fill": color_plan.get("header_fill"),
            "left_fill": color_plan.get("left_fill"),
            "body_fill_cells": sorted([list(k) for k in color_plan.get("body_fill", {}).keys()]),
            "body_fill_details": [
                {"row": r, "col": c, "color": color}
                for (r, c), color in sorted(color_plan.get("body_fill", {}).items())
            ],
        }
    }


def save_artifacts(
    json_filename: str,
    table_obj: dict,
    texts: List[List[str]],
    erased_cells: List[Dict[str, Any]],
    numeric_start_col: int,
    color_plan: Dict[str, Any],
    raw_top_header_rows_num: int,
    top_header_rows_num: int,
    render_info: Dict[str, Any] = None,
):
    ensure_dir(MODIFIED_JSON_DIR)
    ensure_dir(MODIFIED_HTML_DIR)
    ensure_dir(EDIT_META_DIR)

    base_name = f"HiTab_{stem_name(json_filename)}"

    modified_json_path = os.path.join(MODIFIED_JSON_DIR, base_name + ".json")
    modified_html_path = os.path.join(MODIFIED_HTML_DIR, base_name + ".html")
    edit_meta_path = os.path.join(EDIT_META_DIR, base_name + ".json")

    save_json(
        build_modified_json(
            table_obj, texts, erased_cells, numeric_start_col, color_plan,
            raw_top_header_rows_num, top_header_rows_num
        ),
        modified_json_path
    )

    html_text = build_html(
        texts, table_obj.get("merged_regions", []), top_header_rows_num, numeric_start_col
    )
    with open(modified_html_path, "w", encoding="utf-8") as f:
        f.write(html_text)

    save_json(
        build_edit_meta(
            json_filename, table_obj, texts, erased_cells, numeric_start_col, color_plan,
            raw_top_header_rows_num, top_header_rows_num, render_info
        ),
        edit_meta_path
    )


def render_table_image(
    texts: List[List[str]],
    merged_regions: List[Dict[str, int]],
    top_header_rows_num: int,
    left_header_columns_num: int,
    numeric_start_col: int,
    color_plan: Dict[str, Any],
    save_path: str
):
    ensure_dir(os.path.dirname(save_path))
    fonts = load_fonts()

    n_rows = len(texts)
    n_cols = max((len(r) for r in texts), default=0)
    texts = [row + [""] * (n_cols - len(row)) for row in texts]

    anchor_to_region, covered_to_anchor = build_merge_maps(n_rows, n_cols, merged_regions)
    has_multi_left_header = numeric_start_col > 1

    tmp = Image.new("RGB", (400, 400), PAGE_BG_COLOR)
    tmp_draw = ImageDraw.Draw(tmp)

    col_widths = compute_col_widths(texts, top_header_rows_num, tmp_draw, fonts)
    row_heights = compute_row_heights(texts, top_header_rows_num, tmp_draw, fonts)

    table_w = sum(col_widths)
    table_h = sum(row_heights)

    img_w = table_w + PAGE_MARGIN_X * 2
    img_h = table_h + PAGE_MARGIN_Y * 2

    img = Image.new("RGB", (img_w, img_h), PAGE_BG_COLOR)
    draw = ImageDraw.Draw(img)

    table_left = PAGE_MARGIN_X
    table_top = PAGE_MARGIN_Y
    table_right = table_left + table_w
    table_bottom = table_top + table_h

    x_positions = [table_left]
    for w in col_widths:
        x_positions.append(x_positions[-1] + w)

    y_positions = [table_top]
    for h in row_heights:
        y_positions.append(y_positions[-1] + h)

    if color_plan.get("enabled", False):
        header_fill = color_plan.get("header_fill")
        left_fill = color_plan.get("left_fill")
        body_fill = color_plan.get("body_fill", {})

        if header_fill is not None and top_header_rows_num > 0:
            x1 = x_positions[0]
            y1 = y_positions[0]
            x2 = x_positions[n_cols]
            y2 = y_positions[top_header_rows_num]
            draw.rectangle((x1, y1, x2, y2), fill=header_fill)

        if left_fill is not None and numeric_start_col > 0:
            x1 = x_positions[0]
            y1 = y_positions[top_header_rows_num] if top_header_rows_num < len(y_positions) else y_positions[0]
            x2 = x_positions[numeric_start_col]
            y2 = y_positions[n_rows]
            draw.rectangle((x1, y1, x2, y2), fill=left_fill)

        for (r, c), fill_color in body_fill.items():
            if not is_anchor_cell(r, c, covered_to_anchor):
                continue
            reg = anchor_to_region[(r, c)]
            fr, lr = reg["first_row"], reg["last_row"]
            fc, lc = reg["first_column"], reg["last_column"]

            x1 = x_positions[fc]
            y1 = y_positions[fr]
            x2 = x_positions[lc + 1]
            y2 = y_positions[lr + 1]
            draw.rectangle((x1, y1, x2, y2), fill=fill_color)

    draw_rect_outline(
        draw,
        (table_left, table_top, table_right, table_bottom),
        BORDER_COLOR,
        BORDER_WIDTH
    )

    for r in range(n_rows):
        for c in range(n_cols):
            if not is_anchor_cell(r, c, covered_to_anchor):
                continue

            reg = anchor_to_region[(r, c)]
            fr, lr = reg["first_row"], reg["last_row"]
            fc, lc = reg["first_column"], reg["last_column"]

            x1 = x_positions[fc]
            y1 = y_positions[fr]
            x2 = x_positions[lc + 1]
            y2 = y_positions[lr + 1]

            in_top_header = fr < top_header_rows_num
            in_left_header = fc < numeric_start_col

            if in_top_header:
                draw_rect_outline(draw, (x1, y1, x2, y2), HEADER_LINE_COLOR, HEADER_LINE_WIDTH)
            else:
                if in_left_header and has_multi_left_header:
                    draw_rect_outline(draw, (x1, y1, x2, y2), LEFT_HEADER_LINE_COLOR, LEFT_HEADER_LINE_WIDTH)
                else:
                    pass

    for r in range(n_rows):
        for c in range(n_cols):
            if not is_anchor_cell(r, c, covered_to_anchor):
                continue

            reg = anchor_to_region[(r, c)]
            fr, lr = reg["first_row"], reg["last_row"]
            fc, lc = reg["first_column"], reg["last_column"]

            txt = normalize_space(texts[fr][fc])
            x1 = x_positions[fc]
            y1 = y_positions[fr]
            x2 = x_positions[lc + 1]
            y2 = y_positions[lr + 1]

            font = fonts["header_bold"] if fr < top_header_rows_num else fonts["body_bold"]
            draw_centered_text(draw, (x1, y1, x2, y2), txt, font, TEXT_COLOR)

    img.save(save_path, quality=95)

    return {
        "image_width": img_w,
        "image_height": img_h,
        "column_widths": col_widths,
        "row_heights": row_heights,
        "pillow_version": PIL.__version__,
        "text_line_policy": "preserve_source_newlines",
        "multiline_spacing": MULTILINE_SPACING,
        "fonts": {
            role: {
                "path": os.fsdecode(font.path) if getattr(font, "path", None) else "Pillow default",
                "size": getattr(font, "size", None),
            }
            for role, font in fonts.items()
        },
    }


def process_one_table(json_path: str):
    table_obj = read_json(json_path)

    texts = table_obj.get("texts", [])
    merged_regions = table_obj.get("merged_regions", [])

    raw_top_header_rows_num = int(table_obj.get("top_header_rows_num", 0))
    top_header_rows_num = infer_true_top_header_rows_num(table_obj)
    left_header_columns_num = int(table_obj.get("left_header_columns_num", 0))

    validate_table_structure(texts, merged_regions, top_header_rows_num, left_header_columns_num)

    n_cols = max((len(r) for r in texts), default=0)
    texts = [row + [""] * (n_cols - len(row)) for row in texts]

    numeric_start_col = detect_numeric_start_col(
        texts, top_header_rows_num, left_header_columns_num
    )

    anchor_to_region, covered_to_anchor = build_merge_maps(
        len(texts), n_cols, merged_regions
    )

    erased_cells = erase_data_cells_hitab(
        texts=texts,
        top_header_rows_num=top_header_rows_num,
        numeric_start_col=numeric_start_col,
        covered_to_anchor=covered_to_anchor
    )

    color_plan = choose_hitab_color_plan(
        texts=texts,
        top_header_rows_num=top_header_rows_num,
        numeric_start_col=numeric_start_col,
        covered_to_anchor=covered_to_anchor
    )

    out_name = f"HiTab_{stem_name(json_path)}{OUTPUT_IMAGE_EXT}"
    save_path = os.path.join(OUTPUT_DIR, out_name)

    render_info = render_table_image(
        texts=texts,
        merged_regions=merged_regions,
        top_header_rows_num=top_header_rows_num,
        left_header_columns_num=left_header_columns_num,
        numeric_start_col=numeric_start_col,
        color_plan=color_plan,
        save_path=save_path
    )

    save_artifacts(
        json_filename=os.path.basename(json_path),
        table_obj=table_obj,
        texts=texts,
        erased_cells=erased_cells,
        numeric_start_col=numeric_start_col,
        color_plan=color_plan,
        raw_top_header_rows_num=raw_top_header_rows_num,
        top_header_rows_num=top_header_rows_num,
        render_info=render_info,
    )

    return {
        "json_file": os.path.basename(json_path),
        "title": normalize_space(table_obj.get("title", "")),
        "rows": len(texts),
        "cols": n_cols,
        "top_header_rows_raw": raw_top_header_rows_num,
        "top_header_rows_used": top_header_rows_num,
        "left_header_columns_num": left_header_columns_num,
        "numeric_start_col": numeric_start_col,
        "left_header_line_enabled": numeric_start_col > 1,
        "erased_count": len(erased_cells),
        "color_fill_enabled": color_plan.get("enabled", False),
        "image_name": out_name,
    }


def main(argv=None):
    global TABLES_DIR, RANDOM_SEED, FONT_PATH_BOLD_CANDIDATES
    parser = argparse.ArgumentParser(description="Render HiTab with the fixed experimental line policy.")
    parser.add_argument("--input-dir", required=True, help="Directory containing the original HiTab table JSON files.")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Output directory; use a separate directory for this release.")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED, help="Batch seed for erasure and color selection (default: 42).")
    parser.add_argument("--font-bold", help="Optional exact bold font file. Otherwise use the original fallback list.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing outputs in an existing output directory.")
    args = parser.parse_args(argv)

    TABLES_DIR = os.path.abspath(args.input_dir)
    configure_output(args.output_dir)
    RANDOM_SEED = args.seed
    if not os.path.isdir(TABLES_DIR):
        parser.error(f"Input directory does not exist: {TABLES_DIR}")
    if args.font_bold:
        font_path = os.path.abspath(args.font_bold)
        if not os.path.isfile(font_path):
            parser.error(f"Font file does not exist: {font_path}")
        try:
            ImageFont.truetype(font_path, size=BODY_FONT_SIZE)
        except OSError as exc:
            parser.error(f"Cannot load the selected font: {exc}")
        FONT_PATH_BOLD_CANDIDATES = [font_path]

    json_files = list_json_files(TABLES_DIR)
    if not json_files:
        parser.error("No JSON table files were found in the input directory.")
    if not args.overwrite and os.path.isdir(OUTPUT_DIR) and os.listdir(OUTPUT_DIR):
        parser.error("Output directory is not empty. Use a fresh directory or explicitly pass --overwrite.")
    for folder in (OUTPUT_DIR, ARTIFACT_DIR, MODIFIED_JSON_DIR, MODIFIED_HTML_DIR, EDIT_META_DIR):
        ensure_dir(folder)

    random.seed(RANDOM_SEED)
    manifest = {
        "input_dir": TABLES_DIR,
        "random_seed": RANDOM_SEED,
        "pillow_version": PIL.__version__,
        "line_policy": "fixed_hitab_headers",
        "random_line_selection": False,
        "text_line_policy": "preserve_source_newlines",
        "automatic_text_wrapping": False,
        "multiline_spacing": MULTILINE_SPACING,
        "erase_ratio": ERASE_RATIO,
        "color_fill_probability": COLOR_FILL_PROB,
        "input_order": [os.path.basename(p) for p in json_files],
        "review_status": "pending_manual_review",
        "samples": [],
    }
    ok = 0
    fail = 0
    print(f"[INFO] Found {len(json_files)} tables; line policy: fixed_hitab_headers")
    for i, json_path in enumerate(json_files, start=1):
        record = {"json_file": os.path.basename(json_path)}
        try:
            record["source_sha256"] = file_sha256(json_path)
            info = process_one_table(json_path)
            record.update(info)
            record["status"] = "rendered_pending_review"
            print(f"[{i}/{len(json_files)}] [RENDERED] {info['image_name']} | erased={info['erased_count']}")
            ok += 1
        except Exception as exc:
            record.update(status="render_failed", error_type=type(exc).__name__, error=str(exc))
            print(f"[{i}/{len(json_files)}] [FAIL] {record['json_file']} | {exc}")
            fail += 1
        manifest["samples"].append(record)
    manifest["counts"] = {"submitted": len(json_files), "rendered": ok, "failed": fail}
    save_json(manifest, os.path.join(OUTPUT_DIR, "render_manifest.json"))
    print(f"Rendered: {ok}; failed: {fail}. Manual quality review is still required.")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())