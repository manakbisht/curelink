import uuid

import pytest
from sqlalchemy import func, select

from app.models.game import GameSession, Response, Round


async def start(client, name="Ada") -> dict:
    resp = await client.post("/sessions", json={"player_name": name})
    assert resp.status_code == 201
    return resp.json()


async def current_sequence(session_factory, session_id: str) -> list[str]:
    """Tests read the pending sequence straight from the DB; the API deliberately hides it."""
    async with session_factory() as db:
        stmt = select(Round).where(Round.session_id == uuid.UUID(session_id)).order_by(Round.round_number.desc())
        return (await db.execute(stmt)).scalars().first().sequence


async def submit(client, session_id, transcript, response_id=None, **extra):
    body = {"response_id": str(response_id or uuid.uuid4()), "transcript": transcript, **extra}
    return await client.post(f"/sessions/{session_id}/responses", json=body)


async def test_create_session(client):
    body = await start(client)

    assert body["status"] == "ACTIVE"
    assert body["score"] == 0
    assert body["current_round"] == 1
    assert body["sequence_length"] == 2
    assert "sequence" not in body


async def test_create_session_defaults_player_name(client):
    resp = await client.post("/sessions", json={})
    assert resp.json()["player_name"] == "Anonymous"


@pytest.mark.parametrize("name", ["", "x" * 41])
async def test_create_session_validates_player_name(client, name):
    resp = await client.post("/sessions", json={"player_name": name})
    assert resp.status_code == 422


async def test_get_session(client):
    created = await start(client)

    resp = await client.get(f"/sessions/{created['session_id']}")

    assert resp.status_code == 200
    assert resp.json() == created


async def test_get_unknown_session_is_404(client):
    resp = await client.get(f"/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"] == "SessionNotFound"


async def test_get_session_with_invalid_id_is_422(client):
    assert (await client.get("/sessions/not-a-uuid")).status_code == 422


async def test_correct_answer_advances_game(client, session_factory):
    session = await start(client)
    sequence = await current_sequence(session_factory, session["session_id"])

    resp = await submit(client, session["session_id"], ", ".join(sequence).upper())

    assert resp.status_code == 200
    result = resp.json()
    assert result["is_correct"] is True
    assert result["expected"] == sequence
    assert result["points_awarded"] == 20
    assert result["score"] == 20
    assert result["next_round"] == 2

    state = (await client.get(f"/sessions/{session['session_id']}")).json()
    assert state["current_round"] == 2
    assert state["sequence_length"] == 3


async def test_wrong_answer_ends_game(client):
    session = await start(client)

    result = (await submit(client, session["session_id"], "no idea")).json()

    assert result["is_correct"] is False
    assert result["status"] == "FAILED"
    assert result["next_round"] is None
    later = await submit(client, session["session_id"], "too late")
    assert later.status_code == 409
    assert later.json()["error"] == "GameNotActive"


async def test_resubmitting_same_response_is_not_double_scored(client, session_factory):
    session = await start(client)
    sid = session["session_id"]
    sequence = await current_sequence(session_factory, sid)
    response_id = uuid.uuid4()

    first = (await submit(client, sid, " ".join(sequence), response_id)).json()
    second = (await submit(client, sid, " ".join(sequence), response_id)).json()

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["score"] == first["score"] == 20
    state = (await client.get(f"/sessions/{sid}")).json()
    assert state["score"] == 20 and state["current_round"] == 2
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Response)) == 1
        assert (await db.get(GameSession, uuid.UUID(sid))).score == 20


async def test_answer_for_previous_round_is_rejected(client, session_factory):
    session = await start(client)
    sid = session["session_id"]
    sequence = await current_sequence(session_factory, sid)
    await submit(client, sid, " ".join(sequence), round_number=1)

    resp = await submit(client, sid, " ".join(sequence), round_number=1)

    assert resp.status_code == 409
    assert resp.json()["error"] == "StaleRound"


async def test_end_session(client, session_factory):
    session = await start(client)
    sid = session["session_id"]
    await submit(client, sid, " ".join(await current_sequence(session_factory, sid)))

    resp = await client.post(f"/sessions/{sid}/end")

    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "COMPLETED"
    assert body["score"] == 20
    assert body["ended_at"] is not None
    assert [(r["round_number"], r["result"]) for r in body["rounds"]] == [(1, "CORRECT"), (2, "ABANDONED")]
    # Once the game is over every sequence can be revealed.
    assert all(r["sequence"] for r in body["rounds"])
    assert (await client.post(f"/sessions/{sid}/end")).json()["ended_at"] == body["ended_at"]


async def test_end_unknown_session_is_404(client):
    assert (await client.post(f"/sessions/{uuid.uuid4()}/end")).status_code == 404
