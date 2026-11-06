"""Cancellation checkpoints for the current bounded evidence request."""

from collections.abc import Callable
from contextvars import ContextVar


request_cancellation: ContextVar[Callable[[], None] | None] = ContextVar(
    "evidence_request_cancellation", default=None,
)


def checkpoint() -> None:
    check = request_cancellation.get()
    if check is not None:
        check()
