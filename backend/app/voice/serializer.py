"""Wire format between the browser and the Pipecat websocket transport.

Binary messages are raw 16-bit little-endian mono PCM: 16 kHz from the
browser, `PipelineParams.audio_out_sample_rate` towards it. Text messages
are JSON control events, which keeps the browser side to a few lines of
vanilla JavaScript without a protobuf dependency.
"""

import json

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000


class BrowserAudioSerializer(FrameSerializer):
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, InterruptionFrame):
            # Tell the browser to drop audio it has buffered but not played yet.
            return json.dumps({"type": "interrupt"})
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, sample_rate=INPUT_SAMPLE_RATE, num_channels=1)
        try:
            return InputTransportMessageFrame(message=json.loads(data))
        except json.JSONDecodeError:
            return None
