from typing import Literal

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    validation_result: Literal["success", "fail"]
    reason: str = Field(description="Brief explanation of why validation passed or failed")
