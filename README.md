# Memory Cards: a voice memory game on Pipecat

The bot reads out a sequence of words and you say them back in the same order. Each correct round adds one
more word. One wrong answer ends the game.

- **Backend:** FastAPI, SQLAlchemy 2 (async) + Alembic on PostgreSQL, Valkey/Redis cache, Pydantic v2
- **Voice:** Pipecat with Silero VAD, Deepgram STT (nova-3) and Deepgram TTS (Aura-2)
- **Host personality:** Gemini through LiteLLM
- **UI:** server-rendered HTML with HTMX, plus one small vanilla-JS file for the microphone and speaker
- **Deploy:** Render (one web service, PostgreSQL, Key Value)

## Architecture

```text
Browser (HTML + HTMX + voice.js)
   │  HTTP (JSON API and HTML partials)            │  WebSocket (PCM audio + JSON events)
   ▼                                                ▼
FastAPI routers (api/)                        Pipecat pipeline (voice/)
   │                                            VAD → Deepgram STT → user-turn detection
   │                                              → MemoryGameProcessor → Deepgram TTS
   │                                                │                 │
   ▼                                                ▼                 ▼
SessionService / LeaderboardService ◄──────── GameGateway        HostLLM (Gemini via LiteLLM)
   │           │                                                  reactions only, never facts
   ▼           ▼
GameService   GameCache (Valkey)          game:{id} snapshots, leaderboard
   │
Repositories → PostgreSQL (source of truth)
```

The core rule: **Pipecat runs the conversation, and the game service owns game state and correctness.**

| Layer | Owns | Never does |
|---|---|---|
| `services/game_service.py` | Sequence generation, difficulty, validation, scoring, round progression, game over. Each public method is one DB transaction. | Talk to the cache or voice layer |
| `services/session_service.py` | Coordinating PostgreSQL writes with cache refreshes. Cache-first reads with DB fallback. | Make game-rule decisions |
| `repositories/` | All SQLAlchemy queries. They only flush. | Commit transactions |
| `voice/bot.py` | Turn-taking: when to speak, when to listen, which user turn counts as an answer | Decide if an answer is correct |
| `voice/host.py` | One short LLM reaction line per event | See sequences, scores or answers |

### How a round works

1. The bot says: `<Gemini reaction> Round 3. Your 4 words are: tiger, rocket, violin, lemon. Your turn.`
   The reaction comes from the LLM. Everything after it comes from a deterministic template in `voice/prompts.py`.
2. The browser reports `playback_done` once it has actually finished playing that audio. Only then does the
   bot start listening (`PRESENTING → LISTENING`).
3. Deepgram transcripts are collected until the user turn ends: Silero VAD sees 0.4 s of silence and then a
   further `VOICE_TURN_TIMEOUT` passes (1.2 s by default). This lets players pause between words.
4. The full answer goes to `GameService.submit_response` with a freshly generated `response_id`.
5. `evaluate_response` (a pure function) compares the answer to the sequence. It lowercases, strips
   punctuation, drops fillers such as "um", "and" and "the", and allows plurals and near-misses (similarity
   ≥ 0.8, for words of at least 4 letters). A unit test checks that no two cards fuzzy-match each other.
   Order, missing words and extra words are strict.
6. A correct answer adds `10 × words` points and creates the next round (one more word). A wrong answer
   marks the session `FAILED`. Clearing `MAX_ROUNDS` (default 10) marks it `COMPLETED`.

Saying "repeat" re-reads the sequence without scoring anything.

### Interruptions

Barge-in is enabled. When the player starts talking, Pipecat broadcasts an interruption: Deepgram TTS stops,
and the browser drops its queued audio when it receives `{"type": "interrupt"}`.

- **During the sequence** (`PRESENTING`): the processor moves to `INTERRUPTED`. Nothing said in that turn is
  scored, whether it was an early answer, "wait" or a cough. When the player finishes, the bot says "No
  problem, let me start that again" and re-reads the same round.
- **While listening:** the interruption is just the answer starting, so nothing changes.

Why `playback_done` exists: Pipecat's websocket transport sends audio at twice real-time, so the server's
`BotStoppedSpeakingFrame` fires while the browser still has seconds of audio queued. If that event opened the
answer window, a player talking over the end of the sequence would be scored instead of treated as an
interruption. Only the browser knows when the sequence has actually been heard.

