from pydantic import BaseModel, Field


class ClassificationResult(BaseModel):
    label: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    alternative_labels: list[str] = Field(default_factory=list, max_length=3)
