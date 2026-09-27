from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import GameCache
from app.core.config import Settings, get_settings
from app.models.database import get_db
from app.services.game_service import GameService
from app.services.session_service import SessionService

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def get_cache(request: Request) -> GameCache:
    return request.app.state.cache


CacheDep = Annotated[GameCache, Depends(get_cache)]


def get_game_service(db: DbDep, settings: SettingsDep) -> GameService:
    return GameService(db, settings)


def get_session_service(game: Annotated[GameService, Depends(get_game_service)], cache: CacheDep) -> SessionService:
    return SessionService(game, cache)


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
