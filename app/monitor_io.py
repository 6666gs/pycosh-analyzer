"""Persistence and in-memory buffering for live-monitoring output.

When monitoring runs with saving enabled, each cycle writes:
- ``spectrum_<NNNN>[_<stamp>].npz`` — that cycle's full noise spectrum, stored
  with both the raw building blocks (freq, gfilter, psd*) needed to rebuild a
  ProcessResult *and* the derived single-sideband Sν/Sφ columns for external
  analysis, and
- ``trend_lorentz_beta.npz`` — the cumulative time series of the Lorentz
  linewidth (FWHM) and β-separation Gaussian FWHM, rewritten every cycle.

``MonitorFrame`` is the in-memory record of one cycle (raw trace + result),
used for the rollback-save buffer. All functions here are pure (no Qt).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .data_io import DualBpdData
from .processor import ProcessRequest, ProcessResult

SPECTRUM_PREFIX = "spectrum"
TREND_FILENAME = "trend_lorentz_beta.npz"

# Sentinel for a None range_start/range_stop (valid indices are >= 0).
_NO_RANGE = -1


def _enc_range(v: int | None) -> int:
    return _NO_RANGE if v is None else int(v)


def _dec_range(v) -> int | None:
    v = int(v)
    return None if v == _NO_RANGE else v


@dataclass(frozen=True)
class MonitorFrame:
    """One monitoring cycle held in memory for rollback save."""
    raw: DualBpdData
    result: ProcessResult
    elapsed: float
    stamp: str


def save_cycle_spectrum(path: str | Path, result: ProcessResult) -> None:
    """Save one cycle's noise spectrum to an ``.npz``.

    Stores the raw psd/gfilter + request scalars (so load_spectrum_result can
    rebuild an identical ProcessResult) alongside the human-readable derived
    single-sideband Sν/Sφ columns.
    """
    req = result.request
    np.savez(
        path,
        # --- raw building blocks for exact reconstruction ---
        frequency_Hz=np.asarray(result.freq),
        gfilter=np.asarray(result.gfilter),
        psd11=np.asarray(result.psd11),
        psd11_err=np.asarray(result.psd11_err),
        psd22=np.asarray(result.psd22),
        psd22_err=np.asarray(result.psd22_err),
        psd12=np.asarray(result.psd12),
        psd12_err=np.asarray(result.psd12_err),
        sample_rate=float(req.sample_rate),
        delay_freq=float(req.delay_freq),
        bw_segment=np.asarray(req.bw_segment, dtype=float),
        offset_start_ratio=int(req.offset_start_ratio),
        range_start=_enc_range(req.range_start),
        range_stop=_enc_range(req.range_stop),
        has_v2=int(req.v2 is not None),
        # --- derived single-sideband spectra (for external analysis) ---
        S_nu_BPD1_Hz2_per_Hz=np.asarray(result.s_nu_11),
        S_nu_BPD2_Hz2_per_Hz=np.asarray(result.s_nu_22),
        S_nu_cross_Hz2_per_Hz=np.asarray(result.s_nu_12),
        S_nu_cross_err_Hz2_per_Hz=np.asarray(result.s_nu_12_err),
        S_phi_BPD1_rad2_per_Hz=np.asarray(result.s_phi_11),
        S_phi_BPD2_rad2_per_Hz=np.asarray(result.s_phi_22),
        S_phi_cross_rad2_per_Hz=np.asarray(result.s_phi_12),
        S_phi_cross_err_rad2_per_Hz=np.asarray(result.s_phi_12_err),
    )


def load_spectrum_result(path: str | Path) -> ProcessResult:
    """Rebuild a ProcessResult from a spectrum ``.npz`` written by
    save_cycle_spectrum (raw v1/v2 are not stored — request carries an empty
    placeholder, with v2 present iff the original had a second channel)."""
    with np.load(path) as d:
        has_v2 = bool(int(d["has_v2"]))
        req = ProcessRequest(
            v1=np.empty(0),
            v2=(np.empty(0) if has_v2 else None),
            sample_rate=float(d["sample_rate"]),
            delay_freq=float(d["delay_freq"]),
            bw_segment=tuple(float(x) for x in d["bw_segment"]),
            offset_start_ratio=int(d["offset_start_ratio"]),
            range_start=_dec_range(d["range_start"]),
            range_stop=_dec_range(d["range_stop"]),
        )
        return ProcessResult(
            freq=np.asarray(d["frequency_Hz"]),
            gfilter=np.asarray(d["gfilter"]),
            psd11=np.asarray(d["psd11"]),
            psd11_err=np.asarray(d["psd11_err"]),
            psd22=np.asarray(d["psd22"]),
            psd22_err=np.asarray(d["psd22_err"]),
            psd12=np.asarray(d["psd12"]),
            psd12_err=np.asarray(d["psd12_err"]),
            request=req,
        )


def save_trend(
    path: str | Path,
    elapsed_s,
    lorentz_fwhm_hz,
    beta_fwhm_hz,
) -> None:
    """Write the cumulative Lorentz/β trend to an ``.npz`` (overwrites)."""
    np.savez(
        path,
        elapsed_s=np.asarray(elapsed_s, dtype=float),
        lorentz_fwhm_hz=np.asarray(lorentz_fwhm_hz, dtype=float),
        beta_fwhm_hz=np.asarray(beta_fwhm_hz, dtype=float),
    )


class MonitorRecorder:
    """Writes per-cycle spectra and a cumulative Lorentz/β trend to a folder.

    One instance per monitoring session. ``record()`` is called once per cycle;
    it saves that cycle's spectrum and rewrites the trend file with every point
    collected so far (missing fits are stored as NaN). ``spectrum_paths`` keeps
    the written files in cycle order so a trend-point index maps to its file.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._index = 0
        self.spectrum_paths: list[Path] = []
        self._elapsed: list[float] = []
        self._lorentz: list[float] = []
        self._beta: list[float] = []

    @property
    def count(self) -> int:
        return self._index

    @property
    def trend_path(self) -> Path:
        return self.directory / TREND_FILENAME

    def record(
        self,
        result: ProcessResult,
        elapsed: float,
        lorentz_fwhm: float | None,
        beta_fwhm: float | None,
        stamp: str = "",
    ) -> Path:
        """Persist one cycle. Returns the spectrum file path."""
        self._index += 1
        name = f"{SPECTRUM_PREFIX}_{self._index:04d}"
        if stamp:
            name += f"_{stamp}"
        spec_path = self.directory / f"{name}.npz"
        save_cycle_spectrum(spec_path, result)
        self.spectrum_paths.append(spec_path)

        self._elapsed.append(float(elapsed))
        self._lorentz.append(float(lorentz_fwhm) if lorentz_fwhm is not None else np.nan)
        self._beta.append(float(beta_fwhm) if beta_fwhm is not None else np.nan)
        save_trend(self.trend_path, self._elapsed, self._lorentz, self._beta)
        return spec_path
