"""Persistence for live-monitoring output.

When monitoring runs with saving enabled, each cycle writes:
- ``spectrum_<NNNN>[_<stamp>].npz`` — that cycle's full noise spectrum, and
- ``trend_lorentz_beta.npz`` — the cumulative time series of the Lorentz
  linewidth (FWHM) and β-separation Gaussian FWHM, rewritten every cycle.

All functions are pure (no Qt) so they can be unit-tested directly.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

SPECTRUM_PREFIX = "spectrum"
TREND_FILENAME = "trend_lorentz_beta.npz"


def save_cycle_spectrum(path: str | Path, result) -> None:
    """Save one monitoring cycle's noise spectrum (Sν and Sφ, single-sideband)
    to an ``.npz`` with named, self-describing arrays."""
    np.savez(
        path,
        frequency_Hz=np.asarray(result.freq),
        S_nu_BPD1_Hz2_per_Hz=np.asarray(result.s_nu_11),
        S_nu_BPD2_Hz2_per_Hz=np.asarray(result.s_nu_22),
        S_nu_cross_Hz2_per_Hz=np.asarray(result.s_nu_12),
        S_nu_cross_err_Hz2_per_Hz=np.asarray(result.s_nu_12_err),
        S_phi_BPD1_rad2_per_Hz=np.asarray(result.s_phi_11),
        S_phi_BPD2_rad2_per_Hz=np.asarray(result.s_phi_22),
        S_phi_cross_rad2_per_Hz=np.asarray(result.s_phi_12),
        S_phi_cross_err_rad2_per_Hz=np.asarray(result.s_phi_12_err),
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
    collected so far (missing fits are stored as NaN).
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._index = 0
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
        result,
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

        self._elapsed.append(float(elapsed))
        self._lorentz.append(float(lorentz_fwhm) if lorentz_fwhm is not None else np.nan)
        self._beta.append(float(beta_fwhm) if beta_fwhm is not None else np.nan)
        save_trend(self.trend_path, self._elapsed, self._lorentz, self._beta)
        return spec_path
