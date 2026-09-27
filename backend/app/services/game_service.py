"""Deterministic game rules and state transitions.

Everything that decides correctness, score or progression lives here. The
voice layer and the LLM only ever call into this module; they never decide
game outcomes themselves.
"""

import difflib
import random
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.game import GameSession, Response, Round, RoundResult, SessionStatus
from app.repositories.response_repository import ResponseRepository
from app.repositories.round_repository import RoundRepository
from app.repositories.session_repository import SessionRepository

# Short, concrete, easy-to-say nouns. None of them is a homophone of a common
# word or a filler, and no two are close enough to be fuzzy-matched to each
# other (see tests/unit/test_sequence_validation.py).
CARDS: tuple[str, ...] = (
    "apple", "anchor", "balloon", "banana", "blanket", "bottle", "button", "camera",
    "candle", "carpet", "castle", "compass", "dragon", "forest", "garden", "guitar",
    "hammer", "helmet", "jacket", "kettle", "ladder", "lemon", "mountain", "orange",
    "pencil", "penguin", "piano", "pillow", "rabbit", "river", "rocket", "saddle",
    "tiger", "tomato", "turtle", "violin", "wallet", "window", "zebra", "coffee",
)  # fmt: skip

# Words people naturally say between items; ignored when comparing.
FILLER_WORDS = frozenset(
    {"a", "an", "and", "the", "then", "um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "okay", "ok", "so", "like"}
)

# Minimum similarity for two words to count as the same card despite a small
# transcription slip ("pencils" for "pencil", "tomatoe" for "tomato").
FUZZY_MATCH_RATIO = 0.8

_NON_LETTERS = re.compile(r"[^a-z]+")


# --------------------------------------------------------------------------- #
# Pure functions
# --------------------------------------------------------------------------- #


def normalize_transcript(transcript: str) -> list[str]:
    """Lowercase, strip punctuation, split into words and drop filler words."""
    words = _NON_LETTERS.sub(" ", transcript.lower()).split()
    return [w for w in words if w not in FILLER_WORDS]


def words_match(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if actual in (expected + "s", expected + "es"):
        return True
    return len(expected) >= 4 and difflib.SequenceMatcher(None, expected, actual).ratio() >= FUZZY_MATCH_RATIO


def evaluate_response(expected: Sequence[str], actual: Sequence[str] | str) -> bool:
    """True if `actual` repeats `expected` exactly: same words, same order, nothing missing or extra.

    `actual` may be a raw transcript or an already tokenised list of words.
    """
    tokens = normalize_transcript(actual) if isinstance(actual, str) else normalize_transcript(" ".join(actual))
    expected_tokens = [w.lower() for w in expected]
    return len(tokens) == len(expected_tokens) and all(
        words_match(e, a) for e, a in zip(expected_tokens, tokens, strict=True)
    )


def sequence_length_for_round(round_number: int) -> int:
    """Round 1 has 2 cards and every round adds one more."""
    if round_number < 1:
        raise ValueError("round_number starts at 1")
    return min(round_number + 1, len(CARDS))


def points_for_round(round_number: int, points_per_item: int) -> int:
    return sequence_length_for_round(round_number) * points_per_item


def generate_sequence(round_number: int, rng: random.Random | None = None) -> list[str]:
    """Pick distinct cards for a round."""
    return (rng or random.SystemRandom()).sample(CARDS, sequence_length_for_round(round_number))


# --------------------------------------------------------------------------- #
# Stateful service
# --------------------------------------------------------------------------- #


class GameError(Exception):
    """Base class for game rule violations that callers should surface as 4xx."""


class SessionNotFound(GameError):
    pass


class GameNotActive(GameError):
    pass


class StaleRound(GameError):
    """The answer targets a round that is no longer the current one."""


@dataclass(frozen=True)
class SubmitOutcome:
    response_id: uuid.UUID
    round_number: int
    is_correct: bool
    expected: list[str]
    points_awarded: int
    session: GameSession
    next_round: Round | None
    duplicate: bool

    @property
    def game_over(self) -> bool:
        return self.session.status is not SessionStatus.ACTIVE


class GameService:
    """Owns game state transitions. Every public method is one database transaction."""

    def __init__(self, db: AsyncSession, settings: Settings, rng: random.Random | None = None) -> None:
        self.db = db
        self.settings = settings
        self.rng = rng
        self.sessions = SessionRepository(db)
        self.rounds = RoundRepository(db)
        self.responses = ResponseRepository(db)

    async def start_game(self, player_name: str) -> tuple[GameSession, Round]:
        session = await self.sessions.create(player_name)
        first_round = await self.rounds.create(session.id, 1, generate_sequence(1, self.rng))
        await self.db.commit()
        return session, first_round

    async def get_game(self, session_id: uuid.UUID) -> tuple[GameSession, Round | None]:
        session = await self.sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFound(f"session {session_id} not found")
        return session, await self.rounds.get_current_round(session_id)

    async def submit_response(
        self,
        session_id: uuid.UUID,
        response_id: uuid.UUID,
        transcript: str,
        round_number: int | None = None,
    ) -> SubmitOutcome:
        """Score one answer for the current round.

        Safe to call repeatedly with the same `response_id`: the first call
        scores it, every later call returns the stored outcome with
        `duplicate=True` and changes nothing.
        """
        try:
            return await self._submit(session_id, response_id, transcript, round_number)
        except GameError:
            await self.db.rollback()  # release the row lock
            raise
        except IntegrityError:
            # Lost a race with a concurrent submission for the same response or
            # round. The database refused the second write; report what won.
            await self.db.rollback()
            existing = await self.responses.get_by_id(response_id)
            if existing is None:
                raise StaleRound("round was already answered") from None
            return await self._replay(session_id, existing)

    async def end_game(self, session_id: uuid.UUID) -> GameSession:
        """End a game on the player's request. Idempotent."""
        session = await self.sessions.get_by_id(session_id, for_update=True)
        if session is None:
            raise SessionNotFound(f"session {session_id} not found")
        if session.status is SessionStatus.ACTIVE:
            current = await self.rounds.get_current_round(session_id)
            if current is not None and current.result is RoundResult.PENDING:
                await self.rounds.save_result(current, RoundResult.ABANDONED)
            await self.sessions.update_status(session, SessionStatus.COMPLETED)
        await self.db.commit()
        return session

    async def _submit(
        self, session_id: uuid.UUID, response_id: uuid.UUID, transcript: str, round_number: int | None
    ) -> SubmitOutcome:
        # Lock the session row first so concurrent submissions are processed one at a time.
        session = await self.sessions.get_by_id(session_id, for_update=True)
        if session is None:
            raise SessionNotFound(f"session {session_id} not found")

        existing = await self.responses.get_by_id(response_id)
        if existing is not None:
            await self.db.commit()  # release the lock
            return await self._replay(session_id, existing)

        if session.status is not SessionStatus.ACTIVE:
            raise GameNotActive(f"game is {session.status.value}")

        current = await self.rounds.get_current_round(session_id)
        if current is None or current.result is not RoundResult.PENDING:
            raise GameNotActive("no round is waiting for an answer")
        if round_number is not None and round_number != current.round_number:
            raise StaleRound(f"round {round_number} is not the current round ({current.round_number})")

        is_correct = evaluate_response(current.sequence, transcript)
        await self.responses.create(response_id, current.id, transcript, is_correct)

        points = 0
        next_round: Round | None = None
        if is_correct:
            points = points_for_round(current.round_number, self.settings.points_per_item)
            await self.rounds.save_result(current, RoundResult.CORRECT)
            if current.round_number >= self.settings.max_rounds:
                await self.sessions.update_score(session, score=session.score + points, current_round=current.round_number)
                await self.sessions.update_status(session, SessionStatus.COMPLETED)
            else:
                next_number = current.round_number + 1
                next_round = await self.rounds.create(session_id, next_number, generate_sequence(next_number, self.rng))
                await self.sessions.update_score(session, score=session.score + points, current_round=next_number)
        else:
            await self.rounds.save_result(current, RoundResult.WRONG)
            await self.sessions.update_status(session, SessionStatus.FAILED)

        await self.db.commit()
        return SubmitOutcome(
            response_id=response_id,
            round_number=current.round_number,
            is_correct=is_correct,
            expected=list(current.sequence),
            points_awarded=points,
            session=session,
            next_round=next_round,
            duplicate=False,
        )

    async def _replay(self, session_id: uuid.UUID, response: Response) -> SubmitOutcome:
        round_ = await self.rounds.get_by_id(response.round_id)
        if round_ is None or round_.session_id != session_id:
            raise StaleRound("response id belongs to a different game")
        session, current = await self.get_game(session_id)
        points = points_for_round(round_.round_number, self.settings.points_per_item) if response.is_correct else 0
        return SubmitOutcome(
            response_id=response.id,
            round_number=round_.round_number,
            is_correct=response.is_correct,
            expected=list(round_.sequence),
            points_awarded=points,
            session=session,
            next_round=current if current is not None and current.result is RoundResult.PENDING else None,
            duplicate=True,
        )
