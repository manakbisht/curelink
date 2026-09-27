import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.game import RoundResult


class RoundResponse(BaseModel):
    """A round as shown to clients. The sequence is only revealed once the round is resolved."""

    model_config = ConfigDict(from_attributes=True)

    round_number: int
    result: RoundResult
    sequence_length: int
    sequence: list[str] | None
    transcript: str | None = None


class ResponseRequest(BaseModel):
    response_id: uuid.UUID = Field(description="Client-generated id; resubmitting the same id is a no-op.")
    transcript: str = Field(max_length=2000)
    round_number: int | None = Field(
        default=None, description="Round the answer is for. If it is no longer current the request is rejected."
    )


class ResponseResult(BaseModel):
    response_id: uuid.UUID
    round_number: int
    is_correct: bool
    expected: list[str]
    points_awarded: int
    score: int
    status: str
    next_round: int | None
    # True when this response id had already been processed; nothing was changed.
    duplicate: bool = False
