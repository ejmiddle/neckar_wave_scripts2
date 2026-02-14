from fastapi import Header, HTTPException, status

from api.core.config import settings


def verify_bearer_token(authorization: str | None = Header(default=None)) -> None:
    """
    Simple bearer check.

    If `API_BEARER_TOKEN` is empty, auth is disabled for local/dev use.
    """
    configured_token = settings.api_bearer_token
    if not configured_token:
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )

    token = authorization.split(" ", 1)[1].strip()
    if token != configured_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
        )

