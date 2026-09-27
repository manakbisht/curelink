from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GameSession, SessionStatus


class ScoreRepository:
    """Read-only queries over finished games."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def recent(self, limit: int) -> list[GameSession]:
        stmt = (
            select(GameSession)
            .where(GameSession.status != SessionStatus.ACTIVE)
            .order_by(GameSession.ended_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def top(self, limit: int) -> list[GameSession]:
        stmt = (
            select(GameSession)
            .where(GameSession.status != SessionStatus.ACTIVE)
            .order_by(GameSession.score.desc(), GameSession.ended_at.asc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())
