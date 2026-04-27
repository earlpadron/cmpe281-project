"""
Shared image-resize core.

Imported by:
  * cloud/lambda_function.py        (runs in AWS Lambda)
  * greengrass/components/cmpe281.edge_resizer/artifacts/worker.py
                                    (runs as a Greengrass v2 component on the Pi)
  * backend/main.py                 (the in-process fallback when USE_GREENGRASS_IPC=false)

Keeping a single implementation guarantees that the EDGE and CLOUD paths produce
byte-identical thumbnails for the same input.
"""

from io import BytesIO
from typing import Optional, Tuple

from PIL import Image

DEFAULT_TARGET_SIZE: Tuple[int, int] = (800, 800)


def _normalize_format(img_format: Optional[str], fallback: str = "JPEG") -> str:
    if not img_format:
        return fallback
    fmt = img_format.upper()
    if fmt == "JPG":
        fmt = "JPEG"
    return fmt


def resize_bytes(
    image_bytes: bytes,
    target_size: Tuple[int, int] = DEFAULT_TARGET_SIZE,
    img_format: Optional[str] = None,
) -> bytes:
    """Resize a raw image payload in memory and return the encoded bytes."""
    img = Image.open(BytesIO(image_bytes))
    detected_format = img.format
    resized = img.resize(target_size, Image.Resampling.LANCZOS)

    save_format = _normalize_format(img_format or detected_format)
    buffer = BytesIO()
    resized.save(buffer, format=save_format)
    return buffer.getvalue()


def resize_file(
    input_path: str,
    output_path: str,
    target_size: Tuple[int, int] = DEFAULT_TARGET_SIZE,
    img_format: Optional[str] = None,
) -> str:
    """File-based variant used by the Greengrass worker (avoids IPC payload limits)."""
    with open(input_path, "rb") as f:
        data = f.read()
    out = resize_bytes(data, target_size=target_size, img_format=img_format)
    with open(output_path, "wb") as f:
        f.write(out)
    return output_path
