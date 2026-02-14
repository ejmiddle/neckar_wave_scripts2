from pydantic import BaseModel, Field


class ImageExtractResponse(BaseModel):
    request_id: str = Field(..., description="Correlation id for tracing/logs.")
    status: str = Field(default="ok")
    columns: list[str]
    rows: list[dict[str, str]]
    # Native order payload aligned with `pages/5_Order_Erfassung.py`.
    orders: list[dict]
    warnings: list[str] = []
    model_version: str
