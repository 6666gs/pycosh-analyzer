"""Auto-calibrate MZI delay-line FSR from a single-BPD heterodyne trace.

Adapted from the user's measure_mzi_delay() snippet: detect the first
zero of the 4 sin^2(pi f tau) MZI transfer function in the frequency-noise
PSD; FSR = first zero, tau = 1/FSR, delta_L = c / (n_core * FSR).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.signal as sg

C_LIGHT = 299_792_458.0


@dataclass(frozen=True)
class MziResult:
    fsr_hz: float | None
    tau_s: float | None
    delta_L_m: float | None
    contrast: float | None
    reliable: bool
    carrier_hz: float
    sample_rate: float
    freq: np.ndarray
    psd: np.ndarray
    search_lo: float
    search_hi: float
    n_dips: int


def calibrate_mzi(
    v: np.ndarray,
    sample_rate: float,
    n_core: float = 1.468,
    nperseg: int = 131_072,
    search_lo: float = 5e5,
    search_hi: float | None = None,
) -> MziResult:
    """Infer MZI delay from a single-BPD self-heterodyne trace.

    Args:
        v           : single-channel oscilloscope voltage trace
        sample_rate : Hz
        n_core      : fiber effective index
        nperseg     : Welch segment length (larger if FSR is small)
        search_lo   : low end of FSR search band (Hz)
        search_hi   : high end (default 0.9 * detected carrier)

    Returns:
        MziResult — fsr/tau/delta_L are None when no clean zero is found.
    """
    v = np.asarray(v, dtype=np.float64)
    v = v - v.mean()
    inst_freq = (
        np.diff(np.unwrap(np.angle(sg.hilbert(v)))) * sample_rate / (2 * np.pi)
    )
    carrier = float(inst_freq.mean())
    freq, psd = sg.welch(inst_freq - carrier, fs=sample_rate, nperseg=nperseg)

    if search_hi is None:
        search_hi = 0.9 * carrier
    band = (freq >= search_lo) & (freq <= search_hi)
    fb, pb = freq[band], psd[band]
    if len(pb) > 51:
        logp_s = sg.savgol_filter(np.log10(pb), 51, 3)
    else:
        logp_s = np.log10(pb)
    dips, props = sg.find_peaks(-logp_s, prominence=0.5)

    if len(dips):
        fsr = float(fb[dips[0]])
        contrast = float(10 ** props["prominences"][0])
        return MziResult(
            fsr_hz=fsr,
            tau_s=1.0 / fsr,
            delta_L_m=C_LIGHT / (n_core * fsr),
            contrast=contrast,
            reliable=contrast >= 10,
            carrier_hz=carrier,
            sample_rate=sample_rate,
            freq=freq,
            psd=psd,
            search_lo=search_lo,
            search_hi=search_hi,
            n_dips=len(dips),
        )
    return MziResult(
        fsr_hz=None, tau_s=None, delta_L_m=None, contrast=None,
        reliable=False, carrier_hz=carrier, sample_rate=sample_rate,
        freq=freq, psd=psd, search_lo=search_lo, search_hi=search_hi,
        n_dips=0,
    )
