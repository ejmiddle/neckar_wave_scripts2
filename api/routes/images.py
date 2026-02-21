import json
import uuid
from time import perf_counter

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from api.core.config import settings
from api.deps.security import verify_bearer_token
from api.models.image_extract import ImageExtractResponse
from api.services.image_extractor import extract_orders_from_image
from src.logging_config import logger

router = APIRouter(prefix="/api/v1/images", tags=["images"])

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/heic",
    "image/heif",
}


def _looks_like_image(content: bytes) -> bool:
    if content.startswith(b"\xff\xd8\xff"):  # JPEG
        return True
    if content.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
        return True
    if len(content) >= 12 and content[4:8] == b"ftyp":  # HEIC/HEIF family
        brand = content[8:12]
        return brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}
    return False


@router.post("/extract", response_model=ImageExtractResponse)
async def extract_from_image(
    image: UploadFile = File(...),
    metadata: str = Form(default="{}"),
    x_request_id: str | None = Header(default=None),
    _: None = Depends(verify_bearer_token),
) -> ImageExtractResponse:
    started_at = perf_counter()
    if image.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {image.content_type}",
        )

    read_started_at = perf_counter()
    content = await image.read()
    read_duration_ms = round((perf_counter() - read_started_at) * 1000, 1)
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image is empty.")
    if len(content) > settings.max_image_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds max size of {settings.max_image_bytes} bytes.",
        )
    if not _looks_like_image(content):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file does not look like a valid supported image.",
        )

    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid metadata JSON: {exc}",
        ) from exc
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Metadata must be a JSON object.",
        )

    request_id = x_request_id or str(uuid.uuid4())
    logger.info(
        "Image extract request accepted request_id=%s content_type=%s image_bytes=%s read_ms=%s",
        request_id,
        image.content_type,
        len(content),
        read_duration_ms,
    )
    response = await run_in_threadpool(
        extract_orders_from_image,
        request_id=request_id,
        image_bytes=content,
        content_type=image.content_type or "application/octet-stream",
        metadata=parsed_metadata,
    )
    logger.info(
        "Image extract request finished request_id=%s total_ms=%s",
        request_id,
        round((perf_counter() - started_at) * 1000, 1),
    )
    return response


@router.get("/extract")
async def extract_from_image_help() -> dict[str, str]:
    return {
        "detail": (
            "Use POST /api/v1/images/extract with multipart form-data: "
            "field 'image' (file) and optional field 'metadata' (JSON string)."
        )
    }
