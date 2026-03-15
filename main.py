import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="CMPE 281 Backend", version="1.0.0")

# Local folder used while AWS / edge integration is not ready
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Allowed image extensions
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def decide_route(file_size: int, content_type: str | None = None) -> str:
    """
    Temporary routing stub.

    Right now this is intentionally simple.
    Later, this can be replaced with:
    - rule-based logic
    - ML model prediction
    - real edge/cloud decision engine
    """
    # For now, always route to cloud in the response
    return "cloud"


@app.get("/")
def root():
    return {"message": "Backend is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/resize")
async def upload_image(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    original_filename = file.filename
    file_ext = Path(original_filename).suffix.lower()

    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Use .jpg, .jpeg, .png, or .webp",
        )

    try:
        file_bytes = await file.read()
        file_size = len(file_bytes)

        if file_size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        request_id = str(uuid.uuid4())
        route = decide_route(file_size=file_size, content_type=file.content_type)

        saved_filename = f"{request_id}{file_ext}"
        saved_path = UPLOAD_DIR / saved_filename

        with open(saved_path, "wb") as f:
            f.write(file_bytes)

        response_data = {
            "message": "Upload successful",
            "request_id": request_id,
            "filename": original_filename,
            "saved_filename": saved_filename,
            "saved_path": str(saved_path),
            "file_size_bytes": file_size,
            "content_type": file.content_type,
            "route": route,
        }

        return JSONResponse(status_code=200, content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")