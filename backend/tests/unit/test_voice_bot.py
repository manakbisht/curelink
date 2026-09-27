"""Turn-taking tests for the voice game processor, driven through a Pipecat test pipeline.

The game backend is replaced by an in-memory gateway that applies the real
`evaluate_response` rules, so these tests focus on *when* the bot listens,
speaks and submits answers.
"""

import uuid
from datetime import UTC, datetime

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.models.game import SessionStatus
from app.schemas.round import ResponseResult
from app.schemas.session import GameState
from app.services.game_service import evaluate_response
from app.voice.bot import MemoryGameProcessor, Phase, StartGameFrame

SEQUENCES = {1: ["apple", "banana"], 2: ["tiger", "rocket", "violin"]}


class FakeGateway:
    def __init__(self) -> None:
        self.state = GameState(
            session_id=uuid.uuid4(),
            player_name="Ada",
            status=SessionStatus.ACTIVE,
            score=0,
            current_round=1,
            sequence=SEQUENCES[1],
            started_at=datetime.now(UTC),
            ended_at=None,
        )
        self.submissions: list[tuple[uuid.UUID, str, int]] = []

    async def get_state(self) -> GameState:
        return self.state

    async def submit(self, response_id: uuid.UUID, transcript: str, round_number: int) -> ResponseResult:
        self.submissions.append((response_id, transcript, round_number))
        expected = self.state.sequence
        correct = evaluate_response(expected, transcript)
        if correct:
            nxt = round_number + 1
            self.state = self.state.model_copy(
                update={"score": self.state.score + 20, "current_round": nxt, "sequence": SEQUENCES[nxt]}
            )
        else:
            self.state = self.state.model_copy(update={"status": SessionStatus.FAILED, "sequence": None})
        return ResponseResult(
            response_id=response_id,
            round_number=round_number,
            is_correct=correct,
            expected=expected,
            points_awarded=20 if correct else 0,
            score=self.state.score,
            status=self.state.status.value,
            next_round=self.state.current_round if correct else None,
        )


def transcript(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="player", timestamp=datetime.now(UTC).isoformat())


def user_says(text: str) -> list:
    return [UserStartedSpeakingFrame(), transcript(text), UserStoppedSpeakingFrame(), SleepFrame(0.05)]


def spoken(frames) -> list[str]:
    return [f.text for f in frames if isinstance(f, TTSSpeakFrame)]


async def play(frames: list, gateway: FakeGateway | None = None) -> tuple[MemoryGameProcessor, FakeGateway, list[str], list]:
    gateway = gateway or FakeGateway()
    processor = MemoryGameProcessor(gateway)
    down, _ = await run_test(processor, frames_to_send=[StartGameFrame(), SleepFrame(0.05), *frames])
    return processor, gateway, spoken(down), down


async def test_greets_player_and_reads_first_sequence():
    processor, _, said, _ = await play([])

    assert len(said) == 1
    assert said[0].startswith("Hi Ada")
    assert "Round 1. Your 2 words are: apple, banana." in said[0]
    assert processor.phase is Phase.PRESENTING


async def test_answer_after_bot_finishes_is_submitted_and_next_round_read():
    processor, gateway, said, _ = await play([BotStoppedSpeakingFrame(), *user_says("Apple, banana.")])

    assert [(t, r) for _, t, r in gateway.submissions] == [("Apple, banana.", 1)]
    assert said[-1] == "Correct! That's 20 more points. Round 2. Your 3 words are: tiger, rocket, violin. Your turn."
    assert processor.phase is Phase.PRESENTING


async def test_transcripts_are_never_sent_to_tts():
    _, _, _, down = await play([BotStoppedSpeakingFrame(), *user_says("apple banana")])

    assert not any(isinstance(f, TranscriptionFrame) for f in down)


async def test_speech_while_bot_is_reading_is_not_treated_as_an_answer():
    processor, gateway, _, _ = await play([*user_says("apple"), BotStoppedSpeakingFrame(), SleepFrame(0.05)])

    assert gateway.submissions == []
    assert processor.phase is Phase.LISTENING


async def test_empty_turn_is_ignored():
    _, gateway, _, _ = await play(
        [BotStoppedSpeakingFrame(), UserStartedSpeakingFrame(), UserStoppedSpeakingFrame(), SleepFrame(0.05)]
    )

    assert gateway.submissions == []


async def test_repeat_request_rereads_sequence_without_scoring():
    processor, gateway, said, _ = await play([BotStoppedSpeakingFrame(), *user_says("Repeat, please?")])

    assert gateway.submissions == []
    assert said[-1] == "Sure, here they are again. Round 1. Your 2 words are: apple, banana. Your turn."
    assert processor.phase is Phase.PRESENTING


async def test_wrong_answer_reveals_sequence_and_finishes():
    processor, gateway, said, _ = await play([BotStoppedSpeakingFrame(), *user_says("banana apple")])

    assert len(gateway.submissions) == 1
    assert "The words were: apple, banana" in said[-1]
    assert processor.phase is Phase.FINISHED


async def test_turns_after_game_over_are_ignored():
    _, gateway, _, _ = await play(
        [BotStoppedSpeakingFrame(), *user_says("wrong"), BotStoppedSpeakingFrame(), *user_says("apple banana")]
    )

    assert len(gateway.submissions) == 1


async def test_finished_game_is_announced_instead_of_played():
    gateway = FakeGateway()
    gateway.state = gateway.state.model_copy(update={"status": SessionStatus.FAILED, "sequence": None})

    processor, _, said, _ = await play([], gateway)

    assert said == ["This game has already finished. Start a new game to play again."]
    assert processor.phase is Phase.FINISHED
