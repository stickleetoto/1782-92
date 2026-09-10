from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Iterator
from types import FrameType
from typing import Any

# Pure-Python modeling batches should be short. This is a cooperative guard:
# it can interrupt Python loops, but it cannot pre-empt one long-running C call
# inside Blender itself.
APPLY_BUDGET_S = 15.0
TRACE_CHECK_EVERY = 128


class ApplyBudgetExceeded(RuntimeError):
    pass


@contextlib.contextmanager
def deadline(seconds: float = APPLY_BUDGET_S) -> Iterator[None]:
    """Interrupt long-running Python in the current thread after *seconds*.

    Blender executes apply code on its main thread. A transport timeout alone is
    not enough because the Python batch may keep running after the HTTP client has
    given up. sys.settrace gives us a cooperative deadline for the common failure
    mode: generated Python loops that run far longer than intended.
    """
    if seconds <= 0:
        raise ValueError("deadline_seconds")

    previous = sys.gettrace()
    expires = time.perf_counter() + seconds
    events = 0

    def tracer(_frame: FrameType, event: str, _arg: Any):
        nonlocal events
        if event == "line":
            events += 1
            if events % TRACE_CHECK_EVERY == 0 and time.perf_counter() >= expires:
                raise ApplyBudgetExceeded("apply_budget_exceeded:split_batch")
        return tracer

    sys.settrace(tracer)
    try:
        yield
    finally:
        sys.settrace(previous)
