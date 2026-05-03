from typing import Literal

from pydantic import BaseModel, Field


class ToolCallStatus(BaseModel):
    agent_status: Literal["success", "fail"]
    agent_outcome: str = Field(
        description="Concise summary of the tool call result."
    )
