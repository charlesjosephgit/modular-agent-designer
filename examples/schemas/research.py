from pydantic import BaseModel, Field


class ResearchReport(BaseModel):
    topic: str
    key_findings: list[str] = Field(min_length=1, max_length=10)
    sources_mentioned: list[str] = Field(default_factory=list)
    confidence: str = Field(description="high, medium, or low")
    follow_up_questions: list[str] = Field(default_factory=list, max_length=3)
