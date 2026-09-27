import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.voice import host as host_module
from app.voice.host import HostEvent, HostLLM, clean_line


def chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def completion(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


async def stream(*parts):
    for part in parts:
        yield chunk(part)


@pytest.fixture
def llm_settings(settings):
    return settings.model_copy(
        update={
            "llm_model": "vertex_ai/gemini-2.5-flash",
            "llm_streaming": True,
            "llm_extra_params": {"vertex_project": "proj", "vertex_location": "asia-south1"},
            "vertex_credentials": '{"type": "authorized_user"}',
            "host_line_timeout": 0.5,
        }
    )


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def install(response):
        async def acompletion(**kwargs):
            calls.append(kwargs)
            if isinstance(response, Exception):
                raise response
            result = response(**kwargs) if callable(response) else response
            return await result if inspect.isawaitable(result) else result

        monkeypatch.setattr(host_module.litellm, "acompletion", acompletion)
        return calls

    return install


async def test_streams_and_joins_line(llm_settings, fake_llm):
    calls = fake_llm(lambda **_: stream("Great job, ", "Ada!", None))

    line = await HostLLM(llm_settings).line(HostEvent.CORRECT, "Ada")

    assert line == "Great job, Ada!"
    kwargs = calls[0]
    assert kwargs["stream"] is True
    assert kwargs["model"] == "vertex_ai/gemini-2.5-flash"
    assert kwargs["vertex_project"] == "proj" and kwargs["vertex_location"] == "asia-south1"
    assert kwargs["vertex_credentials"] == '{"type": "authorized_user"}'
    assert kwargs["num_retries"] == llm_settings.llm_num_retries
    assert "Ada" in kwargs["messages"][1]["content"]


async def test_non_streaming_mode(llm_settings, fake_llm):
    calls = fake_llm(completion("Welcome aboard, Ada!"))
    settings = llm_settings.model_copy(update={"llm_streaming": False})

    assert await HostLLM(settings).line(HostEvent.GREETING, "Ada") == "Welcome aboard, Ada!"
    assert "stream" not in calls[0]


async def test_llm_error_falls_back_to_canned_line(llm_settings, fake_llm):
    fake_llm(RuntimeError("vertex unavailable"))

    assert await HostLLM(llm_settings).line(HostEvent.WRONG, "Ada") == "Oh no, not quite."


async def test_slow_llm_falls_back_within_budget(llm_settings, fake_llm):
    async def slow(**_):
        await asyncio.sleep(5)
        return stream("too late")

    fake_llm(slow)

    async with asyncio.timeout(2):
        assert await HostLLM(llm_settings).line(HostEvent.GREETING, "Ada") == "Hi Ada, welcome to Memory Cards!"


async def test_line_mentioning_a_card_is_rejected(llm_settings, fake_llm):
    fake_llm(lambda **_: stream("Was it the Bananas? Great job!"))

    assert await HostLLM(llm_settings).line(HostEvent.CORRECT, "Ada") == "Correct!"


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ('  **Nice**   work,\n"Ada"! ', "Nice work, Ada!"),
        ("", None),
        ("x" * 201, None),
        ("Your apple memory rocks", None),
        ("Pineapple-level focus!", "Pineapple-level focus!"),
    ],
)
def test_clean_line(raw, cleaned):
    assert clean_line(raw) == cleaned


async def test_failure_skips_llm_during_cooldown(llm_settings, fake_llm):
    calls = fake_llm(RuntimeError("no credentials"))
    host = HostLLM(llm_settings)

    await host.line(HostEvent.GREETING, "Ada")
    assert await host.line(HostEvent.CORRECT, "Ada") == "Correct!"

    assert len(calls) == 1
