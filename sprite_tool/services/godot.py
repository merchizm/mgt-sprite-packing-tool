import os
import re
from typing import Dict, List, Sequence, Tuple

from sprite_tool.models import FrameInfo

_NUMBERED = re.compile(r"^(?P<base>.*?)[_\-\s]?(?P<num>\d+)$")


def res_path_for(file_path: str, project_dir: str) -> str:
    # Godot addresses project files as res://relative/path.
    relative = os.path.relpath(os.path.abspath(file_path), os.path.abspath(project_dir))
    if relative.startswith(".."):
        raise ValueError("Atlas PNG must be inside the Godot project folder.")
    return "res://" + relative.replace(os.sep, "/")


def _rect(x: float, y: float, w: float, h: float) -> str:
    return f"Rect2({x}, {y}, {w}, {h})"


def _region_lines(frame: FrameInfo) -> List[str]:
    lines = [f"region = {_rect(frame.x, frame.y, frame.w, frame.h)}"]
    # Trimmed sprites keep their original size/offset through AtlasTexture.margin.
    if frame.trimmed:
        lines.append(
            f"margin = {_rect(frame.source_x, frame.source_y, frame.source_w - frame.w, frame.source_h - frame.h)}"
        )
    return lines


def build_atlas_texture_tres(atlas_res_path: str, frame: FrameInfo) -> str:
    lines = [
        '[gd_resource type="AtlasTexture" load_steps=2 format=3]',
        "",
        f'[ext_resource type="Texture2D" path="{atlas_res_path}" id="1"]',
        "",
        "[resource]",
        'atlas = ExtResource("1")',
        *_region_lines(frame),
        "",
    ]
    return "\n".join(lines)


def safe_file_stem(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name))[0]
    return re.sub(r"[^A-Za-z0-9_\-.]", "_", stem) or "sprite"


def group_animations(frames: Sequence[FrameInfo]) -> Dict[str, List[FrameInfo]]:
    # walk_0, walk_1, run_0 -> {"walk": [...], "run": [...]}.
    # Names without a numeric suffix fall back to a single "default" animation.
    grouped: Dict[str, List[Tuple[int, int, FrameInfo]]] = {}
    for order, frame in enumerate(frames):
        stem = os.path.splitext(frame.name)[0]
        match = _NUMBERED.match(stem)
        if match and match.group("base"):
            animation = re.sub(r"[^A-Za-z0-9_]", "_", match.group("base"))
            number = int(match.group("num"))
        else:
            animation = "default"
            number = order
        grouped.setdefault(animation, []).append((number, order, frame))
    return {name: [item[2] for item in sorted(items, key=lambda i: (i[0], i[1]))] for name, items in grouped.items()}


def build_sprite_frames_tres(atlas_res_path: str, frames: Sequence[FrameInfo], fps: float) -> str:
    animations = group_animations(frames)
    sub_resources: List[str] = []
    animation_blocks: List[str] = []
    next_id = 0
    for name, items in animations.items():
        frame_entries = []
        for frame in items:
            sub_id = f"AtlasTexture_{next_id}"
            next_id += 1
            sub_resources.append(
                "\n".join(
                    [
                        f'[sub_resource type="AtlasTexture" id="{sub_id}"]',
                        'atlas = ExtResource("1")',
                        *_region_lines(frame),
                        "",
                    ]
                )
            )
            frame_entries.append(f'{{\n"duration": 1.0,\n"texture": SubResource("{sub_id}")\n}}')
        animation_blocks.append(
            "{\n"
            f'"frames": [{", ".join(frame_entries)}],\n'
            '"loop": true,\n'
            f'"name": &"{name}",\n'
            f'"speed": {float(fps)}\n'
            "}"
        )
    header = [
        f'[gd_resource type="SpriteFrames" load_steps={len(sub_resources) + 2} format=3]',
        "",
        f'[ext_resource type="Texture2D" path="{atlas_res_path}" id="1"]',
        "",
    ]
    body = ["[resource]", f"animations = [{', '.join(animation_blocks)}]", ""]
    return "\n".join(header) + "\n" + "\n".join(sub_resources) + "\n" + "\n".join(body)


def export_godot_resources(
    atlas_res_path: str,
    frames: Sequence[FrameInfo],
    output_dir: str,
    sheet_stem: str,
    write_atlas_textures: bool,
    write_sprite_frames: bool,
    fps: float,
) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)
    written: List[str] = []
    if write_atlas_textures:
        used = set()
        for frame in frames:
            stem = safe_file_stem(frame.name)
            candidate = stem
            counter = 1
            while candidate in used:
                candidate = f"{stem}_{counter}"
                counter += 1
            used.add(candidate)
            path = os.path.join(output_dir, f"{candidate}.tres")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(build_atlas_texture_tres(atlas_res_path, frame))
            written.append(path)
    if write_sprite_frames:
        path = os.path.join(output_dir, f"{sheet_stem}_frames.tres")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(build_sprite_frames_tres(atlas_res_path, frames, fps))
        written.append(path)
    return written
