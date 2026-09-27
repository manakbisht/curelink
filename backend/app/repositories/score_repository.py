from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GameSession, Round, RoundResult, SessionStatus


class ScoreRepository:
    """Read-only queries over finished games."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def recent(self, limit: int) -> list[tuple[GameSession, int]]:
        """Most recently finished games with their number of cleared rounds."""
        return await self._fetch(self._finished().order_by(GameSession.ended_at.desc()).limit(limit))

    async def top(self, limit: int) -> list[tuple[GameSession, int]]:
        """Highest scores; ties go to whoever got there first."""
        stmt = self._finished().order_by(GameSession.score.desc(), GameSession.ended_at.asc()).limit(limit)
        return await self._fetch(stmt)

    @staticmethod
    def _finished() -> Select:
        rounds_cleared = (
            select(func.count())
            .where(Round.session_id == GameSession.id, Round.result == RoundResult.CORRECT)
            .correlate(GameSession)
            .scalar_subquery()
        )
        return select(GameSession, rounds_cleared).where(GameSession.status != SessionStatus.ACTIVE)

    async def _fetch(self, stmt: Select) -> list[tuple[GameSession, int]]:
        return [(session, cleared) for session, cleared in (await self.db.execute(stmt)).all()]
