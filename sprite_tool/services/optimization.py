import os
import shutil
import subprocess
from typing import Sequence, Tuple

from PIL import Image

from sprite_tool.models import FrameInfo, OptimizationSettings


def find_default_pngquant_binary() -> str:
    return shutil.which("pngquant") or "pngquant"


def find_default_zopfli_binary() -> str:
    return shutil.which("zopflipng") or "zopflipng"


def apply_pixel_format(image: Image.Image, pixel_format: str, matte_rgb: Tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    # These formats are simulated inside a regular PNG by quantizing channel values.
    # RGB formats flatten alpha against the chosen matte color.
    rgba = image.convert("RGBA")
    normalized = pixel_format.upper()
    if normalized == "RGBA8888":
        return rgba
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if normalized in {"RGB888", "RGB565"}:
                if a < 255:
                    alpha = a / 255.0
                    r = round(r * alpha + matte_rgb[0] * (1 - alpha))
                    g = round(g * alpha + matte_rgb[1] * (1 - alpha))
                    b = round(b * alpha + matte_rgb[2] * (1 - alpha))
                a = 255
            if normalized == "RGB565":
                r = round(round(r / 255 * 31) * 255 / 31)
                g = round(round(g / 255 * 63) * 255 / 63)
                b = round(round(b / 255 * 31) * 255 / 31)
            elif normalized == "RGBA4444":
                r = round(round(r / 255 * 15) * 255 / 15)
                g = round(round(g / 255 * 15) * 255 / 15)
                b = round(round(b / 255 * 15) * 255 / 15)
                a = round(round(a / 255 * 15) * 255 / 15)
            elif normalized != "RGB888":
                raise ValueError(f"Unsupported pixel format: {pixel_format}")
            pixels[x, y] = (r, g, b, a)
    return rgba


def downscale_export(image: Image.Image, frames: Sequence[FrameInfo], percent: int) -> tuple[Image.Image, list[FrameInfo]]:
    # Export downscale must keep the atlas image and every JSON coordinate in sync.
    if percent == 100:
        return image, list(frames)
    width = max(1, round(image.width * percent / 100))
    height = max(1, round(image.height * percent / 100))
    scaled = image.resize((width, height), Image.Resampling.LANCZOS)
    factor = percent / 100.0
    scaled_frames = []
    for frame in frames:
        scaled_frames.append(
            FrameInfo(
                name=frame.name,
                x=round(frame.x * factor),
                y=round(frame.y * factor),
                w=max(1, round(frame.w * factor)),
                h=max(1, round(frame.h * factor)),
                source_w=max(1, round(frame.source_w * factor)),
                source_h=max(1, round(frame.source_h * factor)),
                source_x=round(frame.source_x * factor),
                source_y=round(frame.source_y * factor),
                trimmed=frame.trimmed,
                vertices=[(round(x * factor), round(y * factor)) for x, y in frame.vertices],
            )
        )
    return scaled, scaled_frames


def optimize_png(path: str, settings: OptimizationSettings) -> list[str]:
    # pngquant runs before zopflipng: first reduce palette/bit depth, then squeeze
    # the final PNG stream losslessly.
    messages = []
    if settings.pngquant_enabled:
        binary = settings.pngquant_path.strip() or find_default_pngquant_binary()
        if not shutil.which(binary) and not os.path.isfile(binary):
            raise RuntimeError("pngquant is enabled but the executable was not found.")
        command = [
            binary,
            "--force",
            "--ext",
            ".png",
            "--quality",
            f"{settings.pngquant_quality_min}-{settings.pngquant_quality_max}",
            "--speed",
            str(settings.pngquant_speed),
            path,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"pngquant failed: {error_text}")
        messages.append("pngquant applied")

    if settings.zopfli_enabled:
        binary = settings.zopfli_path.strip() or find_default_zopfli_binary()
        if not shutil.which(binary) and not os.path.isfile(binary):
            raise RuntimeError("Zopfli is enabled but the zopflipng executable was not found.")
        if not os.path.basename(binary).startswith("zopflipng"):
            raise RuntimeError("Zopfli PNG optimization requires `zopflipng`.")
        command = [binary, "-y", f"--iterations={settings.zopfli_iterations}", path, path]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Zopfli compression failed: {error_text}")
        messages.append("Zopfli applied")
    return messages
