"""CSV loading and noise-spectrum export."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DualBpdData:
    """Loaded dual-channel oscilloscope record."""
    t: np.ndarray
    v1: np.ndarray
    v2: np.ndarray | None
    sample_rate: float
    source_files: tuple[Path, ...]

    @property
    def duration_s(self) -> float:
        return float(self.t[-1] - self.t[0])

    @property
    def n_samples(self) -> int:
        return int(self.t.size)


def load_csv(path: Path) -> DualBpdData:
    """Load a single CSV file. Supports:
    - 2 columns (time, voltage) -> single channel
    - 3 columns (time, ch1, ch2) -> dual channel
    """
    df = pd.read_csv(path)
    if df.shape[1] < 2:
        raise ValueError(f"CSV {path} has fewer than 2 columns")
    t = df.iloc[:, 0].to_numpy(dtype=np.float64)
    v1 = df.iloc[:, 1].to_numpy(dtype=np.float64)
    v2 = df.iloc[:, 2].to_numpy(dtype=np.float64) if df.shape[1] >= 3 else None

    dt = float(np.median(np.diff(t[: min(t.size, 1000)])))
    sample_rate = 1.0 / dt
    return DualBpdData(t=t, v1=v1, v2=v2, sample_rate=sample_rate,
                       source_files=(path,))


def load_two_csv(path1: Path, path2: Path) -> DualBpdData:
    """Load two single-channel CSV files and combine."""
    a = load_csv(path1)
    b = load_csv(path2)
    n = min(a.n_samples, b.n_samples)
    return DualBpdData(t=a.t[:n], v1=a.v1[:n], v2=b.v1[:n],
                       sample_rate=a.sample_rate,
                       source_files=(path1, path2))


def from_arrays(
    t: np.ndarray,
    v1: np.ndarray,
    v2: np.ndarray | None,
    sample_rate: float,
    label: str = "scope",
) -> DualBpdData:
    """Build a DualBpdData from in-memory arrays (e.g. fresh scope acquisition)."""
    return DualBpdData(t=t, v1=v1, v2=v2, sample_rate=sample_rate,
                       source_files=(Path(f"<{label}>"),))


def save_dual_bpd_csv(path: Path, data: "DualBpdData") -> None:
    """Persist a DualBpdData record as a 2- or 3-column CSV."""
    cols = {"t_s": data.t, "C2": data.v1}
    if data.v2 is not None:
        cols["C4"] = data.v2
    pd.DataFrame(cols).to_csv(path, index=False)


def export_spectrum(
    path: Path,
    freq: np.ndarray,
    columns: dict[str, np.ndarray],
    metadata: dict[str, str] | None = None,
) -> None:
    """Export a noise spectrum to CSV with optional metadata header lines."""
    df = pd.DataFrame({"frequency_Hz": freq, **columns})
    with open(path, "w", encoding="utf-8") as f:
        if metadata:
            for k, v in metadata.items():
                f.write(f"# {k}: {v}\n")
        df.to_csv(f, index=False)
