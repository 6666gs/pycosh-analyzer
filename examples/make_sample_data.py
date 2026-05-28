"""Regenerate examples/sample_data.csv from a full-resolution recording.

This script is committed so the sample CSV can be reproduced or refreshed
from new measurement data without leaving a binary trail.

Default: read the original 2 GSa/s × 10 ms recording at
`/Users/x/python/sds7404/sin_noise.csv`, decimate to 250 MSa/s with a
proper anti-alias filter, trim to the first 100,000 samples (= 400 µs),
and save as `examples/sample_data.csv` with 6 significant figures.

Adjust SOURCE_CSV / DECIM_FACTOR / N_KEEP at the top if you want a
different example.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import decimate

SOURCE_CSV = Path("/Users/x/python/sds7404/sin_noise.csv")
OUT_CSV = Path(__file__).resolve().parent / "sample_data.csv"

DECIM_FACTOR = 8     # 2 GSa/s → 250 MSa/s (Nyquist 125 MHz, safe for 80 MHz AOM)
N_KEEP = 100_000     # 400 µs at 250 MSa/s


def main() -> None:
    if not SOURCE_CSV.exists():
        raise SystemExit(f"Source CSV not found: {SOURCE_CSV}")

    print(f"Loading {SOURCE_CSV} …")
    df = pd.read_csv(SOURCE_CSV)
    t = df.iloc[:, 0].to_numpy(dtype=np.float64)
    v1 = df.iloc[:, 1].to_numpy(dtype=np.float64)
    v2 = df.iloc[:, 2].to_numpy(dtype=np.float64)
    sr_in = 1.0 / float(np.median(np.diff(t[:1000])))
    print(f"  {t.size:,} samples at {sr_in/1e6:.0f} MSa/s")

    print(f"Decimating ×{DECIM_FACTOR} (anti-aliased) …")
    v1d = decimate(v1, DECIM_FACTOR, ftype="iir", zero_phase=True)
    v2d = decimate(v2, DECIM_FACTOR, ftype="iir", zero_phase=True)

    n = min(N_KEEP, v1d.size)
    sr_out = sr_in / DECIM_FACTOR
    t_out = np.arange(n) / sr_out
    v1_out = v1d[:n]
    v2_out = v2d[:n]
    duration_us = n / sr_out * 1e6
    print(f"  kept {n:,} samples at {sr_out/1e6:.0f} MSa/s "
          f"({duration_us:.1f} µs total)")

    out = pd.DataFrame({"t_s": t_out, "C2": v1_out, "C4": v2_out})
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False, float_format="%.6e")
    print(f"Wrote {OUT_CSV}  ({OUT_CSV.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
