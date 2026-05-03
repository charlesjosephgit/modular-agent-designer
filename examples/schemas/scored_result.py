from typing import Literal

from pydantic import BaseModel, Field


class ScoredResult(BaseModel):
    score: int = Field(ge=0, le=100, description="Quality score from 0 to 100")
    category: Literal["A", "B", "C"] = Field(description="Grade category: A, B, or C")
    summary: str = Field(min_length=10, description="At least 10 characters")
