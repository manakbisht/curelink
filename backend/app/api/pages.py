"""Server-rendered UI. HTMX swaps these partials; voice is handled by static/voice.js."""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.dependencies import LeaderboardServiceDep, SessionServiceDep
from app.models.game import RoundResult, SessionStatus
from app.services.game_service import SessionNotFound

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


async def _game_context(sessions: SessionServiceDep, session_id: uuid.UUID) -> dict:
    session = await sessions.get_session(session_id)
    finished = session.status is not SessionStatus.ACTIVE
    if finished:
        session = await sessions.get_session(session_id, include_rounds=True)
    return {
        "session": session,
        "active": not finished,
        "rounds_cleared": sum(r.result is RoundResult.CORRECT for r in session.rounds),
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, sessions: SessionServiceDep, session: uuid.UUID | None = None) -> Response:
    context: dict = {}
    if session is not None:
        try:
            context = await _game_context(sessions, session)
        except SessionNotFound:
            context = {}
    return templates.TemplateResponse(request, "index.html", context)


@router.post("/ui/sessions", response_class=HTMLResponse)
async def start_game(
    request: Request, sessions: SessionServiceDep, player_name: Annotated[str, Form(max_length=40)] = ""
) -> Response:
    state = await sessions.start_session(player_name.strip() or "Anonymous")
    context = await _game_context(sessions, state.session_id)
    response = templates.TemplateResponse(request, "partials/game.html", context)
    response.headers["HX-Push-Url"] = f"/?session={state.session_id}"
    return response


@router.get("/ui/sessions/{session_id}", response_class=HTMLResponse)
async def game_state(request: Request, session_id: uuid.UUID, sessions: SessionServiceDep) -> Response:
    context = await _game_context(sessions, session_id)
    response = templates.TemplateResponse(request, "partials/game_state.html", context)
    if not context["active"]:
        response.headers["HX-Trigger"] = "game-ended"
    return response


@router.post("/ui/sessions/{session_id}/end", response_class=HTMLResponse)
async def end_game(request: Request, session_id: uuid.UUID, sessions: SessionServiceDep) -> Response:
    await sessions.end_session(session_id)
    response = templates.TemplateResponse(
        request, "partials/game_state.html", await _game_context(sessions, session_id)
    )
    response.headers["HX-Trigger"] = "game-ended"
    return response


@router.get("/ui/scores", response_class=HTMLResponse)
async def scores(request: Request, leaderboard: LeaderboardServiceDep) -> Response:
    return templates.TemplateResponse(
        request,
        "partials/scores.html",
        {"leaderboard": await leaderboard.leaderboard(10), "recent": await leaderboard.recent_scores(5)},
    )
