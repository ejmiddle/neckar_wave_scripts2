import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_name: str = os.getenv("API_NAME", "Neckar Wave Image API")
    api_version: str = os.getenv("API_VERSION", "1.0.0")
    api_bearer_token: str = os.getenv("API_BEARER_TOKEN", "").strip()
    max_image_bytes: int = int(os.getenv("API_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))


settings = Settings()

