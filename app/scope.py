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


def auto_calibrate_fsr(
    v1: np.ndarray, sample_rate: float, n_core: float
) -> tuple[float, str]:
    """Auto-calibrate the MZI FSR from one record. Returns ``(fsr_hz, message)``
    or raises ``RuntimeError`` (surfaced to the user) when no FSR dip is found.
    Shared by the scope-streaming and file-based averaging workers."""
    from .mzi_calibrate import calibrate_mzi

    res = calibrate_mzi(v1, sample_rate, n_core)
    if res.fsr_hz is None:
        raise RuntimeError(
            "Could not auto-detect the MZI FSR from the first record. "
            "Tick 'Manual FSR' and enter the fiber length / τ, then average "
            "again.")
    msg = (f"Auto-calibrated FSR = {res.fsr_hz / 1e6:.4f} MHz "
           f"(ΔL ≈ {res.delta_L_m:.1f} m). Averaging …")
    return res.fsr_hz, msg


class AverageAcquireWorker(QThread):
    """Read ch1 ``n_avg`` times and average their frequency-noise spectra
    (Plot_Linewidth method). Free-runs in AUTO mode (stop → read → run each
    cycle) so it never blocks on a trigger. Emits the final AveragedResult plus
    per-checkpoint snapshots when convergence curves are requested.

    The MZI FSR needed for the G(f) compensation can be supplied up front
    (``fsr_hz``); when it's ``None`` the worker auto-calibrates it from the very
    first acquired record (no pre-loaded file required) and fails with a clear
    message if no FSR dip is found, prompting the user to set it manually."""

    progress = Signal(str)
    finished_ok = Signal(object)     # (final AveragedResult, [checkpoint results])
    finished_err = Signal(str)

    def __init__(
        self,
        host: str,
        ch1: str,
        n_avg: int,
        fsr_hz: float | None,
        n_skip: int,
        fmax: float,
        with_convergence: bool = False,
        n_core: float = 1.468,
        keep_raw: bool = False,
        timeout_ms: int = 30_000,
        scope_factory=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.ch1 = ch1
        self.n_avg = int(n_avg)
        self.fsr_hz = fsr_hz
        self.n_skip = int(n_skip)
        self.fmax = fmax
        self.with_convergence = with_convergence
        self.n_core = n_core
        self.keep_raw = keep_raw
        self.timeout_ms = timeout_ms
        self._scope_factory = scope_factory
        # Populated only when keep_raw is set (opt-in, since N raw records can be
        # gigabytes): the acquired single-BPD traces + their sample rate, so the
        # caller can save them as one multi-record file afterwards.
        self.raw_records: list[np.ndarray] | None = [] if keep_raw else None
        self.sample_rate_hz: float | None = None

    def _open_scope(self):
        if self._scope_factory is not None:
            return self._scope_factory(self.host, timeout_ms=self.timeout_ms)
        ensure_sds7404_importable()
        from sds7404 import SDS7404  # type: ignore
        return SDS7404(self.host, timeout_ms=self.timeout_ms)

    def run(self) -> None:
        from .averaging import PsdAverager, even_checkpoints
        try:
            averager = PsdAverager(self.n_skip)
            checkpoints = (set(even_checkpoints(self.n_avg))
                           if self.with_convergence else set())
            snapshots = []
            sample_rate = None
            fsr = self.fsr_hz                     # None → auto-calibrate below
            with self._open_scope() as scope:
                scope.run()                      # AUTO free-run
                for i in range(self.n_avg):
                    scope.stop()                 # coherent frame, no trigger wait
                    frame = scope.read_channels([self.ch1])
                    scope.run()
                    _t, v1, _v2, sample_rate = frame_to_arrays(frame, self.ch1, None)
                    if fsr is None:
                        fsr, msg = auto_calibrate_fsr(v1, sample_rate, self.n_core)
                        self.fsr_hz = fsr            # expose the resolved value
                        self.progress.emit(msg)
                    if self.raw_records is not None:
                        self.raw_records.append(np.array(v1, dtype=np.float64))
                    averager.add(v1)
                    self.progress.emit(f"Averaging {i + 1}/{self.n_avg} …")
                    if (i + 1) in checkpoints:
                        snapshots.append(
                            averager.result(sample_rate, fsr, self.fmax))
            self.sample_rate_hz = sample_rate
            final = averager.result(sample_rate, fsr, self.fmax)
            self.finished_ok.emit((final, snapshots))
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")


class AverageFileWorker(QThread):
    """Average a multi-record file (one ``.npz`` holding N single-BPD raw
    records) off the main thread. Re-uses the exact ``PsdAverager`` pipeline as
    the scope path — only the record source differs (file rows vs scope reads).

    The MZI FSR is supplied up front (manual override) or, when ``fsr_hz`` is
    ``None``, auto-calibrated from the first record in the file."""

    progress = Signal(str)
    finished_ok = Signal(object)     # (final AveragedResult, [checkpoint results])
    finished_err = Signal(str)

    def __init__(
        self,
        path: str | Path,
        fsr_hz: float | None,
        n_skip: int,
        fmax: float,
        with_convergence: bool = False,
        n_core: float = 1.468,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.fsr_hz = fsr_hz
        self.n_skip = int(n_skip)
        self.fmax = fmax
        self.with_convergence = with_convergence
        self.n_core = n_core

    def run(self) -> None:
        from .averaging import PsdAverager, even_checkpoints
        from .data_io import load_records
        try:
            records, sample_rate = load_records(self.path)
            n_avg = int(records.shape[0])
            averager = PsdAverager(self.n_skip)
            checkpoints = (set(even_checkpoints(n_avg))
                           if self.with_convergence else set())
            snapshots = []
            fsr = self.fsr_hz                     # None → auto-calibrate below
            for i in range(n_avg):
                v1 = records[i]
                if fsr is None:
                    fsr, msg = auto_calibrate_fsr(v1, sample_rate, self.n_core)
                    self.fsr_hz = fsr
                    self.progress.emit(msg)
                averager.add(v1)
                self.progress.emit(f"Averaging {i + 1}/{n_avg} …")
                if (i + 1) in checkpoints:
                    snapshots.append(averager.result(sample_rate, fsr, self.fmax))
            final = averager.result(sample_rate, fsr, self.fmax)
            self.finished_ok.emit((final, snapshots))
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")
