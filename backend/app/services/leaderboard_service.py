from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import GameCache
from app.models.game import GameSession
from app.repositories.score_repository import ScoreRepository
from app.schemas.score import LeaderboardEntry, ScoreResponse


def _score(session: GameSession, rounds_cleared: int) -> dict:
    return {
        "session_id": session.id,
        "player_name": session.player_name,
        "score": session.score,
        "rounds_cleared": rounds_cleared,
        "status": session.status,
        "ended_at": session.ended_at,
    }


class LeaderboardService:
    def __init__(self, db: AsyncSession, cache: GameCache) -> None:
        self.scores = ScoreRepository(db)
        self.cache = cache

    async def recent_scores(self, limit: int) -> list[ScoreResponse]:
        return [ScoreResponse(**_score(*row)) for row in await self.scores.recent(limit)]

    async def leaderboard(self, limit: int) -> list[LeaderboardEntry]:
        """Top scores. Cached briefly and invalidated whenever a game finishes."""
        cached = await self.cache.get_leaderboard(limit)
        if cached is not None:
            return cached
        entries = [
            LeaderboardEntry(rank=rank, **_score(*row))
            for rank, row in enumerate(await self.scores.top(limit), start=1)
        ]
        await self.cache.set_leaderboard(limit, entries)
        return entries
