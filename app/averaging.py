"""Multi-record frequency-noise averaging (ported from Plot_Linewidth.m).

Each record is a single-channel self-heterodyne beat trace. Per record we take
the Hilbert phase, detrend it (removes the carrier ramp), apply a Hann window,
and accumulate ``2·|rfft|²``. Averaging across N independent records lowers the
variance, revealing the white-frequency-noise floor; the Lorentzian linewidth
is ``π·S₀`` (Di Domenico). The MZI transfer function ``4·sin²(πfτ)`` is
deconvolved using the FSR resolved elsewhere in the app (manual or auto-cal).

``PsdAverager`` accumulates incrementally (it never keeps the raw records, which
can be hundreds of MB each), so the acquisition worker can stream records in and
snapshot a result at any checkpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy.signal as sg

# Defaults mirroring the MATLAB reference.
DEFAULT_N_SKIP = 10_000      # samples trimmed from each end of a record
DEFAULT_FMAX = 50e6          # upper edge of the noise-floor search band (Hz)


@dataclass(frozen=True)
class AveragedResult:
    """Averaged single-channel frequency-noise spectrum and derived linewidth."""
    freq: np.ndarray            # Hz (non-negative, rfftfreq)
    s_nu: np.ndarray            # frequency-noise PSD, Hz²/Hz
    s_phi: np.ndarray           # phase-noise PSD, rad²/Hz  (= s_nu / f²)
    n_avg: int                  # records averaged into this result
    floor_hz2_per_hz: float     # S₀ = min(s_nu) over (0, fmax)
    linewidth_hz: float         # π · S₀
    fsr_hz: float
    n_skip: int


class PsdAverager:
    """Accumulates ``2·|rfft(window·phase)|²`` across records (no raw retained)."""

    def __init__(self, n_skip: int = DEFAULT_N_SKIP) -> None:
        self.n_skip = int(n_skip)
        self._cumul: np.ndarray | None = None
        self._n = 0
        self._npts: int | None = None       # trimmed record length
        self._hann_sq_sum: float | None = None

    @property
    def count(self) -> int:
        return self._n

    def add(self, record: np.ndarray) -> None:
        """Process one record and add its single-sided phase spectrum."""
        x = np.asarray(record, dtype=np.float64)
        if self.n_skip > 0:
            if x.size <= 2 * self.n_skip:
                raise ValueError(
                    f"record of {x.size} samples is too short to trim "
                    f"{self.n_skip} from each end")
            x = x[self.n_skip: x.size - self.n_skip]
        x = x - x.mean()
        phase = np.unwrap(np.angle(sg.hilbert(x)))
        phase = sg.detrend(phase, type="linear")   # remove the carrier ramp
        n = x.size
        window = sg.windows.hann(n)
        power = 2.0 * np.abs(np.fft.rfft(window * phase)) ** 2

        if self._cumul is None:
            self._cumul = power
            self._npts = n
            self._hann_sq_sum = float(np.sum(window ** 2))
        else:
            if power.shape != self._cumul.shape:
                raise ValueError("all records must have the same length")
            self._cumul = self._cumul + power
        self._n += 1

    def result(self, sample_rate: float, fsr_hz: float,
               fmax: float = DEFAULT_FMAX) -> AveragedResult:
        """Snapshot the averaged spectrum + linewidth for the records so far."""
        if self._n == 0 or self._cumul is None:
            raise ValueError("no records have been added")
        n = self._npts
        freq = np.fft.rfftfreq(n, d=1.0 / sample_rate)
        psd = self._cumul / self._n
        psd = psd / (sample_rate * self._hann_sq_sum)          # one-sided PSD norm
        gfilter = 4.0 * np.sin(np.pi * freq / fsr_hz) ** 2     # τ = 1/FSR
        with np.errstate(divide="ignore", invalid="ignore"):
            s_phi = psd / gfilter                              # deconvolve MZI G(f)
            s_nu = s_phi * freq ** 2                           # → frequency noise
        band = (freq > 0) & (freq < fmax) & np.isfinite(s_nu)
        if np.any(band):
            floor = float(np.min(s_nu[band]))
            linewidth = float(np.pi * floor)
        else:
            floor = float("nan")
            linewidth = float("nan")
        return AveragedResult(
            freq=freq, s_nu=s_nu, s_phi=s_phi, n_avg=self._n,
            floor_hz2_per_hz=floor, linewidth_hz=linewidth,
            fsr_hz=fsr_hz, n_skip=self.n_skip,
        )


def average_records(records, sample_rate: float, fsr_hz: float,
                    n_skip: int = DEFAULT_N_SKIP,
                    fmax: float = DEFAULT_FMAX) -> AveragedResult:
    """Convenience: average a list/iterable of records in one call."""
    avg = PsdAverager(n_skip)
    for rec in records:
        avg.add(rec)
    return avg.result(sample_rate, fsr_hz, fmax)


def even_checkpoints(n_avg: int, count: int = 5) -> list[int]:
    """Up to ``count`` evenly spaced record counts at which to snapshot a
    convergence curve, always including ``n_avg`` (e.g. 10 → [2,4,6,8,10])."""
    if n_avg <= 1:
        return [n_avg] if n_avg == 1 else []
    count = min(count, n_avg)
    pts = {int(round(n_avg * k / count)) for k in range(1, count + 1)}
    pts.discard(0)
    pts.add(n_avg)
    return sorted(pts)


def save_averaged(path: str | Path, result: AveragedResult) -> None:
    """Save an AveragedResult as ``.csv`` (metadata header + columns) or
    ``.npz`` (named arrays + scalars). Dispatches on the suffix."""
    path = Path(path)
    suffix = path.suffix.lower()
    meta = {
        "n_avg": result.n_avg,
        "FSR_Hz": f"{result.fsr_hz:.6f}",
        "n_skip": result.n_skip,
        "floor_Hz2_per_Hz": f"{result.floor_hz2_per_hz:.6g}",
        "linewidth_Hz": f"{result.linewidth_hz:.6g}",
    }
    if suffix == ".csv":
        import pandas as pd
        df = pd.DataFrame({
            "frequency_Hz": result.freq,
            "S_nu_Hz2_per_Hz": result.s_nu,
            "S_phi_rad2_per_Hz": result.s_phi,
        })
        with open(path, "w", encoding="utf-8") as f:
            for k, v in meta.items():
                f.write(f"# {k}: {v}\n")
            df.to_csv(f, index=False)
    elif suffix == ".npz":
        np.savez(
            path,
            frequency_Hz=result.freq,
            S_nu_Hz2_per_Hz=result.s_nu,
            S_phi_rad2_per_Hz=result.s_phi,
            n_avg=result.n_avg,
            fsr_hz=result.fsr_hz,
            n_skip=result.n_skip,
            floor_hz2_per_hz=result.floor_hz2_per_hz,
            linewidth_hz=result.linewidth_hz,
        )
    else:
        raise ValueError(f"Unsupported format '{path.name}' (use .csv or .npz)")
