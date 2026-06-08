"""Background worker that runs pycosh's CoshXcorr without blocking the UI."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

# Lookup order: already-on-sys.path → vendor/ → optional external override
_REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_PYCOSH_PARENT = _REPO_ROOT / "vendor"
EXTERNAL_PYCOSH_PARENT_ENV = "DBPD_PYCOSH_PARENT"

# pycosh returns a *two-sided* PSD (S(f) defined for ±f). We display the
# single-sideband (SSB) spectrum — positive frequencies only — so every
# physical spectrum is the two-sided density times this factor. With the SSB
# convention the Di Domenico relations hold directly: FWHM_Lorentz = π · S₀
# and the β-separation line is 8 ln2/π² · f (see app/analysis.py).
SSB_FACTOR = 2.0


def ensure_pycosh_importable() -> None:
    """Make `pycosh` importable. Prefers the vendored copy under vendor/;
    falls back to $DBPD_PYCOSH_PARENT if set, for power users who want to
    point at a development checkout of upstream pycosh."""
    try:
        import pycosh  # noqa: F401
        return
    except ImportError:
        pass

    import os
    candidates: list[Path] = []
    if VENDOR_PYCOSH_PARENT.exists():
        candidates.append(VENDOR_PYCOSH_PARENT)
    override = os.environ.get(EXTERNAL_PYCOSH_PARENT_ENV)
    if override:
        candidates.append(Path(override))

    for parent in candidates:
        if (parent / "pycosh").exists():
            sys.path.insert(0, str(parent))
            return


@dataclass(frozen=True)
class ProcessRequest:
    v1: np.ndarray
    v2: np.ndarray | None        # None -> autocorrelation only
    sample_rate: float
    delay_freq: float            # FSR in Hz
    bw_segment: tuple[float, ...]
    offset_start_ratio: int
    range_start: int | None
    range_stop: int | None


@dataclass(frozen=True)
class ProcessResult:
    freq: np.ndarray
    gfilter: np.ndarray
    psd11: np.ndarray
    psd11_err: np.ndarray
    psd22: np.ndarray
    psd22_err: np.ndarray
    psd12: np.ndarray
    psd12_err: np.ndarray
    request: ProcessRequest

    # Derived spectra (G(f) compensated), single-sideband (× SSB_FACTOR).
    # s_phi_* derive from s_nu_*, so they inherit the SSB factor automatically.
    @property
    def s_nu_11(self) -> np.ndarray:
        return SSB_FACTOR * np.abs(self.psd11) / self.gfilter

    @property
    def s_nu_22(self) -> np.ndarray:
        return SSB_FACTOR * np.abs(self.psd22) / self.gfilter

    @property
    def s_nu_12(self) -> np.ndarray:
        return SSB_FACTOR * np.abs(self.psd12) / self.gfilter

    @property
    def s_nu_12_err(self) -> np.ndarray:
        return SSB_FACTOR * np.abs(self.psd12_err) / self.gfilter

    @property
    def s_phi_11(self) -> np.ndarray:
        return self.s_nu_11 / self.freq**2

    @property
    def s_phi_22(self) -> np.ndarray:
        return self.s_nu_22 / self.freq**2

    @property
    def s_phi_12(self) -> np.ndarray:
        return self.s_nu_12 / self.freq**2

    @property
    def s_phi_12_err(self) -> np.ndarray:
        return self.s_nu_12_err / self.freq**2


def run_cosh(
    request: ProcessRequest,
    progress=None,
    use_gpu: bool = True,
) -> ProcessResult:
    """Run pycosh's CoshXcorr for one request and build a ProcessResult.

    Pure (no Qt). Shared by ProcessWorker and MonitorWorker. `progress` is an
    optional callable(str) for status messages.

    When ``use_gpu`` is True (default) the call dispatches through
    ``CoshXcorr.process_gpu()``, which auto-selects a CUDA/ROCm GPU if one is
    present and otherwise runs the parallel CPU path. On Apple Silicon / iGPU /
    AMD-on-Windows machines this transparently uses the CPU (see
    CoshXcorr.process_gpu for why Metal/MPS is skipped). Pass ``use_gpu=False``
    to force the CPU path.
    """
    def _say(msg: str) -> None:
        if progress is not None:
            progress(msg)

    ensure_pycosh_importable()
    from pycosh import CoshConfig, CoshXcorr  # type: ignore

    _say("Configuring pycosh …")
    cfg = CoshConfig(
        delay_freq=request.delay_freq,
        bw_segment=list(request.bw_segment),
        sample_rate=request.sample_rate,
        offset_start_ratio=request.offset_start_ratio,
        range_start=request.range_start,
        range_stop=request.range_stop,
    )
    trace2 = request.v2 if request.v2 is not None else request.v1
    cosh = CoshXcorr(trace1=request.v1, trace2=trace2, config=cfg)
    if use_gpu:
        _say("Running Hilbert + multi-band FFT (GPU if available, else CPU) …")
        cosh.process_gpu(print_progress=False)
    else:
        _say("Running Hilbert + multi-band FFT (CPU) …")
        cosh.process(print_progress=False)
    return ProcessResult(
        freq=np.asarray(cosh.freq_list),
        gfilter=np.asarray(cosh.freq_filter),
        psd11=np.asarray(cosh.psd11),
        psd11_err=np.asarray(cosh.psd11_err),
        psd22=np.asarray(cosh.psd22),
        psd22_err=np.asarray(cosh.psd22_err),
        psd12=np.asarray(cosh.psd12),
        psd12_err=np.asarray(cosh.psd12_err),
        request=request,
    )


class ProcessWorker(QThread):
    """Runs CoshXcorr.process() off the main thread."""
    progress = Signal(str)
    finished_ok = Signal(object)     # ProcessResult
    finished_err = Signal(str)

    def __init__(self, request: ProcessRequest, parent: QObject | None = None):
        super().__init__(parent)
        self._req = request

    def run(self) -> None:
        try:
            result = run_cosh(self._req, progress=self.progress.emit)
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")


class CalibrateWorker(QThread):
    """Runs MZI FSR calibration off the main thread."""
    finished_ok = Signal(object)     # MziResult
    finished_err = Signal(str)

    def __init__(
        self,
        v: np.ndarray,
        sample_rate: float,
        n_core: float,
        nperseg: int = 131_072,
        search_lo: float = 5e5,
        search_hi: float | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.v = v
        self.sample_rate = sample_rate
        self.n_core = n_core
        self.nperseg = nperseg
        self.search_lo = search_lo
        self.search_hi = search_hi

    def run(self) -> None:
        try:
            from .mzi_calibrate import calibrate_mzi
            res = calibrate_mzi(
                self.v, self.sample_rate, n_core=self.n_core,
                nperseg=self.nperseg,
                search_lo=self.search_lo, search_hi=self.search_hi,
            )
            self.finished_ok.emit(res)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")
