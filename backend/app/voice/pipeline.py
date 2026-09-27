"""Pipecat pipeline for one voice game session.

    browser mic ─► websocket ─► VAD ─► Deepgram STT ─► user-turn detection
                                                              │
    browser speaker ◄─ websocket ◄─ Deepgram TTS ◄─ MemoryGameProcessor
"""

import logging

from fastapi import WebSocket
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from app.core.config import Settings
from app.services.game_service import CARDS
from app.voice.bot import GameGateway, MemoryGameProcessor, StartGameFrame
from app.voice.host import HostLLM
from app.voice.serializer import INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, BrowserAudioSerializer

logger = logging.getLogger(__name__)


def build_user_turns(settings: Settings) -> UserTurnProcessor:
    return UserTurnProcessor(
        user_turn_strategies=UserTurnStrategies(
            # The player can barge in: bot audio stops as soon as they start talking.
            start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
            # Players pause between words while recalling; give them time before
            # treating silence as the end of their answer.
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=settings.voice_turn_timeout)],
        ),
    )


async def run_voice_session(websocket: WebSocket, gateway: GameGateway, settings: Settings) -> None:
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=BrowserAudioSerializer(),
            session_timeout=settings.voice_session_timeout,
        ),
    )
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.4)))
    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model=settings.deepgram_stt_model,
            language=Language.EN,
            interim_results=True,
            punctuate=True,
            # Bias recognition towards the words that can appear in a sequence.
            keyterm=list(CARDS),
        ),
    )
    tts = DeepgramTTSService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramTTSService.Settings(voice=settings.deepgram_tts_voice),
    )
    game = MemoryGameProcessor(gateway, HostLLM(settings))

    pipeline = Pipeline([transport.input(), vad, stt, build_user_turns(settings), game, tts, transport.output()])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=INPUT_SAMPLE_RATE, audio_out_sample_rate=OUTPUT_SAMPLE_RATE),
        idle_timeout_secs=settings.voice_session_timeout,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _websocket) -> None:
        await task.queue_frame(StartGameFrame())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _websocket) -> None:
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)
