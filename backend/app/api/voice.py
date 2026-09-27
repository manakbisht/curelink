import uuid

from fastapi import APIRouter, WebSocket, status

from app.core.config import get_settings
from app.services.game_service import SessionNotFound
from app.voice.bot import DatabaseGameGateway
from app.voice.pipeline import run_voice_session

router = APIRouter(tags=["voice"])


@router.websocket("/ws/sessions/{session_id}")
async def voice_session(websocket: WebSocket, session_id: uuid.UUID) -> None:
    """Bidirectional audio for one game. See app/voice/serializer.py for the wire format."""
    settings = get_settings()
    gateway = DatabaseGameGateway(session_id, websocket.app.state.cache, settings)
    try:
        await gateway.get_state()
    except SessionNotFound:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="unknown session")
        return
    await websocket.accept()
    await run_voice_session(websocket, gateway, settings)
