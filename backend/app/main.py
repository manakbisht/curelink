import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import pages, scores, sessions, voice
from app.cache.redis import GameCache, create_redis
from app.core.config import REPO_ROOT, get_settings
from app.models.database import engine
from app.services.game_service import GameError, SessionNotFound


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    redis = create_redis(settings.redis_url)
    app.state.cache = GameCache(redis, game_ttl=settings.game_state_ttl, leaderboard_ttl=settings.leaderboard_ttl)
    yield
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="Memory Card Voice Bot", lifespan=lifespan)
app.include_router(sessions.router)
app.include_router(scores.router)
app.include_router(voice.router)
app.include_router(pages.router)
app.mount("/static", StaticFiles(directory=REPO_ROOT / "backend" / "static"), name="static")


@app.exception_handler(GameError)
async def game_error_handler(_: Request, exc: GameError) -> JSONResponse:
    code = status.HTTP_404_NOT_FOUND if isinstance(exc, SessionNotFound) else status.HTTP_409_CONFLICT
    return JSONResponse(status_code=code, content={"detail": str(exc) or type(exc).__name__, "error": type(exc).__name__})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