A related detail: `UserStoppedSpeakingFrame` is a system frame, so it can overtake transcripts still queued
in the processor. The processor re-queues the turn end as a data frame so every transcript of the turn is
collected before evaluation.

### Preventing double scoring

The guarantee comes from the database, not from application code or the cache:

- `responses.id` is the **caller-supplied `response_id`** (primary key), so it acts as an idempotency key.
- `responses.round_id` is **unique**, so a round can be scored by at most one response.
- `rounds (session_id, round_number)` is **unique**, so the next round cannot be created twice.
- `submit_response` locks the session row (`SELECT … FOR UPDATE`) and then checks for an existing response.
  Resubmitting a `response_id` returns the stored outcome with `duplicate: true` and changes nothing.
  An `IntegrityError` from a lost race is caught and reported the same way.
- The voice gateway retries transient DB errors with the **same** `response_id`, so a retry after a commit
  whose acknowledgement was lost cannot score twice.

Tests cover sequential replays, 5 concurrent submissions of one response, and 5 concurrent different answers
to one round.

### Caching

- `game:{session_id}` holds a `GameState` snapshot (status, round, score, pending sequence) with a TTL
  (`GAME_STATE_TTL`, default 1 h). It is written after every commit, and UI polling reads it first.
- `leaderboard:{limit}` has a short TTL and is invalidated whenever a game finishes.
- Every cache error is logged and treated as a miss, so a Valkey outage slows the app down but does not
  break it. PostgreSQL is always authoritative.

### What the LLM does

`HostLLM` asks Gemini for one line of at most 15 words reacting to an event: greeting, correct, wrong,
completed, repeat or interrupted. It is only given the player's name and the event. Replies are streamed
(`LLM_STREAMING`) but collected in full and checked before they are spoken. The bot uses a canned line
instead if the LLM:

- mentions any card word,
- runs too long,
- errors,
- or takes longer than `HOST_LINE_TIMEOUT` (4 s).

After a failure, the bot skips the LLM for 60 s so players don't wait out the timeout on every line.

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/sessions` | `{"player_name": "Ada"}` → 201 with the session. The pending sequence is never exposed. |
| `GET` | `/sessions/{id}` | Cache-backed state. Add `?include_rounds=true` for the round history. |
| `POST` | `/sessions/{id}/responses` | `{"response_id": uuid, "transcript": "...", "round_number": 3?}`. Idempotent per `response_id`. 409 if the game is over or the round is stale. |
| `POST` | `/sessions/{id}/end` | Ends the game (idempotent). A pending round becomes `ABANDONED`. |
| `GET` | `/scores/recent?limit=10` | Finished games, newest first |
| `GET` | `/leaderboard?limit=10` | Ranked by score, including rounds cleared |
| `WS` | `/ws/sessions/{id}` | Voice. Binary frames are 16-bit mono PCM (16 kHz up, 24 kHz down). Text frames are JSON events. |
| `GET` | `/healthz` | Health check |

Interactive docs are at `/docs`.

## Running locally

Prerequisites: Python 3.13, [uv](https://docs.astral.sh/uv/), PostgreSQL and Redis or Valkey.

```bash
cp .env.example .env              # add DEEPGRAM_API_KEY and the LLM settings
uv sync
createdb memorycard
cd backend
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000, start a game and press **Start talking**. Browsers only allow microphone access on
`localhost` or HTTPS. Headphones stop the bot from hearing (and interrupting) itself.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://localhost:5432/memorycard` | `postgres://` and `postgresql://` URLs are converted to asyncpg automatically |
| `REDIS_URL` | `redis://localhost:6379/0` | Valkey or Redis |
| `DEEPGRAM_API_KEY` | — | STT and TTS |
| `DEEPGRAM_STT_MODEL` / `DEEPGRAM_TTS_VOICE` | `nova-3` / `aura-2-thalia-en` | |
| `LLM_MODEL` | `vertex_ai/gemini-2.5-flash` | Any LiteLLM model string |
| `LLM_TEMPERATURE`, `LLM_TIMEOUT`, `LLM_NUM_RETRIES`, `LLM_STREAMING` | `0.2`, `30`, `2`, `true` | |
| `LLM_EXTRA_PARAMS` | `{}` | JSON passed through to LiteLLM, e.g. `{"vertex_project": "p", "vertex_location": "asia-south1"}` |
| `VERTEX_CREDENTIALS` | — | JSON contents of, or a path to, a Google credentials file (`authorized_user` or service account). If unset, Application Default Credentials are used. |
| `GEMINI_API_KEY` | — | Only for AI Studio models (`gemini/...`) |
| `HOST_LINE_TIMEOUT` | `4` | Latency budget for one LLM line |
| `VOICE_TURN_TIMEOUT` | `1.2` | Silence after speech before an answer is final |
| `MAX_ROUNDS`, `POINTS_PER_ITEM` | `10`, `10` | Game tuning |
| `GAME_STATE_TTL`, `LEADERBOARD_TTL` | `3600`, `30` | Cache TTLs in seconds |

