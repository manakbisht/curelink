from typing import Annotated

from fastapi import APIRouter, Query

from app.core.dependencies import LeaderboardServiceDep
from app.schemas.score import LeaderboardEntry, ScoreResponse

router = APIRouter(tags=["scores"])

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/scores/recent")
async def recent_scores(leaderboard: LeaderboardServiceDep, limit: Limit = 10) -> list[ScoreResponse]:
    return await leaderboard.recent_scores(limit)


@router.get("/leaderboard")
async def get_leaderboard(leaderboard: LeaderboardServiceDep, limit: Limit = 10) -> list[LeaderboardEntry]:
    return await leaderboard.leaderboard(limit)
