import os
import io
import uuid
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

load_dotenv()

# ── AWS config ────────────────────────────────────────────────────────────────
BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "cmpe281-benchmark-data-a81aa9e4")
AWS_REGION  = os.getenv("AWS_REGION", "us-east-1")

s3 = boto3.client("s3", region_name=AWS_REGION)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="CloudResize API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # tighten this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
FORMAT_MAP = {"jpeg": "JPEG", "jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


def decide_route(file_size: int) -> str:
    """Stub — replace with ML model inference in Phase 3."""
    return "cloud"


def resize_image(data: bytes, width: int, height: int, out_format: str) -> tuple[bytes, str]:
    """
    Resize image bytes to (width x height) using Lanczos resampling.
    Returns (resized_bytes, pillow_format_string).
    """
    img = Image.open(io.BytesIO(data))
    original_format = img.format or "JPEG"

    # Resolve output format
    pil_format = FORMAT_MAP.get(out_format.lower(), original_format)

    resized = img.resize((width, height), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    save_kwargs = {}
    if pil_format == "JPEG":
        save_kwargs["quality"] = 90
        # JPEG doesn't support alpha channel
        if resized.mode in ("RGBA", "P"):
            resized = resized.convert("RGB")
    resized.save(buf, format=pil_format, **save_kwargs)
    return buf.getvalue(), pil_format


def upload_to_s3(data: bytes, key: str, content_type: str) -> str:
    """Upload bytes to S3 and return a presigned download URL (1 hour)."""
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=3600,
    )
    return url


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/resize")
async def resize_endpoint(
    file: UploadFile = File(...),
    width: int = Form(800),
    height: int = Form(800),
    format: str = Form("original"),
):
    """
    Accept one image, resize it, upload to S3, return presigned URL.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported type: {ext}")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="File is empty.")

    file_size = len(raw)
    route = decide_route(file_size)

    try:
        resized_bytes, pil_format = resize_image(raw, width, height, format)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Image processing failed: {e}")

    ext_out = pil_format.lower().replace("jpeg", "jpg")
    request_id = str(uuid.uuid4())
    s3_key = f"resized/{request_id}.{ext_out}"
    content_type = f"image/{'jpeg' if ext_out == 'jpg' else ext_out}"

    try:
        download_url = upload_to_s3(resized_bytes, s3_key, content_type)
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=502, detail=f"S3 upload failed: {e}")

    return JSONResponse({
        "request_id": request_id,
        "filename": file.filename,
        "route": route,
        "width": width,
        "height": height,
        "format": pil_format,
        "original_size_bytes": file_size,
        "resized_size_bytes": len(resized_bytes),
        "s3_key": s3_key,
        "download_url": download_url,
    })


@app.post("/resize-batch")
async def resize_batch(
    files: list[UploadFile] = File(...),
    width: int = Form(800),
    height: int = Form(800),
    format: str = Form("original"),
):
    """
    Accept multiple images, resize all, return list of results.
    If only one file → returns single result.
    If multiple → also returns a presigned ZIP via /download-zip.
    """
    results = []
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            results.append({"filename": file.filename, "error": f"Unsupported type {ext}"})
            continue

        raw = await file.read()
        if not raw:
            results.append({"filename": file.filename, "error": "Empty file"})
            continue

        try:
            resized_bytes, pil_format = resize_image(raw, width, height, format)
        except Exception as e:
            results.append({"filename": file.filename, "error": str(e)})
            continue

        ext_out = pil_format.lower().replace("jpeg", "jpg")
        request_id = str(uuid.uuid4())
        s3_key = f"resized/{request_id}.{ext_out}"
        content_type = f"image/{'jpeg' if ext_out == 'jpg' else ext_out}"

        try:
            download_url = upload_to_s3(resized_bytes, s3_key, content_type)
            results.append({
                "request_id": request_id,
                "filename": file.filename,
                "route": decide_route(len(raw)),
                "width": width,
                "height": height,
                "format": pil_format,
                "original_size_bytes": len(raw),
                "resized_size_bytes": len(resized_bytes),
                "s3_key": s3_key,
                "download_url": download_url,
            })
        except (BotoCoreError, ClientError) as e:
            results.append({"filename": file.filename, "error": f"S3 upload failed: {e}"})

    return JSONResponse({"results": results, "total": len(results)})


# ── Serve the frontend ────────────────────────────────────────────────────────
# Place index.html in a folder called "static/" next to main.py
# Then visit http://localhost:8000/
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
