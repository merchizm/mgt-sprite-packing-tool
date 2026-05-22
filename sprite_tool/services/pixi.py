from typing import Sequence

from sprite_tool.constants import APP_NAME
from sprite_tool.models import FrameInfo


def _frame_data(frame: FrameInfo) -> dict:
    data = {
        "frame": {"x": frame.x, "y": frame.y, "w": frame.w, "h": frame.h},
        "rotated": False,
        "trimmed": frame.trimmed,
        "spriteSourceSize": {
            "x": frame.source_x,
            "y": frame.source_y,
            "w": frame.w,
            "h": frame.h,
        },
        "sourceSize": {"w": frame.source_w, "h": frame.source_h},
    }
    if frame.vertices:
        data["vertices"] = [{"x": x, "y": y} for x, y in frame.vertices]
    return data


def build_pixi_data(
    image_name: str,
    sheet_w: int,
    sheet_h: int,
    frames: Sequence[FrameInfo],
    data_format: str,
    pixel_format: str = "RGBA8888",
) -> dict:
    meta = {
        "app": APP_NAME,
        "version": "1.2",
        "image": image_name,
        "format": pixel_format,
        "size": {"w": sheet_w, "h": sheet_h},
        "scale": "1",
    }

    if data_format == "array":
        return {
            "frames": [
                {"filename": frame.name, **_frame_data(frame)}
                for frame in frames
            ],
            "meta": meta,
        }

    return {
        "frames": {
            frame.name: _frame_data(frame)
            for frame in frames
        },
        "meta": meta,
    }
