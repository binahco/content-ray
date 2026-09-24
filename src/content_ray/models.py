from __future__ import annotations

from pydantic import BaseModel


class ContentSummary(BaseModel):
    """Resumen del corpus escaneado (`content-summary-v1`)."""

    corpus: str
    files: int
    sections: int = 0
    total_tokens: int = 0
    highlights: list[str] = []