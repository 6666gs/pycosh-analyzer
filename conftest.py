"""Pytest root config.

Lives at the repo root so the `app` package is importable from tests, and
forces Qt into headless ``offscreen`` mode so GUI smoke tests run without a
display server.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import dataclass

import numpy as np


def synthetic_beat(n: int = 4096, sr: float = 1e6, fbeat: float = 1e5, seed: int = 0):
    """A valid heterodyne beat: carrier + tiny random-walk phase noise.

    Returns (t, v1, v2, sample_rate). Long enough that pycosh's smallest
    band (1 kHz at 1 MSa/s → 1000-sample segments) gets several segments.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    phase = 2 * np.pi * fbeat * t + 0.01 * np.cumsum(rng.standard_normal(n))
    v1 = np.sin(phase)
    v2 = np.sin(phase + 0.001 * rng.standard_normal(n))
    return t, v1, v2, sr


@dataclass
class FakeFrame:
    time_axis: np.ndarray
    voltages: dict
    sample_rate: float


class FakeScope:
    """Records driver calls; returns canned frames. Used as a context manager
    via FakeScopeFactory so AcquireWorker/MonitorWorker run without hardware."""

    def __init__(self, frames=None):
        t, v1, v2, sr = synthetic_beat()
        self._frame = FakeFrame(time_axis=t,
                                voltages={"C1": v1, "C2": v2},
                                sample_rate=sr)
        self.calls = []

    def __enter__(self):
        self.calls.append("enter")
        return self

    def __exit__(self, *exc):
        self.calls.append("close")
        return False

    def idn(self):
        return "FAKE,SDS7404,0,0"

    def single(self):
        self.calls.append("single")

    def stop(self):
        self.calls.append("stop")

    def run(self, continuous: bool = True):
        self.calls.append(f"run(continuous={continuous})")

    def read_channels(self, channels):
        self.calls.append(("read_channels", tuple(channels)))
        return self._frame


class FakeScopeFactory:
    """Callable(host, **kw) -> FakeScope. Keeps the last scope for assertions."""

    def __init__(self):
        self.last = None

    def __call__(self, host, *args, **kwargs):
        self.last = FakeScope()
        return self.last
