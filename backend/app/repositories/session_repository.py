import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GameSession, SessionStatus


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, player_name: str) -> GameSession:
        session = GameSession(player_name=player_name, status=SessionStatus.ACTIVE, score=0, current_round=1)
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_by_id(self, session_id: uuid.UUID, *, for_update: bool = False) -> GameSession | None:
        """Load a session. `for_update` takes a row lock so concurrent scoring is serialised."""
        stmt = select(GameSession).where(GameSession.id == session_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def update_status(self, session: GameSession, status: SessionStatus) -> None:
        session.status = status
        if status is not SessionStatus.ACTIVE:
            session.ended_at = datetime.now(UTC)
        await self.db.flush()

    async def update_score(self, session: GameSession, *, score: int, current_round: int) -> None:
        session.score = score
        session.current_round = current_round
        await self.db.flush()