## Tests

```bash
createdb memorycard_test            # once
uv run pytest
```

The service and API tests use a real PostgreSQL database (`TEST_DATABASE_URL`, default
`postgresql+asyncpg://localhost:5432/memorycard_test`), because the double-scoring guarantees depend on its
constraints and row locks. Redis is replaced by fakeredis. There are 101 tests:

- `unit/test_sequence_validation.py`: exact match, wrong word, wrong order, missing and extra words, case,
  punctuation, fillers, plurals, typos, card list distinctness, difficulty and points
- `unit/test_game_service.py`: progression, game over, completion, stale rounds, idempotent replay,
  concurrent duplicates
- `unit/test_session_service.py`: cache hit, miss and repopulate, refresh after scoring, leaderboard
  invalidation, cache outage
- `unit/test_voice_bot.py`: turn-taking through a Pipecat test pipeline (listening window, repeat,
  interruption replay, transcripts never reaching TTS, game over)
- `unit/test_host.py`: LiteLLM parameters, streaming, fallbacks, timeout, card-word filter, cooldown
- `integration/`: the HTTP API, the scores and leaderboard endpoints, and the HTML/HTMX routes

## Deploying to Render

`render.yaml` is a Blueprint. In Render, choose **New → Blueprint** and point it at this repository. It
creates:

- `memory-cards`: a Docker web service. It runs `alembic upgrade head` and then uvicorn.
- `memory-cards-db`: PostgreSQL, wired to `DATABASE_URL`.
- `memory-cards-cache`: Key Value (Valkey), internal only, wired to `REDIS_URL`.

You will be asked for `DEEPGRAM_API_KEY`, `LLM_EXTRA_PARAMS` and `VERTEX_CREDENTIALS` (paste the JSON). All
plans are set to `free`. Free web services sleep when idle, so use a paid plan for a smooth demo.

## Design notes and deviations

- **Websocket transport instead of WebRTC.** Render web services only accept HTTP(S), not inbound UDP, so
  Pipecat's SmallWebRTC transport would need an extra TURN service. Pipecat's FastAPI websocket transport with
  a small custom serializer (`voice/serializer.py`) works on Render as-is and keeps the browser side to
  vanilla JS with no protobuf or npm. Echo cancellation comes from the browser's `getUserMedia` constraints.
- **uv with `pyproject.toml` and `uv.lock`** replace `requirements.txt`. The Docker build installs from the
  lock file.
- **Added endpoint:** `POST /sessions/{id}/responses` exposes answer submission over HTTP. It makes the game
  playable and testable without voice and demonstrates the idempotency guarantee.
- **Added column:** `player_name` on `sessions`, for the leaderboard. There is still no users table.
- **Card list:** 40 fixed nouns chosen to be easy to pronounce and hard to confuse. They are also sent to
  Deepgram as key terms.

## Demo script

1. Start a game, press **Start talking**, and answer round 1 correctly. The round and score update live.
2. In round 2, talk over the bot while it is reading the words. It stops, says it will start again, and
   re-reads round 2. The score and round do not change.
3. Say "repeat". The words are re-read and nothing is scored.
4. Double-scoring check: resubmit the same `response_id` through the API. The result has `duplicate: true`
   and the score is unchanged:
   ```bash
   curl -X POST localhost:8000/sessions/$SID/responses -H 'content-type: application/json' \
        -d '{"response_id": "6f1c…", "transcript": "…"}'   # run it twice
   ```
5. Answer a round wrong. The bot reveals the words, the panel shows the round history, and the game appears
   on the leaderboard.
