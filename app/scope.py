"""Background worker for SDS7404 oscilloscope acquisition."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

# Lookup order: already-on-sys.path → vendor/ → optional external override
_REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_SDS7404_PARENT = _REPO_ROOT / "vendor"
EXTERNAL_SDS7404_PARENT_ENV = "DBPD_SDS7404_PARENT"


def ensure_sds7404_importable() -> None:
    """Make `sds7404` importable. Prefers the vendored driver under vendor/;
    falls back to $DBPD_SDS7404_PARENT if set."""
    try:
        import sds7404  # noqa: F401
        return
    except ImportError:
        pass

    import os
    candidates: list[Path] = []
    if VENDOR_SDS7404_PARENT.exists():
        candidates.append(VENDOR_SDS7404_PARENT)
    override = os.environ.get(EXTERNAL_SDS7404_PARENT_ENV)
    if override:
        candidates.append(Path(override))

    for parent in candidates:
        if (parent / "sds7404").exists():
            sys.path.insert(0, str(parent))
            return


class AcquireWorker(QThread):
    """Pulls a multichannel frame from the scope off the main thread.

    After reading, resumes live acquisition (Feature: scope keeps running
    after a one-shot Acquire) unless `resume=False`. `scope_factory` lets
    tests inject a fake scope; production uses the vendored SDS7404 driver.
    """
    progress = Signal(str)
    finished_ok = Signal(object)     # (frame, ch1_name, ch2_name)
    finished_err = Signal(str)

    def __init__(
        self,
        host: str,
        ch1: str,
        ch2: str | None,
        send_single: bool = False,
        resume: bool = True,
        timeout_ms: int = 30_000,
        scope_factory=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.ch1 = ch1
        self.ch2 = ch2
        self.send_single = send_single
        self.resume = resume
        self.timeout_ms = timeout_ms
        self._scope_factory = scope_factory

    def _open_scope(self):
        if self._scope_factory is not None:
            return self._scope_factory(self.host, timeout_ms=self.timeout_ms)
        ensure_sds7404_importable()
        from sds7404 import SDS7404  # type: ignore
        return SDS7404(self.host, timeout_ms=self.timeout_ms)

    def run(self) -> None:
        try:
            with self._open_scope() as scope:
                idn = scope.idn()
                self.progress.emit(f"Connected: {idn}")
                if self.send_single:
                    self.progress.emit("Sending SINGle trigger, waiting Stop …")
                    scope.single()
                else:
                    # Freeze a coherent frame from the live acquisition. Reading
                    # an un-stopped scope yields a torn frame whose carrier is
                    # wrong, which makes FSR auto-calibration miss its dip.
                    scope.stop()
                channels = [self.ch1] + ([self.ch2] if self.ch2 else [])
                self.progress.emit(f"Reading channels {channels} …")
                frame = scope.read_channels(channels)
                if self.resume:
                    # Resume live acquisition so the operator keeps seeing the
                    # signal after the one-shot grab.
                    scope.run()
            self.finished_ok.emit((frame, self.ch1, self.ch2))
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")


class TestConnectionWorker(QThread):
    """Lightweight reachability check: connect, query ``*IDN?``, disconnect.

    Unlike AcquireWorker it triggers no acquisition and changes no scope state,
    so it is safe to run any time. `scope_factory` lets tests inject a fake
    scope; production uses the vendored SDS7404 driver.
    """
    finished_ok = Signal(str)        # idn string
    finished_err = Signal(str)

    def __init__(
        self,
        host: str,
        timeout_ms: int = 5_000,
        scope_factory=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.timeout_ms = timeout_ms
        self._scope_factory = scope_factory

    def _open_scope(self):
        if self._scope_factory is not None:
            return self._scope_factory(self.host, timeout_ms=self.timeout_ms)
        ensure_sds7404_importable()
        from sds7404 import SDS7404  # type: ignore
        return SDS7404(self.host, timeout_ms=self.timeout_ms)

    def run(self) -> None:
        try:
            with self._open_scope() as scope:
                idn = scope.idn()
            self.finished_ok.emit(idn)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")


def frame_to_arrays(
    frame, ch1: str, ch2: str | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, float]:
    """Convert sds7404 MultiChannelFrame → (t, v1, v2, sample_rate)."""
    v1 = np.asarray(frame.voltages[ch1], dtype=np.float64)
    v2 = (np.asarray(frame.voltages[ch2], dtype=np.float64)
          if ch2 and ch2 in frame.voltages else None)
    t = np.asarray(frame.time_axis, dtype=np.float64)
    return t, v1, v2, float(frame.sample_rate)
