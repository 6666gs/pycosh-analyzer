"""Continuous acquire+process loop for live phase-noise monitoring.

A MonitorWorker opens the scope once and repeatedly grabs a fresh single-shot
frame, runs pycosh on it (reusing the already-calibrated FSR), and emits the
ProcessResult to the main thread, until asked to stop. On stop it leaves the
scope running live. `scope_factory` lets tests inject a fake scope.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from .data_io import from_arrays
from .processor import ProcessRequest, ProcessResult, run_cosh
from .scope import ensure_sds7404_importable, frame_to_arrays


@dataclass(frozen=True)
class MonitorRequest:
    """Fixed parameters for a monitoring session (FSR is reused, not re-fit)."""
    host: str
    ch1: str
    ch2: str | None
    send_single: bool
    delay_freq: float
    bw_segment: tuple[float, ...]
    offset_start_ratio: int
    range_start: int | None
    range_stop: int | None


class MonitorWorker(QThread):
    """Loops single→read→run_cosh→emit until request_stop()."""
    # (ProcessResult, elapsed_seconds, raw DualBpdData for this cycle)
    cycle_done = Signal(object, float, object)
    progress = Signal(str)
    finished_err = Signal(str)

    def __init__(self, request: MonitorRequest, scope_factory=None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._req = request
        self._scope_factory = scope_factory
        self._stop = False

    def request_stop(self) -> None:
        """Ask the loop to finish after the current frame (thread-safe flag)."""
        self._stop = True

    def _open_scope(self):
        if self._scope_factory is not None:
            return self._scope_factory(self._req.host)
        ensure_sds7404_importable()
        from sds7404 import SDS7404  # type: ignore
        return SDS7404(self._req.host)

    def run(self) -> None:
        req = self._req
        channels = [req.ch1] + ([req.ch2] if req.ch2 else [])
        try:
            with self._open_scope() as scope:
                # Free-run in AUTO mode: the scope keeps acquiring and
                # auto-triggers, so each cycle reads a real beat without ever
                # blocking on an external trigger (a SINGle-shot wait would
                # hang forever when no trigger event occurs).
                scope.run()
                start = time.monotonic()
                while not self._stop:
                    # Freeze a coherent frame (stop → read → resume); stop()
                    # returns immediately, it does NOT wait for a trigger.
                    scope.stop()
                    frame = scope.read_channels(channels)
                    scope.run()
                    t, v1, v2, sr = frame_to_arrays(frame, req.ch1, req.ch2)
                    proc_req = ProcessRequest(
                        v1=v1, v2=v2, sample_rate=sr,
                        delay_freq=req.delay_freq,
                        bw_segment=req.bw_segment,
                        offset_start_ratio=req.offset_start_ratio,
                        range_start=req.range_start,
                        range_stop=req.range_stop,
                    )
                    result = run_cosh(proc_req)
                    raw = from_arrays(t, v1, v2, sr, label="monitor")
                    self.cycle_done.emit(result, time.monotonic() - start, raw)
                # Loop left the scope running (AUTO) — live for the operator.
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")
