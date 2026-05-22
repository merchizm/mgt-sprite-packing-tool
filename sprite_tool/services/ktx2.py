import os
import shutil
import subprocess
import sys


def find_default_ktx_binary() -> str:
    if sys.platform.startswith("win"):
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "KTX-Software", "bin", "ktx.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "KTX-Software", "bin", "ktx.exe"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return shutil.which("ktx.exe") or shutil.which("ktx") or "ktx"
    return shutil.which("ktx") or "ktx"


def run_ktx2_export(
    source_image_path: str,
    output_ktx2_path: str,
    encoder_mode: str,
    ktx_path: str,
    generate_mipmaps: bool,
    mipmap_filter: str,
) -> str:
    binary = ktx_path.strip() or find_default_ktx_binary()
    resolved = shutil.which(binary) if not os.path.isabs(binary) else binary
    if not resolved or not os.path.exists(resolved):
        raise ValueError(
            "KTX2 export requires the modern `ktx` tool. "
            "Install KTX-Software or set the ktx executable path."
        )

    os.makedirs(os.path.dirname(output_ktx2_path) or ".", exist_ok=True)
    command = [resolved, "create", "--format", "R8G8B8A8_SRGB"]
    if encoder_mode == "UASTC":
        command.extend(["--encode", "uastc"])
    else:
        command.extend(["--encode", "basis-lz"])
    if generate_mipmaps:
        command.extend(["--generate-mipmap", "--mipmap-filter", mipmap_filter])
    command.extend([source_image_path, output_ktx2_path])

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "Unknown error").strip()
        raise RuntimeError(f"`ktx create` failed: {error_text}")
    return " ".join(command)
