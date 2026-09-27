import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.game import SessionStatus
from app.schemas.round import RoundResponse


class SessionCreate(BaseModel):
    player_name: str = Field(default="Anonymous", min_length=1, max_length=40)


class GameState(BaseModel):
    """Snapshot of a game; this is what gets cached under `game:{session_id}`."""

    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    player_name: str
    status: SessionStatus
    score: int
    current_round: int
    # The pending round's sequence, or None once the game is over.
    sequence: list[str] | None
    started_at: datetime
    ended_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.status is SessionStatus.ACTIVE


class SessionResponse(BaseModel):
    session_id: uuid.UUID
    player_name: str
    status: SessionStatus
    score: int
    current_round: int
    sequence_length: int | None
    started_at: datetime
    ended_at: datetime | None
    rounds: list[RoundResponse] = []
