from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from api.core.config import settings
from api.routes.health import router as health_router
from api.routes.images import router as images_router
from api.routes.qdrant_demo import router as qdrant_router


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.api_name,
        version=settings.api_version,
        description=(
            "Image upload API for iOS integration. "
            "Extraction logic targets handwritten order slips and falls back to dummy data."
        ),
    )

    @app.get("/")
    def root() -> dict[str, str]:
        return {"message": "hello world", "service": settings.api_name}

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": "Unexpected server error.",
                "type": exc.__class__.__name__,
            },
        )

    app.include_router(health_router)
    app.include_router(images_router)
    app.include_router(qdrant_router)
    return app


app = create_app()
