"""Standard-library system-command execution with caller-owned subprocess policy."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[Any]]


def _live(arguments: list[str], **options: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(arguments, **options)


_RUNNER: ContextVar[Runner] = ContextVar("system_command_runner", default=_live)


@contextmanager
def use_runner(runner: Runner) -> Iterator[None]:
    """Inject command transport for one operation and its nested/recovery calls."""
    token = _RUNNER.set(runner)
    try:
        yield
    finally:
        _RUNNER.reset(token)


def run(arguments: list[str], **options: Any) -> subprocess.CompletedProcess[Any]:
    """Run a command, preserving its streams, environment, timeout and failure policy.

    The default adapter calls subprocess.run unchanged; injected transports own the
    same contract. No retry, deadline, stream mode or exception translation is added.
    """
    return _RUNNER.get()(arguments, **options)
