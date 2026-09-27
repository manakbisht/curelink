import uuid

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache.redis import GameCache, game_key
from app.models.game import SessionStatus
from app.services.game_service import GameService
from app.services.session_service import SessionService


@pytest.fixture
def sessions(db, settings, rng, cache) -> SessionService:
    return SessionService(GameService(db, settings, rng), cache)


async def test_start_session_caches_active_state(sessions, redis, settings):
    state = await sessions.start_session("Ada")

    raw = await redis.get(game_key(state.session_id))
    assert raw is not None
    assert 0 < await redis.ttl(game_key(state.session_id)) <= settings.game_state_ttl
    assert state.status is SessionStatus.ACTIVE
    assert len(state.sequence) == 2


async def test_get_state_is_served_from_cache(sessions, redis):
    state = await sessions.start_session("Ada")
    tampered = state.model_copy(update={"score": 999})
    await redis.set(game_key(state.session_id), tampered.model_dump_json())

    assert (await sessions.get_state(state.session_id)).score == 999


async def test_get_state_falls_back_to_database_and_repopulates(sessions, redis):
    state = await sessions.start_session("Ada")
    await redis.delete(game_key(state.session_id))

    loaded = await sessions.get_state(state.session_id)

    assert loaded == state
    assert await redis.exists(game_key(state.session_id))


async def test_submit_refreshes_cached_state(sessions):
    state = await sessions.start_session("Ada")

    result = await sessions.submit_response(state.session_id, uuid.uuid4(), " ".join(state.sequence))

    cached = await sessions.get_state(state.session_id)
    assert result.is_correct and result.next_round == 2
    assert cached.score == 20
    assert cached.current_round == 2
    assert len(cached.sequence) == 3


async def test_game_over_clears_sequence_and_leaderboard_cache(sessions, redis):
    state = await sessions.start_session("Ada")
    await redis.set("leaderboard:10", "[]")

    await sessions.submit_response(state.session_id, uuid.uuid4(), "wrong")

    cached = await sessions.get_state(state.session_id)
    assert cached.status is SessionStatus.FAILED
    assert cached.sequence is None
    assert not await redis.exists("leaderboard:10")


async def test_public_session_view_hides_pending_sequence(sessions):
    state = await sessions.start_session("Ada")
    await sessions.submit_response(state.session_id, uuid.uuid4(), " ".join(state.sequence))

    view = await sessions.get_session(state.session_id, include_rounds=True)

    assert view.sequence_length == 3
    assert [r.round_number for r in view.rounds] == [1, 2]
    assert view.rounds[0].sequence == state.sequence
    assert view.rounds[0].transcript == " ".join(state.sequence)
    assert view.rounds[1].sequence is None


class BrokenRedis:
    async def get(self, *_a, **_k):
        raise RedisConnectionError("down")

    set = delete = get

    def scan_iter(self, *_a, **_k):
        raise RedisConnectionError("down")


async def test_cache_outage_falls_back_to_database(db, settings, rng):
    broken = GameCache(BrokenRedis(), game_ttl=60, leaderboard_ttl=60)
    sessions = SessionService(GameService(db, settings, rng), broken)

    state = await sessions.start_session("Ada")
    result = await sessions.submit_response(state.session_id, uuid.uuid4(), " ".join(state.sequence))
    loaded = await sessions.get_state(state.session_id)

    assert result.is_correct
    assert loaded.score == 20
