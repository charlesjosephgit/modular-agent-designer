from pydantic import BaseModel, Field


class ArticleSummary(BaseModel):
    title: str
    bullets: list[str] = Field(min_length=1, max_length=5)
    sentiment: str = Field(description="positive, neutral, or negative")
    word_count_estimate: int
