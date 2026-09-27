import re
import uuid


async def start_game(client, name="Ada"):
    resp = await client.post("/ui/sessions", data={"player_name": name})
    assert resp.status_code == 200
    session_id = re.search(r'data-session-id="([0-9a-f-]+)"', resp.text).group(1)
    return session_id, resp


async def test_index_shows_start_form(client):
    resp = await client.get("/")

    assert resp.status_code == 200
    assert 'hx-post="/ui/sessions"' in resp.text
    assert "htmx.org@2.0.4" in resp.text


async def test_starting_a_game_renders_panel_and_pushes_url(client):
    session_id, resp = await start_game(client)

    assert resp.headers["HX-Push-Url"] == f"/?session={session_id}"
    assert 'data-status="ACTIVE"' in resp.text
    assert resp.text.count('class="card-back"') == 2
    assert 'id="voice-connect"' in resp.text


async def test_blank_name_becomes_anonymous(client):
    _, resp = await start_game(client, name="   ")
    assert "<dd>Anonymous</dd>" in resp.text


async def test_index_resumes_existing_session(client):
    session_id, _ = await start_game(client)

    resp = await client.get("/", params={"session": session_id})

    assert f'data-session-id="{session_id}"' in resp.text


async def test_index_with_unknown_session_falls_back_to_start_form(client):
    resp = await client.get("/", params={"session": str(uuid.uuid4())})
    assert 'hx-post="/ui/sessions"' in resp.text


async def test_active_state_partial_keeps_polling_and_hides_sequence(client, session_factory):
    session_id, _ = await start_game(client)

    resp = await client.get(f"/ui/sessions/{session_id}")

    assert 'hx-trigger="every 3s, refresh"' in resp.text
    assert "HX-Trigger" not in resp.headers
    async with session_factory() as db:
        from app.repositories.round_repository import RoundRepository

        sequence = (await RoundRepository(db).get_current_round(uuid.UUID(session_id))).sequence
    assert not any(word in resp.text for word in sequence)


async def test_ending_game_stops_polling_and_shows_result(client):
    session_id, _ = await start_game(client)

    resp = await client.post(f"/ui/sessions/{session_id}/end")

    assert resp.headers["HX-Trigger"] == "game-ended"
    assert 'data-status="COMPLETED"' in resp.text
    assert "hx-trigger" not in resp.text
    assert "Game ended" in resp.text
    assert "Play again" in resp.text


async def test_failed_game_shows_history(client):
    session_id, _ = await start_game(client)
    await client.post(
        f"/sessions/{session_id}/responses", json={"response_id": str(uuid.uuid4()), "transcript": "no idea"}
    )

    resp = await client.get(f"/ui/sessions/{session_id}")

    assert "Game over" in resp.text
    assert "no idea" in resp.text
    assert "badge-wrong" in resp.text


async def test_scores_partial(client):
    session_id, _ = await start_game(client, name="Grace")
    await client.post(f"/ui/sessions/{session_id}/end")

    resp = await client.get("/ui/scores")

    assert "Leaderboard" in resp.text
    assert "Grace" in resp.text


async def test_static_assets_are_served(client):
    assert (await client.get("/static/voice.js")).status_code == 200
    assert (await client.get("/static/style.css")).status_code == 200
