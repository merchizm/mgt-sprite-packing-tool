from dataclasses import dataclass, field


@dataclass
class FrameInfo:
    name: str
    x: int
    y: int
    w: int
    h: int
    source_w: int
    source_h: int
    source_x: int = 0
    source_y: int = 0
    trimmed: bool = False
    vertices: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Ktx2Settings:
    enabled: bool
    output_path: str
    encoder_mode: str
    ktx_path: str
    generate_mipmaps: bool
    mipmap_filter: str
    use_in_json: bool


@dataclass
class OptimizationSettings:
    pixel_format: str
    export_downscale_percent: int
    pngquant_enabled: bool
    pngquant_path: str
    pngquant_quality_min: int
    pngquant_quality_max: int
    pngquant_speed: int
    zopfli_enabled: bool
    zopfli_path: str
    zopfli_iterations: int
