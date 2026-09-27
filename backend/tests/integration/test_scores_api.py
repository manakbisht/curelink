import uuid

from sqlalchemy import select

from app.models.game import Round


async def play(client, session_factory, name: str, rounds_to_clear: int) -> str:
    """Clear `rounds_to_clear` rounds, then answer wrong (or stop if the game completes)."""
    sid = (await client.post("/sessions", json={"player_name": name})).json()["session_id"]
    for _ in range(rounds_to_clear):
        async with session_factory() as db:
            stmt = select(Round).where(Round.session_id == uuid.UUID(sid)).order_by(Round.round_number.desc())
            sequence = (await db.execute(stmt)).scalars().first().sequence
        result = await client.post(
            f"/sessions/{sid}/responses", json={"response_id": str(uuid.uuid4()), "transcript": " ".join(sequence)}
        )
        if result.json()["status"] != "ACTIVE":
            return sid
    await client.post(f"/sessions/{sid}/responses", json={"response_id": str(uuid.uuid4()), "transcript": "wrong"})
    return sid


async def test_recent_scores_lists_finished_games_newest_first(client, session_factory):
    await play(client, session_factory, "first", 1)
    await play(client, session_factory, "second", 0)
    await client.post("/sessions", json={"player_name": "still-playing"})

    body = (await client.get("/scores/recent")).json()

    assert [s["player_name"] for s in body] == ["second", "first"]
    assert body[1] | {"session_id": None, "ended_at": None} == {
        "session_id": None,
        "player_name": "first",
        "score": 20,
        "rounds_cleared": 1,
        "status": "FAILED",
        "ended_at": None,
    }


async def test_leaderboard_ranks_by_score(client, session_factory, settings):
    await play(client, session_factory, "low", 1)
    await play(client, session_factory, "champion", settings.max_rounds)
    await play(client, session_factory, "mid", 2)

    body = (await client.get("/leaderboard")).json()

    assert [(e["rank"], e["player_name"], e["score"], e["rounds_cleared"]) for e in body] == [
        (1, "champion", 90, 3),
        (2, "mid", 50, 2),
        (3, "low", 20, 1),
    ]
    assert body[0]["status"] == "COMPLETED"


async def test_leaderboard_respects_limit(client, session_factory):
    for i in range(3):
        await play(client, session_factory, f"p{i}", 0)

    assert len((await client.get("/leaderboard", params={"limit": 2})).json()) == 2
    assert (await client.get("/leaderboard", params={"limit": 0})).status_code == 422


async def test_leaderboard_is_cached_and_invalidated_when_a_game_ends(client, session_factory, redis):
    await play(client, session_factory, "first", 1)
    assert len((await client.get("/leaderboard")).json()) == 1
    assert await redis.exists("leaderboard:10")

    await play(client, session_factory, "second", 2)

    body = (await client.get("/leaderboard")).json()
    assert [e["player_name"] for e in body] == ["second", "first"]


async def test_empty_scores(client):
    assert (await client.get("/scores/recent")).json() == []
    assert (await client.get("/leaderboard")).json() == []
