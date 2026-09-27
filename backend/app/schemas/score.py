import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.game import SessionStatus


class ScoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    player_name: str
    score: int
    rounds_cleared: int
    status: SessionStatus
    ended_at: datetime | None


class LeaderboardEntry(ScoreResponse):
    rank: int
