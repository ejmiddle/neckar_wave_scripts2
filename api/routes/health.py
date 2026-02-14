from fastapi import APIRouter

from api.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.api_name, "version": settings.api_version}

