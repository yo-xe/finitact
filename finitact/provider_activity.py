"""Tells an observer when Finitact is waiting on an external model (ADR-0010 追記4).

The indicator shows the decision provider as a separate spark that lights only while a request is in
flight. A context variable carries the observer, so only the run that set it is reported, and the
provider code needs no reference to the indicator.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

Listener = Callable[[bool], None]

_LISTENER: ContextVar[Listener | None] = ContextVar("finitact_provider_listener", default=None)


@contextmanager
def listening(listener: Listener | None) -> Iterator[None]:
    token = _LISTENER.set(listener)
    try:
        yield
    finally:
        _LISTENER.reset(token)


@contextmanager
def provider_call() -> Iterator[None]:
    listener = _LISTENER.get()
    if listener is None:
        yield
        return
    listener(True)
    try:
        yield
    finally:
        listener(False)
