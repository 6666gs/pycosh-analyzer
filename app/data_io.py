"""CSV / NumPy (.npy/.npz) loading and noise-spectrum export.

Binary formats load 30–60× faster than CSV (no text parsing): an `.npy`
holds a single 2-D ``(N, 2|3)`` array of columns ``t, v1[, v2]``; an `.npz`
holds named arrays ``t_s, C2[, C4]`` mirroring the CSV column names.  Voltage
samples are kept ``float64`` so binary-loaded data reproduces CSV results
bit-for-bit on the CPU path (the GPU path casts to float32 internally).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Every on-disk record format the app can load.
DATA_SUFFIXES = (".csv", ".npy", ".npz")


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


# ---------- binary (.npy / .npz) formats ----------

def is_data_path(p: str | Path) -> bool:
    """True if the suffix is one the app knows how to load (csv/npy/npz)."""
    return Path(p).suffix.lower() in DATA_SUFFIXES


def _from_columns(
    t: np.ndarray,
    v1: np.ndarray,
    v2: np.ndarray | None,
    source: Path,
) -> DualBpdData:
    """Build a DualBpdData from raw column arrays, inferring the sample rate
    from the time column. Voltages are float64 to match the CSV path."""
    t = np.asarray(t, dtype=np.float64)
    v1 = np.asarray(v1, dtype=np.float64)
    v2 = np.asarray(v2, dtype=np.float64) if v2 is not None else None
    dt = float(np.median(np.diff(t[: min(t.size, 1000)])))
    return DualBpdData(t=t, v1=v1, v2=v2, sample_rate=1.0 / dt,
                       source_files=(Path(source),))


def load_npy(path: Path) -> DualBpdData:
    """Load a 2-D ``.npy`` of columns ``t, v1[, v2]`` (matches save_dual_bpd_npy
    and the column layout produced by external NumPy exports)."""
    arr = np.load(path)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(f".npy {path} must be 2-D with at least 2 columns")
    v2 = arr[:, 2] if arr.shape[1] >= 3 else None
    return _from_columns(arr[:, 0], arr[:, 1], v2, path)


def load_npz(path: Path) -> DualBpdData:
    """Load an ``.npz`` archive. Prefers named arrays ``t_s, C2[, C4]``;
    falls back to positional order (first array = time, then channels)."""
    with np.load(path) as npz:
        files = list(npz.files)
        if "t_s" in files and "C2" in files:
            t, v1 = npz["t_s"], npz["C2"]
            v2 = npz["C4"] if "C4" in files else None
        else:
            if len(files) < 2:
                raise ValueError(f".npz {path} needs at least 2 arrays")
            t, v1 = npz[files[0]], npz[files[1]]
            v2 = npz[files[2]] if len(files) >= 3 else None
    return _from_columns(t, v1, v2, path)


def load_record(path: str | Path) -> DualBpdData:
    """Load a single data file, dispatching on its suffix (csv/npy/npz)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv(path)
    if suffix == ".npy":
        return load_npy(path)
    if suffix == ".npz":
        return load_npz(path)
    raise ValueError(f"Unsupported data file '{path.name}' (expected one of "
                     f"{', '.join(DATA_SUFFIXES)})")


def load_two_records(path1: str | Path, path2: str | Path) -> DualBpdData:
    """Load two single-channel files (any supported format) and combine."""
    a = load_record(path1)
    b = load_record(path2)
    n = min(a.n_samples, b.n_samples)
    return DualBpdData(t=a.t[:n], v1=a.v1[:n], v2=b.v1[:n],
                       sample_rate=a.sample_rate,
                       source_files=(Path(path1), Path(path2)))


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


def save_dual_bpd_npy(path: Path, data: "DualBpdData") -> None:
    """Persist a DualBpdData record as a 2- or 3-column ``.npy`` array
    (columns ``t, v1[, v2]``). Round-trips via load_npy."""
    if data.v2 is not None:
        arr = np.column_stack([data.t, data.v1, data.v2])
    else:
        arr = np.column_stack([data.t, data.v1])
    np.save(path, arr)


def save_dual_bpd_npz(path: Path, data: "DualBpdData") -> None:
    """Persist a DualBpdData record as an ``.npz`` archive with named arrays
    ``t_s, C2[, C4]`` (uncompressed — same speed as .npy, ~30–60× faster than
    CSV to load). Round-trips via load_npz."""
    arrays = {"t_s": data.t, "C2": data.v1}
    if data.v2 is not None:
        arrays["C4"] = data.v2
    np.savez(path, **arrays)


def save_record(path: str | Path, data: "DualBpdData") -> None:
    """Persist a DualBpdData record, dispatching on the path suffix
    (csv/npy/npz). Mirror of :func:`load_record`."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        save_dual_bpd_csv(path, data)
    elif suffix == ".npy":
        save_dual_bpd_npy(path, data)
    elif suffix == ".npz":
        save_dual_bpd_npz(path, data)
    else:
        raise ValueError(f"Unsupported save format '{path.name}' (expected one "
                         f"of {', '.join(DATA_SUFFIXES)})")


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
