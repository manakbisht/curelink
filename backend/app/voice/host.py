"""Gemini (via LiteLLM) as the game show host.

The LLM only writes short reactions ("Nice memory, Ada!"). It is never told
the sequence and never decides anything; the game facts that follow each
reaction are spoken from deterministic templates. If the LLM is slow, fails,
or mentions a card word, the bot uses a canned line instead.
"""

import asyncio
import enum
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import litellm

from app.core.config import Settings
from app.services.game_service import CARDS
from app.voice import prompts

logger = logging.getLogger(__name__)

MAX_LINE_CHARS = 200
_CARD_PATTERN = re.compile(r"\b(" + "|".join(CARDS) + r")s?\b", re.IGNORECASE)


class HostEvent(enum.StrEnum):
    GREETING = "greeting"
    CORRECT = "correct"
    WRONG = "wrong"
    COMPLETED = "completed"
    REPEAT = "repeat"


def fallback_line(event: HostEvent, player_name: str) -> str:
    return {
        HostEvent.GREETING: f"Hi {player_name}, welcome to Memory Cards!",
        HostEvent.CORRECT: "Correct!",
        HostEvent.WRONG: "Oh no, not quite.",
        HostEvent.COMPLETED: "Incredible, you cleared every round!",
        HostEvent.REPEAT: "Sure, here they are again.",
    }[event]


def clean_line(text: str) -> str | None:
    """Make an LLM reaction safe to speak, or return None to use the fallback."""
    text = " ".join(text.replace("*", "").replace('"', "").split())
    if not text or len(text) > MAX_LINE_CHARS or _CARD_PATTERN.search(text):
        return None
    return text


class HostLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _completion_kwargs(self, event: HostEvent, player_name: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": prompts.HOST_SYSTEM_PROMPT},
                {"role": "user", "content": prompts.host_event(event.value, player_name)},
            ],
            "temperature": self.settings.llm_temperature,
            "timeout": self.settings.llm_timeout,
            "num_retries": self.settings.llm_num_retries,
            "max_tokens": 60,
            **self.settings.llm_extra_params,
        }
        if self.settings.vertex_credentials:
            kwargs["vertex_credentials"] = self.settings.vertex_credentials
        if self.settings.gemini_api_key:
            kwargs["api_key"] = self.settings.gemini_api_key
        return kwargs

    async def _generate(self, event: HostEvent, player_name: str) -> AsyncIterator[str]:
        kwargs = self._completion_kwargs(event, player_name)
        if self.settings.llm_streaming:
            response = await litellm.acompletion(stream=True, **kwargs)
            async for chunk in response:
                if delta := chunk.choices[0].delta.content:
                    yield delta
        else:
            response = await litellm.acompletion(**kwargs)
            yield response.choices[0].message.content or ""

    async def line(self, event: HostEvent, player_name: str) -> str:
        """A spoken reaction for `event`, always within the latency budget."""
        try:
            async with asyncio.timeout(self.settings.host_line_timeout):
                text = "".join([part async for part in self._generate(event, player_name)])
        except Exception:
            logger.warning("host line for %s failed, using fallback", event, exc_info=True)
            text = ""
        return clean_line(text) or fallback_line(event, player_name)
