import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PIL import Image


@dataclass
class PackedCell:
    source_index: int
    x: int
    y: int
    w: int
    h: int


def next_power_of_two(value: int) -> int:
    if value <= 0:
        return 1
    return 1 << (value - 1).bit_length()


def compute_sheet_size(
    columns: int,
    rows: int,
    cell_w: int,
    cell_h: int,
    shape_padding: int,
    border_padding: int,
) -> Tuple[int, int]:
    width = border_padding * 2 + columns * cell_w + max(0, columns - 1) * shape_padding
    height = border_padding * 2 + rows * cell_h + max(0, rows - 1) * shape_padding
    return width, height


def pack_cells(
    sizes: Sequence[Tuple[int, int]],
    algorithm: str,
    columns_requested: int,
    shape_padding: int,
    border_padding: int,
    pot_enabled: bool,
) -> Tuple[List[PackedCell], int, int, int, int, int, int]:
    # The UI exposes Polygon as a packing mode, but v1 still places polygon sprites
    # by their rectangle bounds and writes the alpha outline as metadata later.
    normalized = algorithm.lower()
    if normalized == "grid":
        return pack_grid(sizes, columns_requested, shape_padding, border_padding, pot_enabled)
    if normalized == "basic":
        return pack_basic(sizes, columns_requested, shape_padding, border_padding, pot_enabled)
    if normalized in {"maxrects", "polygon"}:
        return pack_maxrects(sizes, columns_requested, shape_padding, border_padding, pot_enabled)
    raise ValueError(f"Unsupported packing algorithm: {algorithm}")


def pack_grid(
    sizes: Sequence[Tuple[int, int]],
    columns_requested: int,
    shape_padding: int,
    border_padding: int,
    pot_enabled: bool,
) -> Tuple[List[PackedCell], int, int, int, int, int, int]:
    total = len(sizes)
    max_w = max(width for width, _height in sizes)
    max_h = max(height for _width, height in sizes)
    columns = columns_requested or math.ceil(math.sqrt(total))
    rows = math.ceil(total / columns)
    sheet_w, sheet_h = compute_sheet_size(columns, rows, max_w, max_h, shape_padding, border_padding)
    if pot_enabled:
        sheet_w = next_power_of_two(sheet_w)
        sheet_h = next_power_of_two(sheet_h)
    cells = []
    for index, (_width, _height) in enumerate(sizes):
        col = index % columns
        row = index // columns
        cells.append(
            PackedCell(
                source_index=index,
                x=border_padding + col * (max_w + shape_padding),
                y=border_padding + row * (max_h + shape_padding),
                w=max_w,
                h=max_h,
            )
        )
    return cells, sheet_w, sheet_h, columns, rows, max_w, max_h


def pack_basic(
    sizes: Sequence[Tuple[int, int]],
    columns_requested: int,
    shape_padding: int,
    border_padding: int,
    pot_enabled: bool,
) -> Tuple[List[PackedCell], int, int, int, int, int, int]:
    # Basic packing is a shelf layout: fill the current row until the target width
    # is exceeded, then move to the next row.
    max_w = max(width for width, _height in sizes)
    max_h = max(height for _width, height in sizes)
    target_columns = columns_requested or max(1, math.ceil(math.sqrt(len(sizes))))
    target_width = border_padding * 2 + target_columns * max_w + max(0, target_columns - 1) * shape_padding
    cells: List[PackedCell] = []
    x = border_padding
    y = border_padding
    row_h = 0
    rows = 1
    max_right = border_padding

    for index, (width, height) in enumerate(sizes):
        if x > border_padding and x + width + border_padding > target_width:
            x = border_padding
            y += row_h + shape_padding
            row_h = 0
            rows += 1
        cells.append(PackedCell(index, x, y, width, height))
        max_right = max(max_right, x + width)
        x += width + shape_padding
        row_h = max(row_h, height)

    sheet_w = max_right + border_padding
    sheet_h = y + row_h + border_padding
    if pot_enabled:
        sheet_w = next_power_of_two(sheet_w)
        sheet_h = next_power_of_two(sheet_h)
    columns = min(target_columns, len(sizes))
    return cells, sheet_w, sheet_h, columns, rows, max_w, max_h


