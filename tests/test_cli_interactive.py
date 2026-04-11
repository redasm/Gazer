from __future__ import annotations

from types import SimpleNamespace

import pytest

from cli.interactive import InteractiveCLI


@pytest.mark.asyncio
async def test_invoke_command_accepts_sync_handler() -> None:
    cli = object.__new__(InteractiveCLI)
    observed: list[str] = []

    def _handler(args: str) -> None:
        observed.append(args)

    await InteractiveCLI._invoke_command(cli, _handler, "hello")
    assert observed == ["hello"]


@pytest.mark.asyncio
async def test_invoke_command_accepts_async_handler() -> None:
    cli = object.__new__(InteractiveCLI)
    observed: list[str] = []

    async def _handler(args: str) -> None:
        observed.append(args)

    await InteractiveCLI._invoke_command(cli, _handler, "world")
    assert observed == ["world"]


def test_new_session_command_resets_main_session() -> None:
    cli = object.__new__(InteractiveCLI)
    reset_calls: list[str] = []
    cli.agent = SimpleNamespace(
        loop=SimpleNamespace(reset_session=lambda session_key: reset_calls.append(session_key))
    )

    InteractiveCLI._cmd_new_session(cli, "")

    assert reset_calls == ["gazer:main"]
