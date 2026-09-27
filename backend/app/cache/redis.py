"""Valkey/Redis cache for active game state and the leaderboard.

PostgreSQL is always the source of truth. Every cache operation is best
effort: failures are logged and treated as a miss so an unavailable cache
slows the app down instead of breaking it.
"""

import logging
import uuid

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.schemas.score import LeaderboardEntry
from app.schemas.session import GameState

logger = logging.getLogger(__name__)

_leaderboard_adapter = TypeAdapter(list[LeaderboardEntry])
LEADERBOARD_KEY = "leaderboard"


def game_key(session_id: uuid.UUID) -> str:
    return f"game:{session_id}"


def create_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1)


class GameCache:
    def __init__(self, redis: Redis, *, game_ttl: int, leaderboard_ttl: int) -> None:
        self.redis = redis
        self.game_ttl = game_ttl
        self.leaderboard_ttl = leaderboard_ttl

    async def get_state(self, session_id: uuid.UUID) -> GameState | None:
        try:
            raw = await self.redis.get(game_key(session_id))
        except RedisError:
            logger.warning("cache read failed for %s", session_id, exc_info=True)
            return None
        return GameState.model_validate_json(raw) if raw else None

    async def set_state(self, state: GameState) -> None:
        try:
            await self.redis.set(game_key(state.session_id), state.model_dump_json(), ex=self.game_ttl)
        except RedisError:
            logger.warning("cache write failed for %s", state.session_id, exc_info=True)

    async def delete_state(self, session_id: uuid.UUID) -> None:
        try:
            await self.redis.delete(game_key(session_id))
        except RedisError:
            logger.warning("cache delete failed for %s", session_id, exc_info=True)

    async def get_leaderboard(self, limit: int) -> list[LeaderboardEntry] | None:
        try:
            raw = await self.redis.get(f"{LEADERBOARD_KEY}:{limit}")
        except RedisError:
            logger.warning("leaderboard cache read failed", exc_info=True)
            return None
        return _leaderboard_adapter.validate_json(raw) if raw else None

    async def set_leaderboard(self, limit: int, entries: list[LeaderboardEntry]) -> None:
        try:
            await self.redis.set(
                f"{LEADERBOARD_KEY}:{limit}", _leaderboard_adapter.dump_json(entries), ex=self.leaderboard_ttl
            )
        except RedisError:
            logger.warning("leaderboard cache write failed", exc_info=True)

    async def invalidate_leaderboard(self) -> None:
        try:
            keys = [key async for key in self.redis.scan_iter(f"{LEADERBOARD_KEY}:*")]
            if keys:
                await self.redis.delete(*keys)
        except RedisError:
            logger.warning("leaderboard cache invalidation failed", exc_info=True)
