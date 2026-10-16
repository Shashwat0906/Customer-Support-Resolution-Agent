"""Crash-point injection for recovery tests.

``SimulatedCrash`` derives from ``BaseException`` so ordinary ``except
Exception`` handlers do not swallow it: it behaves like the process dying at
that exact point. Only tests and the evaluation harness arm crash points.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_armed: dict[str, int] = {}


class SimulatedCrash(BaseException):
    def __init__(self, point: str) -> None:
        super().__init__(f"simulated crash at {point}")
        self.point = point


def arm(point: str, times: int = 1) -> None:
    with _lock:
        _armed[point] = _armed.get(point, 0) + times


def disarm_all() -> None:
    with _lock:
        _armed.clear()


def maybe_crash(point: str) -> None:
    with _lock:
        remaining = _armed.get(point, 0)
        if remaining <= 0:
            return
        _armed[point] = remaining - 1
    raise SimulatedCrash(point)
