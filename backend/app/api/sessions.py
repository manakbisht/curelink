import uuid

from fastapi import APIRouter, status

from app.core.dependencies import SessionServiceDep
from app.schemas.round import ResponseRequest, ResponseResult
from app.schemas.session import SessionCreate, SessionResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, sessions: SessionServiceDep) -> SessionResponse:
    state = await sessions.start_session(body.player_name)
    return await sessions.get_session(state.session_id)


@router.get("/{session_id}")
async def get_session(session_id: uuid.UUID, sessions: SessionServiceDep, include_rounds: bool = False) -> SessionResponse:
    return await sessions.get_session(session_id, include_rounds=include_rounds)


@router.post("/{session_id}/responses")
async def submit_response(session_id: uuid.UUID, body: ResponseRequest, sessions: SessionServiceDep) -> ResponseResult:
    """Score an answer for the current round. Resubmitting the same `response_id` never scores twice."""
    return await sessions.submit_response(session_id, body.response_id, body.transcript, body.round_number)


@router.post("/{session_id}/end")
async def end_session(session_id: uuid.UUID, sessions: SessionServiceDep) -> SessionResponse:
    await sessions.end_session(session_id)
    return await sessions.get_session(session_id, include_rounds=True)
