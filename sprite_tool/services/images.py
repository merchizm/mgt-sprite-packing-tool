import math
import os
from collections import deque
from typing import List, Optional, Sequence, Tuple

from PIL import Image

from sprite_tool.models import FrameInfo
from sprite_tool.services.packing import build_alpha_outline, pack_cells


def ensure_positive(value: int, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} 0'dan buyuk olmali.")


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


def trim_image(image: Image.Image) -> Tuple[Image.Image, int, int, int, int, bool]:
    bbox = image.getbbox()
    if bbox is None:
        return image, 0, 0, image.width, image.height, False
    left, top, right, bottom = bbox
    if left == 0 and top == 0 and right == image.width and bottom == image.height:
        return image, 0, 0, image.width, image.height, False
    return image.crop(bbox), left, top, image.width, image.height, True


def is_image_file(path: str) -> bool:
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
    }


def build_output_name(pattern: str, path: str, index: int, prefix: str = "") -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower() or ".png"
    raw = pattern.format(index=index, stem=stem, prefix=prefix, ext=ext, name=os.path.basename(path))
    if not os.path.splitext(raw)[1]:
        raw += ext or ".png"
    return raw


def scale_image(image: Image.Image, scale_percent: int) -> Image.Image:
    if scale_percent == 100:
        return image
    width = max(1, round(image.width * scale_percent / 100))
    height = max(1, round(image.height * scale_percent / 100))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def collect_source_images(
    file_paths: Sequence[str],
    trim_enabled: bool,
    scale_percent: int,
    transparent_rgb: Optional[Tuple[int, int, int]] = None,
    color_tolerance: int = 0,
    replacement_rgba: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[List[Tuple[str, Image.Image]], int, int]:
    images: List[Tuple[str, Image.Image]] = []
    max_w = 0
    max_h = 0
    for path in file_paths:
        with Image.open(path) as opened:
            image = opened.convert("RGBA")
        if transparent_rgb is not None:
            image = apply_color_key(image, transparent_rgb, color_tolerance, replacement_rgba)
        image = scale_image(image, scale_percent)
        if trim_enabled:
            image, _, _, _, _, _ = trim_image(image)
        images.append((path, image))
        max_w = max(max_w, image.width)
        max_h = max(max_h, image.height)
    if not images:
        return images, max_w, max_h
    ensure_positive(max_w, "Sprite width")
    ensure_positive(max_h, "Sprite height")
    return images, max_w, max_h


def compose_sheet(
    file_paths: Sequence[Optional[str]],
    columns_requested: int,
    shape_padding: int,
    border_padding: int,
    trim_enabled: bool,
    pot_enabled: bool,
    name_pattern: str,
    scale_percent: int = 100,
    cell_backgrounds: Optional[Sequence[Optional[Tuple[int, int, int, int]]]] = None,
    transparent_rgb: Optional[Tuple[int, int, int]] = None,
    color_tolerance: int = 0,
    replacement_rgba: Optional[Tuple[int, int, int, int]] = None,
    packing_algorithm: str = "Grid",
    return_cells: bool = False,
) -> Tuple[Image.Image, List[FrameInfo], int, int, int, int]:
    real_file_paths = [path for path in file_paths if path]
    images, max_w, max_h = collect_source_images(
        real_file_paths,
        trim_enabled,
        scale_percent,
        transparent_rgb,
        color_tolerance,
        replacement_rgba,
    )
    total_cells = len(file_paths)
    if not images:
        raise ValueError("En az bir kaynak sprite secmelisiniz.")

    # Build a cell list that includes both real sprites and spacers. The preview
    # uses these cells so spacers can be selected, colored, and reordered.
    sizes = [(max_w, max_h) if path is None else (0, 0) for path in file_paths]
    images_by_path = iter(images)
    prepared_by_cell: List[Optional[Tuple[str, Image.Image, Image.Image, int, int, int, int, bool]]] = []
    for file_path in file_paths:
        if not file_path:
            prepared_by_cell.append(None)
            continue
        path, preview_image = next(images_by_path)
        with Image.open(path) as opened:
            original = opened.convert("RGBA")
        if transparent_rgb is not None:
            original = apply_color_key(original, transparent_rgb, color_tolerance, replacement_rgba)
        original = scale_image(original, scale_percent)
        prepared = preview_image
        source_x = 0
        source_y = 0
        source_w = original.width
        source_h = original.height
        trimmed = False
        if trim_enabled:
            prepared, source_x, source_y, source_w, source_h, trimmed = trim_image(original)
        prepared_by_cell.append((path, prepared, original, source_x, source_y, source_w, source_h, trimmed))
    for index, item in enumerate(prepared_by_cell):
        if item is not None:
            _path, prepared, _original, _source_x, _source_y, _source_w, _source_h, _trimmed = item
            sizes[index] = (prepared.width, prepared.height)

    cells, sheet_w, sheet_h, columns, rows, max_w, max_h = pack_cells(
        sizes,
        packing_algorithm,
        columns_requested,
        shape_padding,
        border_padding,
        pot_enabled,
    )

    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    frames: List[FrameInfo] = []
    layout_rects = [(cell.x, cell.y, cell.w, cell.h) for cell in cells]
    frame_index = 0
    for cell in cells:
        cell_index = cell.source_index
        file_path = file_paths[cell_index]
        x = cell.x
        y = cell.y
        background = cell_backgrounds[cell_index] if cell_backgrounds and cell_index < len(cell_backgrounds) else None
        if background is not None:
            cell_bg = Image.new("RGBA", (cell.w, cell.h), background)
            sheet.paste(cell_bg, (x, y))
        if not file_path:
            continue

        # Only real sprite cells become Pixi frames; spacer cells reserve colored
        # space in the atlas without creating JSON entries.
        prepared_item = prepared_by_cell[cell_index]
        if prepared_item is None:
            continue
        path, prepared, _original, source_x, source_y, source_w, source_h, trimmed = prepared_item
        sheet.paste(prepared, (x, y), prepared)
        vertices = build_alpha_outline(prepared) if packing_algorithm.lower() == "polygon" else []
        frames.append(
            FrameInfo(
                name=build_output_name(name_pattern, path, frame_index),
                x=x,
                y=y,
                w=prepared.width,
                h=prepared.height,
                source_w=source_w,
                source_h=source_h,
                source_x=source_x,
                source_y=source_y,
                trimmed=trimmed,
                vertices=vertices,
            )
        )
        frame_index += 1

    if return_cells:
        return sheet, frames, columns, rows, max_w, max_h, layout_rects
    return sheet, frames, columns, rows, max_w, max_h


def build_grid_rects(
    sheet: Image.Image,
    columns: int,
    rows: int,
    sprite_w: int,
    sprite_h: int,
    shape_padding: int,
    border_padding: int,
) -> List[Tuple[int, int, int, int, int]]:
    required_w, required_h = compute_sheet_size(
        columns, rows, sprite_w, sprite_h, shape_padding, border_padding
    )
    if sheet.width < required_w or sheet.height < required_h:
        raise ValueError(
            "Grid ayarlari sheet boyutuna sigmiyor. "
            f"Beklenen minimum boyut: {required_w}x{required_h}, mevcut: {sheet.width}x{sheet.height}."
        )

    rects: List[Tuple[int, int, int, int, int]] = []
    for row in range(rows):
        for col in range(columns):
            index = row * columns + col
            x = border_padding + col * (sprite_w + shape_padding)
            y = border_padding + row * (sprite_h + shape_padding)
            rects.append((index, x, y, sprite_w, sprite_h))
    return rects


def fit_columns_rows(
    sheet_w: int,
    sheet_h: int,
    sprite_w: int,
    sprite_h: int,
    shape_padding: int,
    border_padding: int,
) -> Tuple[int, int]:
    usable_w = max(1, sheet_w - border_padding * 2)
    usable_h = max(1, sheet_h - border_padding * 2)
    step_w = max(1, sprite_w + shape_padding)
    step_h = max(1, sprite_h + shape_padding)
    columns = max(1, (usable_w + shape_padding) // step_w)
    rows = max(1, (usable_h + shape_padding) // step_h)
    return columns, rows


def fit_sprite_size(
    sheet_w: int,
    sheet_h: int,
    columns: int,
    rows: int,
    shape_padding: int,
    border_padding: int,
) -> Tuple[int, int]:
    usable_w = max(1, sheet_w - border_padding * 2 - max(0, columns - 1) * shape_padding)
    usable_h = max(1, sheet_h - border_padding * 2 - max(0, rows - 1) * shape_padding)
    sprite_w = max(1, usable_w // max(1, columns))
    sprite_h = max(1, usable_h // max(1, rows))
    return sprite_w, sprite_h


def detect_sprite_regions(
    sheet: Image.Image,
    alpha_threshold: int,
    min_area: int,
    padding: int,
) -> List[Tuple[int, int, int, int, int]]:
    rgba = sheet.convert("RGBA")
    width, height = rgba.size
    alpha = rgba.getchannel("A")
    visited = bytearray(width * height)
    rects: List[Tuple[int, int, int, int, int]] = []

    def flat_index(x: int, y: int) -> int:
        return y * width + x

    for y in range(height):
        for x in range(width):
            start = flat_index(x, y)
            if visited[start]:
                continue
            visited[start] = 1
            if alpha.getpixel((x, y)) <= alpha_threshold:
                continue

            queue = deque([(x, y)])
            min_x = max_x = x
            min_y = max_y = y
            area = 0

            while queue:
                cx, cy = queue.popleft()
                area += 1
                min_x = min(min_x, cx)
                min_y = min(min_y, cy)
                max_x = max(max_x, cx)
                max_y = max(max_y, cy)

                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    neighbor = flat_index(nx, ny)
                    if visited[neighbor]:
                        continue
                    visited[neighbor] = 1
                    if alpha.getpixel((nx, ny)) > alpha_threshold:
                        queue.append((nx, ny))

            if area < min_area:
                continue

            x0 = max(0, min_x - padding)
            y0 = max(0, min_y - padding)
            x1 = min(width, max_x + 1 + padding)
            y1 = min(height, max_y + 1 + padding)
            rects.append((0, x0, y0, x1 - x0, y1 - y0))

    rects.sort(key=lambda item: (item[2], item[1]))
    return [(index, x, y, w, h) for index, (_old, x, y, w, h) in enumerate(rects)]


def apply_transparent_color(image: Image.Image, rgb: Tuple[int, int, int], tolerance: int) -> Image.Image:
    return apply_color_key(image, rgb, tolerance, None)


def apply_color_key(
    image: Image.Image,
    rgb: Tuple[int, int, int],
    tolerance: int,
    replacement_rgba: Optional[Tuple[int, int, int, int]],
) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if (
                abs(r - rgb[0]) <= tolerance
                and abs(g - rgb[1]) <= tolerance
                and abs(b - rgb[2]) <= tolerance
            ):
                pixels[x, y] = replacement_rgba if replacement_rgba is not None else (r, g, b, 0)
            else:
                pixels[x, y] = (r, g, b, a)
    return rgba