def pack_maxrects(
    sizes: Sequence[Tuple[int, int]],
    columns_requested: int,
    shape_padding: int,
    border_padding: int,
    pot_enabled: bool,
) -> Tuple[List[PackedCell], int, int, int, int, int, int]:
    # Start near a square area estimate and grow until every sprite can be placed.
    # This keeps MaxRects deterministic without requiring a user-specified atlas size.
    max_w = max(width for width, _height in sizes)
    max_h = max(height for _width, height in sizes)
    area = sum((width + shape_padding) * (height + shape_padding) for width, height in sizes)
    side = max(max_w, max_h, math.ceil(math.sqrt(area)))
    if columns_requested:
        side = max(side, columns_requested * max_w + max(0, columns_requested - 1) * shape_padding)
    ordered = sorted(enumerate(sizes), key=lambda item: (max(item[1]), item[1][0] * item[1][1]), reverse=True)

    while True:
        placements = _try_maxrects(ordered, side, shape_padding, border_padding)
        if placements is not None:
            break
        side = max(side + max(16, max_w // 2), int(side * 1.25))

    cells = [PackedCell(index, 0, 0, 0, 0) for index in range(len(sizes))]
    for cell in placements:
        cells[cell.source_index] = cell
    sheet_w = max(cell.x + cell.w for cell in cells) + border_padding
    sheet_h = max(cell.y + cell.h for cell in cells) + border_padding
    if pot_enabled:
        sheet_w = next_power_of_two(sheet_w)
        sheet_h = next_power_of_two(sheet_h)
    columns = max(1, round(math.sqrt(len(sizes))))
    rows = math.ceil(len(sizes) / columns)
    return cells, sheet_w, sheet_h, columns, rows, max_w, max_h


def _try_maxrects(
    ordered: Sequence[Tuple[int, Tuple[int, int]]],
    side: int,
    shape_padding: int,
    border_padding: int,
) -> Optional[List[PackedCell]]:
    # Keep a list of available rectangles. Each placement consumes one free rect
    # and splits the remaining space into right/bottom candidates.
    free_rects = [(border_padding, border_padding, side, side)]
    placements: List[PackedCell] = []
    for source_index, (width, height) in ordered:
        padded_w = width + shape_padding
        padded_h = height + shape_padding
        best = None
        for free_index, (x, y, free_w, free_h) in enumerate(free_rects):
            if padded_w <= free_w and padded_h <= free_h:
                score = (min(free_w - padded_w, free_h - padded_h), free_w * free_h)
                if best is None or score < best[0]:
                    best = (score, free_index, x, y, free_w, free_h)
        if best is None:
            return None
        _score, free_index, x, y, free_w, free_h = best
        placements.append(PackedCell(source_index, x, y, width, height))
        del free_rects[free_index]
        right_w = free_w - padded_w
        bottom_h = free_h - padded_h
        if right_w > 0:
            free_rects.append((x + padded_w, y, right_w, padded_h))
        if bottom_h > 0:
            free_rects.append((x, y + padded_h, free_w, bottom_h))
        free_rects.sort(key=lambda rect: (rect[1], rect[0], rect[2] * rect[3]))
    return placements


def build_alpha_outline(image: Image.Image, max_points: int = 24) -> List[Tuple[int, int]]:
    # Polygon mode stores a compact alpha outline for metadata. It is intentionally
    # simplified to a bounded number of points so JSON stays small and predictable.
    alpha = image.convert("RGBA").getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return []
    left, top, right, bottom = bbox
    points: List[Tuple[int, int]] = []
    for x in range(left, right):
        for y in range(top, bottom):
            if alpha.getpixel((x, y)):
                points.append((x, y))
                break
    for y in range(top, bottom):
        for x in range(right - 1, left - 1, -1):
            if alpha.getpixel((x, y)):
                points.append((x, y))
                break
    for x in range(right - 1, left - 1, -1):
        for y in range(bottom - 1, top - 1, -1):
            if alpha.getpixel((x, y)):
                points.append((x, y))
                break
    for y in range(bottom - 1, top - 1, -1):
        for x in range(left, right):
            if alpha.getpixel((x, y)):
                points.append((x, y))
                break
    deduped = []
    seen = set()
    for point in points:
        if point not in seen:
            deduped.append(point)
            seen.add(point)
    if len(deduped) <= max_points:
        return deduped
    step = len(deduped) / max_points
    return [deduped[min(len(deduped) - 1, round(i * step))] for i in range(max_points)]
