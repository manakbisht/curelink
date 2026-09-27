import uuid

from app.cache.redis import GameCache
from app.models.game import GameSession, Round, RoundResult
from app.schemas.round import ResponseResult, RoundResponse
from app.schemas.session import GameState, SessionResponse
from app.services.game_service import GameService, SubmitOutcome


def build_state(session: GameSession, current: Round | None) -> GameState:
    pending = current is not None and current.result is RoundResult.PENDING
    return GameState(
        session_id=session.id,
        player_name=session.player_name,
        status=session.status,
        score=session.score,
        current_round=session.current_round,
        sequence=list(current.sequence) if pending else None,
        started_at=session.started_at,
        ended_at=session.ended_at,
    )


class SessionService:
    """Coordinates the durable game (via GameService) with the cached game state.

    Writes go to PostgreSQL first and the cache is refreshed after commit.
    Reads try the cache and fall back to PostgreSQL.
    """

    def __init__(self, game: GameService, cache: GameCache) -> None:
        self.game = game
        self.cache = cache

    async def start_session(self, player_name: str) -> GameState:
        session, first_round = await self.game.start_game(player_name)
        state = build_state(session, first_round)
        await self.cache.set_state(state)
        return state

    async def get_state(self, session_id: uuid.UUID) -> GameState:
        cached = await self.cache.get_state(session_id)
        if cached is not None:
            return cached
        state = build_state(*await self.game.get_game(session_id))
        await self.cache.set_state(state)
        return state

    async def get_session(self, session_id: uuid.UUID, *, include_rounds: bool = False) -> SessionResponse:
        """Public view of a session. The pending sequence is never exposed.

        The live fields come from the cached state; round history is read from PostgreSQL.
        """
        state = await self.get_state(session_id)
        rounds = []
        if include_rounds:
            rounds = [await self._round_response(r) for r in await self.game.rounds.list_for_session(session_id)]
        return SessionResponse(
            session_id=state.session_id,
            player_name=state.player_name,
            status=state.status,
            score=state.score,
            current_round=state.current_round,
            sequence_length=len(state.sequence) if state.sequence else None,
            started_at=state.started_at,
            ended_at=state.ended_at,
            rounds=rounds,
        )

    async def submit_response(
        self, session_id: uuid.UUID, response_id: uuid.UUID, transcript: str, round_number: int | None = None
    ) -> ResponseResult:
        outcome = await self.game.submit_response(session_id, response_id, transcript, round_number)
        if not outcome.duplicate:
            await self._refresh(outcome)
        return ResponseResult(
            response_id=outcome.response_id,
            round_number=outcome.round_number,
            is_correct=outcome.is_correct,
            expected=outcome.expected,
            points_awarded=outcome.points_awarded,
            score=outcome.session.score,
            status=outcome.session.status.value,
            next_round=outcome.next_round.round_number if outcome.next_round else None,
            duplicate=outcome.duplicate,
        )

    async def end_session(self, session_id: uuid.UUID) -> GameState:
        session = await self.game.end_game(session_id)
        state = build_state(*await self.game.get_game(session.id))
        await self.cache.set_state(state)
        await self.cache.invalidate_leaderboard()
        return state

    async def _refresh(self, outcome: SubmitOutcome) -> None:
        await self.cache.set_state(build_state(outcome.session, outcome.next_round))
        if outcome.game_over:
            await self.cache.invalidate_leaderboard()

    async def _round_response(self, round_: Round) -> RoundResponse:
        resolved = round_.result is not RoundResult.PENDING
        response = await self.game.responses.get_by_round(round_.id) if resolved else None
        return RoundResponse(
            round_number=round_.round_number,
            result=round_.result,
            sequence_length=len(round_.sequence),
            sequence=list(round_.sequence) if resolved else None,
            transcript=response.transcript if response else None,
        )
