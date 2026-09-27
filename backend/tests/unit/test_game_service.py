"""GameService tests. These run against the Postgres test database because the
double-scoring guarantees are enforced by database constraints and row locks."""

import asyncio
import uuid

import pytest

from app.models.game import RoundResult, SessionStatus
from app.repositories.round_repository import RoundRepository
from app.services.game_service import GameNotActive, GameService, SessionNotFound, StaleRound


@pytest.fixture
def service(db, settings, rng) -> GameService:
    return GameService(db, settings, rng)


async def answer(service: GameService, session_id, *, correct: bool, response_id=None, round_number=None):
    _, current = await service.get_game(session_id)
    transcript = " ".join(current.sequence) if correct else "definitely wrong"
    return await service.submit_response(session_id, response_id or uuid.uuid4(), transcript, round_number)


async def test_start_game_creates_active_session_with_first_round(service):
    session, first = await service.start_game("Ada")

    assert session.status is SessionStatus.ACTIVE
    assert session.score == 0
    assert session.current_round == 1
    assert first.round_number == 1
    assert len(first.sequence) == 2
    assert first.result is RoundResult.PENDING


async def test_correct_answer_scores_and_advances(service):
    session, _ = await service.start_game("Ada")

    outcome = await answer(service, session.id, correct=True)

    assert outcome.is_correct
    assert outcome.points_awarded == 20
    assert outcome.session.score == 20
    assert outcome.session.current_round == 2
    assert outcome.next_round is not None
    assert len(outcome.next_round.sequence) == 3
    assert not outcome.game_over


async def test_wrong_answer_ends_game(service):
    session, _ = await service.start_game("Ada")
    await answer(service, session.id, correct=True)

    outcome = await answer(service, session.id, correct=False)

    assert not outcome.is_correct
    assert outcome.points_awarded == 0
    assert outcome.session.status is SessionStatus.FAILED
    assert outcome.session.score == 20
    assert outcome.session.ended_at is not None
    assert outcome.next_round is None
    assert outcome.game_over


async def test_clearing_final_round_completes_game(service, settings):
    session, _ = await service.start_game("Ada")
    for _ in range(settings.max_rounds):
        outcome = await answer(service, session.id, correct=True)

    assert outcome.session.status is SessionStatus.COMPLETED
    assert outcome.session.score == 20 + 30 + 40
    assert outcome.session.current_round == settings.max_rounds
    assert outcome.next_round is None


async def test_answer_after_game_over_is_rejected(service):
    session, _ = await service.start_game("Ada")
    await answer(service, session.id, correct=False)

    with pytest.raises(GameNotActive):
        await service.submit_response(session.id, uuid.uuid4(), "apple banana")


async def test_unknown_session(service):
    with pytest.raises(SessionNotFound):
        await service.submit_response(uuid.uuid4(), uuid.uuid4(), "apple")


async def test_answer_for_stale_round_is_rejected(service):
    session, _ = await service.start_game("Ada")
    await answer(service, session.id, correct=True)

    with pytest.raises(StaleRound):
        await answer(service, session.id, correct=True, round_number=1)


class TestDoubleScoring:
    async def test_replaying_same_response_id_does_not_score_twice(self, service):
        session, _ = await service.start_game("Ada")
        response_id = uuid.uuid4()
        _, first_round = await service.get_game(session.id)
        transcript = " ".join(first_round.sequence)

        first = await service.submit_response(session.id, response_id, transcript)
        replay = await service.submit_response(session.id, response_id, transcript)

        assert not first.duplicate
        assert replay.duplicate
        assert replay.is_correct and replay.round_number == 1
        assert replay.session.score == 20
        assert replay.session.current_round == 2

    async def test_replaying_a_wrong_answer_after_game_over_returns_original_outcome(self, service):
        session, _ = await service.start_game("Ada")
        response_id = uuid.uuid4()

        await service.submit_response(session.id, response_id, "nope")
        replay = await service.submit_response(session.id, response_id, "nope")

        assert replay.duplicate
        assert not replay.is_correct
        assert replay.session.status is SessionStatus.FAILED

    async def test_concurrent_duplicates_are_scored_once(self, service, session_factory, settings, rng):
        session, first_round = await service.start_game("Ada")
        transcript = " ".join(first_round.sequence)
        response_id = uuid.uuid4()

        async def submit():
            async with session_factory() as db:
                return await GameService(db, settings, rng).submit_response(session.id, response_id, transcript)

        outcomes = await asyncio.gather(*(submit() for _ in range(5)))

        assert sum(not o.duplicate for o in outcomes) == 1
        assert {o.session.score for o in outcomes} == {20}
        async with session_factory() as db:
            rounds = await RoundRepository(db).list_for_session(session.id)
        assert [r.round_number for r in rounds] == [1, 2]

    async def test_concurrent_different_answers_for_one_round_score_once(self, service, session_factory, settings, rng):
        session, first_round = await service.start_game("Ada")
        transcript = " ".join(first_round.sequence)

        async def submit():
            async with session_factory() as db:
                try:
                    return await GameService(db, settings, rng).submit_response(
                        session.id, uuid.uuid4(), transcript, round_number=1
                    )
                except StaleRound:
                    return None

        outcomes = await asyncio.gather(*(submit() for _ in range(5)))

        assert sum(o is not None for o in outcomes) == 1
        _, current = await service.get_game(session.id)
        async with session_factory() as db:
            fresh, _ = await GameService(db, settings).get_game(session.id)
        assert fresh.score == 20
        assert current.round_number == 2


async def test_end_game_abandons_pending_round_and_is_idempotent(service):
    session, _ = await service.start_game("Ada")

    ended = await service.end_game(session.id)
    again = await service.end_game(session.id)

    assert ended.status is SessionStatus.COMPLETED
    assert again.ended_at == ended.ended_at
    _, current = await service.get_game(session.id)
    assert current.result is RoundResult.ABANDONED


async def test_end_game_does_not_change_a_failed_game(service):
    session, _ = await service.start_game("Ada")
    await answer(service, session.id, correct=False)

    ended = await service.end_game(session.id)

    assert ended.status is SessionStatus.FAILED
