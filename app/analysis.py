"""Lorentzian floor detection and β-separation linewidth (Di Domenico 2010).

References:
    Di Domenico, Schilt, Thomann, "Simple approach to the relation between
    laser frequency noise and laser line shape," Appl. Opt. 49, 4801 (2010).

Single-sideband (SSB) convention: S_ν is the one-sided density in Hz²/Hz
(positive offsets only). Di Domenico's relations apply directly in this
convention — FWHM_Lorentz = π · S₀ and the β-line is 8 ln2/π² · f.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# β-separation slope: above β-line, frequency noise contributes to the
# Gaussian (slow) part of the line; below it, to the Lorentzian (fast) part.
# β(f) = 8 ln(2) / π² · f ≈ 0.5615 · f       (single-sideband S_ν, Hz²/Hz)
BETA_SLOPE = 8.0 * np.log(2.0) / (np.pi**2)


@dataclass(frozen=True)
class LorentzFit:
    """White-noise floor S₀ and corresponding Lorentzian FWHM = π · S₀."""
    s0_hz2_per_hz: float          # white-noise PSD level (band minimum)
    fwhm_hz: float                # Lorentzian FWHM = π · S₀
    f_min: float
    f_max: float
    n_points: int                 # how many bins were searched for the minimum


@dataclass(frozen=True)
class BetaIntegration:
    """β-separation: integrate the part of S_ν that lies above β(f)·f,
    then convert to a Gaussian FWHM via Di Domenico's approximation."""
    area_hz2: float               # ∫ max(S_ν − β-line, 0) df  (Hz²)
    fwhm_gauss_hz: float          # sqrt(8 ln 2 · area)
    f_min: float
    f_max: float
    fraction_above_beta: float    # what % of the integration band lies above β


def beta_line(freq: np.ndarray) -> np.ndarray:
    """β-separation line: S_ν(f) = 8 ln(2) / π² · f."""
    return BETA_SLOPE * np.asarray(freq, dtype=np.float64)


def fit_lorentz_floor(
    freq: np.ndarray,
    s_nu: np.ndarray,
    f_min: float = 1e6,
    f_max: float | None = None,
) -> LorentzFit | None:
    """Estimate the white-noise floor S₀ from a clean high-offset band.

    Takes the *minimum* of S_ν over the band: the white-noise floor is the
    lowest the spectrum reaches, and the minimum naturally rejects the
    upward MZI G(f) fringe spikes (which appear at integer multiples of FSR).
    FWHM_Lorentz = π · S₀. Pick the band to avoid spurious low-noise dips,
    since the minimum is more sensitive to a single low bin than a median.

    Returns None if fewer than 3 points fall in [f_min, f_max].
    """
    freq = np.asarray(freq, dtype=np.float64)
    s_nu = np.asarray(s_nu, dtype=np.float64)
    if f_max is None:
        f_max = float(freq.max())
    mask = (freq >= f_min) & (freq <= f_max) & np.isfinite(s_nu) & (s_nu > 0)
    if mask.sum() < 3:
        return None
    s0 = float(np.min(s_nu[mask]))
    return LorentzFit(
        s0_hz2_per_hz=s0,
        fwhm_hz=float(np.pi * s0),
        f_min=f_min,
        f_max=f_max,
        n_points=int(mask.sum()),
    )


def integrate_beta(
    freq: np.ndarray,
    s_nu: np.ndarray,
    f_min: float | None = None,
    f_max: float | None = None,
) -> BetaIntegration | None:
    """β-separation method for the slow / Gaussian contribution to FWHM.

    Integrates only the portion of S_ν(f) above the β-line; that's the part
    of the noise spectrum that broadens the line shape (slow modulation).

    A = ∫_{f_min}^{f_max} max(S_ν(f) − β(f), 0) df       [Hz²]
    FWHM_G ≈ sqrt(8 ln 2 · A)
    """
    freq = np.asarray(freq, dtype=np.float64)
    s_nu = np.asarray(s_nu, dtype=np.float64)
    if f_min is None:
        f_min = float(freq.min())
    if f_max is None:
        f_max = float(freq.max())
    mask = (freq >= f_min) & (freq <= f_max) & np.isfinite(s_nu) & (s_nu > 0)
    if mask.sum() < 2:
        return None
    f = freq[mask]
    s = s_nu[mask]
    above = np.maximum(s - beta_line(f), 0.0)
    area = float(np.trapezoid(above, f))
    above_count = int((above > 0).sum())
    return BetaIntegration(
        area_hz2=area,
        fwhm_gauss_hz=float(np.sqrt(8.0 * np.log(2.0) * area)),
        f_min=f_min,
        f_max=f_max,
        fraction_above_beta=above_count / max(1, mask.sum()),
    )
