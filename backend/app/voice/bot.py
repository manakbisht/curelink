"""The voice game host: turns conversation events into game-service calls.

This processor only manages turn-taking (when to speak, when to listen,
which user turn counts as an answer). Correctness, score and progression
are decided by the game service behind `GameGateway`.
"""

import asyncio
import enum
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from pipecat.frames.frames import (
    DataFrame,
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from sqlalchemy.exc import DBAPIError

from app.cache.redis import GameCache
from app.core.config import Settings
from app.models.database import SessionFactory
from app.schemas.round import ResponseResult
from app.schemas.session import GameState
from app.services.game_service import GameService, normalize_transcript
from app.services.session_service import SessionService
from app.voice import prompts
from app.voice.host import HostEvent, fallback_line

logger = logging.getLogger(__name__)

REPEAT_REQUESTS = frozenset({"repeat", "again", "repeat please", "say again", "say it again", "one more time"})


@dataclass
class StartGameFrame(DataFrame):
    """Queued once the browser is connected: greet the player and read the first sequence."""


@dataclass
class UserTurnEndedFrame(DataFrame):
    """Internal marker. `UserStoppedSpeakingFrame` is a system frame and can overtake
    transcripts still queued as data frames; re-queuing the turn end as a data frame
    guarantees every transcript of the turn has been collected before it is evaluated."""


class Phase(enum.StrEnum):
    IDLE = "idle"  # not started yet
    PRESENTING = "presenting"  # bot is reading the sequence
    INTERRUPTED = "interrupted"  # player talked over the sequence; it will be read again
    LISTENING = "listening"  # waiting for the player's answer
    EVALUATING = "evaluating"  # answer handed to the game service
    FINISHED = "finished"  # game over


class Host(Protocol):
    async def line(self, event: HostEvent, player_name: str) -> str: ...


class CannedHost:
    """Host without an LLM; used when none is configured and in tests."""

    async def line(self, event: HostEvent, player_name: str) -> str:
        return fallback_line(event, player_name)


class GameGateway(Protocol):
    """The slice of the game backend the voice bot needs."""

    async def get_state(self) -> GameState: ...

    async def submit(self, response_id: uuid.UUID, transcript: str, round_number: int) -> ResponseResult: ...


class DatabaseGameGateway:
    """GameGateway backed by SessionService, one short DB transaction per call."""

    def __init__(self, session_id: uuid.UUID, cache: GameCache, settings: Settings, attempts: int = 3) -> None:
        self.session_id = session_id
        self.cache = cache
        self.settings = settings
        self.attempts = attempts

    async def get_state(self) -> GameState:
        async with SessionFactory() as db:
            return await SessionService(GameService(db, self.settings), self.cache).get_state(self.session_id)

    async def submit(self, response_id: uuid.UUID, transcript: str, round_number: int) -> ResponseResult:
        # Transient DB errors are retried with the *same* response id, so a retry
        # after a commit whose acknowledgement was lost cannot score twice.
        for attempt in range(1, self.attempts + 1):
            try:
                async with SessionFactory() as db:
                    service = SessionService(GameService(db, self.settings), self.cache)
                    return await service.submit_response(self.session_id, response_id, transcript, round_number)
            except DBAPIError:
                if attempt == self.attempts:
                    raise
                logger.warning("submit attempt %s failed, retrying", attempt, exc_info=True)
                await asyncio.sleep(0.2 * attempt)
        raise AssertionError("unreachable")


class MemoryGameProcessor(FrameProcessor):
    """Sits between user-turn detection and TTS.

    It receives transcripts and user-turn boundaries from upstream and a
    `playback_done` message from the browser once the bot's audio has finished
    playing there. It emits `TTSSpeakFrame`s for the bot's lines and JSON
    messages for the UI.

    The browser, not `BotStoppedSpeakingFrame`, decides when the sequence has
    been heard: the websocket transport sends audio faster than real time, so
    the server considers the bot finished while the browser is still playing
    it. Opening the answer window early would turn a barge-in near the end of
    the sequence into a scored answer.
    """

    def __init__(self, gateway: GameGateway, host: Host | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.host = host or CannedHost()
        self.phase = Phase.IDLE
        self.state: GameState | None = None
        self._transcripts: list[str] = []
        # Serialises game actions so two user turns can never be evaluated concurrently.
        self._lock = asyncio.Lock()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            return  # consumed by VAD/STT upstream; nothing downstream needs it
        if isinstance(frame, StartGameFrame):
            self.create_task(self._locked(self._introduce()), "introduce")
            return
        if isinstance(frame, UserTurnEndedFrame):
            self.create_task(self._locked(self._on_user_turn_end()), "user_turn_end")
            return
        if isinstance(frame, TranscriptionFrame):
            # Never forward transcripts: the TTS service would read them aloud.
            self._transcripts.append(frame.text)
            await self._notify({"type": "transcript", "text": frame.text, "final": True})
            return
        if isinstance(frame, InterimTranscriptionFrame):
            await self._notify({"type": "transcript", "text": frame.text, "final": False})
            return

        await self.push_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            await self._notify({"type": "user_speaking", "speaking": True})
        elif isinstance(frame, UserStoppedSpeakingFrame):
            await self._notify({"type": "user_speaking", "speaking": False})
            await self.queue_frame(UserTurnEndedFrame())
        elif isinstance(frame, InputTransportMessageFrame) and _message_type(frame) == "playback_done":
            await self._on_playback_done()
        elif isinstance(frame, InterruptionFrame):
            await self._on_interruption()

    # -- conversation flow ---------------------------------------------------

    async def _introduce(self) -> None:
        self.state = await self.gateway.get_state()
        if not self.state.is_active or not self.state.sequence:
            await self._set_phase(Phase.FINISHED)
            await self._say(prompts.game_already_over())
            return
        welcome = await self._react(HostEvent.GREETING)
        await self._present(f"{welcome} {prompts.rules()}")

    async def _present(self, lead_in: str = "") -> None:
        """Read the current round's sequence. The player may answer once it has been fully spoken."""
        assert self.state is not None and self.state.sequence
        await self._set_phase(Phase.PRESENTING)
        text = prompts.present_sequence(self.state.current_round, self.state.sequence)
        await self._say(f"{lead_in} {text}".strip())

    async def _on_playback_done(self) -> None:
        if self.phase is Phase.PRESENTING:
            # Anything heard while the bot was talking is not an answer.
            self._transcripts.clear()
            await self._set_phase(Phase.LISTENING)

    async def _on_interruption(self) -> None:
        # Every user turn broadcasts an interruption; it only matters while the
        # sequence is being read. TTS and the browser have already dropped the
        # rest of the audio, so the player has not heard the full sequence.
        if self.phase is Phase.PRESENTING:
            self._transcripts.clear()
            await self._set_phase(Phase.INTERRUPTED)

    async def _on_user_turn_end(self) -> None:
        transcript = " ".join(self._transcripts).strip()
        self._transcripts.clear()

        if self.phase is Phase.INTERRUPTED:
            # Whatever they said (an early answer, "wait", a cough) is not scored:
            # read the same round again from the start.
            await self._present(await self._react(HostEvent.INTERRUPTED))
            return
        if self.phase is not Phase.LISTENING or not transcript:
            return

        if " ".join(normalize_transcript(transcript)) in REPEAT_REQUESTS:
            await self._present(await self._react(HostEvent.REPEAT))
            return

        await self._evaluate(transcript)

    async def _evaluate(self, transcript: str) -> None:
        assert self.state is not None
        await self._set_phase(Phase.EVALUATING)
        # One id per answer: every retry of this submission is the same response.
        result = await self.gateway.submit(uuid.uuid4(), transcript, self.state.current_round)
        self.state = await self.gateway.get_state()
        await self._notify({"type": "result", **result.model_dump(mode="json")})

        if result.is_correct and self.state.is_active:
            reaction = await self._react(HostEvent.CORRECT)
            await self._present(f"{reaction} {prompts.points_earned(result.points_awarded)}")
        elif result.is_correct:
            await self._set_phase(Phase.FINISHED)
            await self._say(f"{await self._react(HostEvent.COMPLETED)} {prompts.final_score(result.score)}")
        else:
            await self._set_phase(Phase.FINISHED)
            await self._say(f"{await self._react(HostEvent.WRONG)} {prompts.reveal(result.expected, result.score)}")

    # -- helpers ---------------------------------------------------------------

    async def _locked(self, coro) -> None:
        async with self._lock:
            try:
                await coro
            except Exception:
                logger.exception("voice game action failed")
                await self._notify({"type": "error", "message": "Something went wrong on our side."})

    async def _react(self, event: HostEvent) -> str:
        assert self.state is not None
        return await self.host.line(event, self.state.player_name)

    async def _say(self, text: str) -> None:
        await self.push_frame(TTSSpeakFrame(text, append_to_context=False))

    async def _set_phase(self, phase: Phase) -> None:
        self.phase = phase
        await self._notify({"type": "phase", "phase": phase.value})

    async def _notify(self, message: dict[str, Any]) -> None:
        await self.push_frame(OutputTransportMessageUrgentFrame(message=message))


def _message_type(frame: InputTransportMessageFrame) -> str | None:
    return frame.message.get("type") if isinstance(frame.message, dict) else None
