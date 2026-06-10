"""Diagnostic script: compute MZI delay fiber length and delay time from
acquired data.

Usage
-----
    python scripts/measure_delay.py  <data_file>  [options]

Arguments
---------
data_file       CSV / npy / npz as produced by the app's Save Acquired button.

Options
-------
--n_core FLOAT  Fiber effective index (default 1.468, matches the app).
--search_lo HZ  Lower frequency bound for the FSR search (default 100 kHz).
                The default 500 kHz in the app misses 400 m fibers whose
                FSR ≈ 510 kHz; set this to 100000 to include them safely.
--nperseg INT   Welch nperseg (default 524288 for long records, gives ~1 Hz
                resolution at 1 MSa/s). Larger → sharper dips, more memory.
--plot          Show the PSD and annotated dips with matplotlib.

Purpose
-------
The auto-calibration in the main app (calibrate_mzi) searches from 500 kHz
by default. A 400 m delay fiber has FSR ≈ c / (n·ΔL) ≈ 511 kHz — barely
above that floor, and easily missed. This script lowers the search limit to
100 kHz, lists *all* detected dips, computes ΔL and τ for each one, and
recommends the best candidate.

400 m reference
---------------
    FSR  ≈ 299792458 / (1.468 × 400) ≈ 510 543 Hz  ≈ 0.51 MHz
    τ    ≈ 1 / FSR                                  ≈ 1957 ns
    20 m would give FSR ≈ 10.16 MHz, τ ≈ 98 ns.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make 'app' importable when the script is invoked from any working directory.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import numpy as np
import scipy.signal as sg

from app.data_io import load_record
from app.mzi_calibrate import calibrate_mzi

C_LIGHT = 299_792_458.0


def _run(
    data_file: Path,
    n_core: float = 1.468,
    search_lo: float = 100_000.0,
    nperseg: int = 524_288,
    plot: bool = False,
) -> None:
    print(f"\n{'='*60}")
    print(f"  Delay fiber diagnostic")
    print(f"  File   : {data_file}")
    print(f"  n_core : {n_core}")
    print(f"  search : {search_lo/1e3:.0f} kHz →")
    print(f"{'='*60}\n")

    data = load_record(data_file)
    v = data.v1
    sr = data.sample_rate
    n_samples = v.size
    duration_ms = n_samples / sr * 1e3

    print(
        f"Signal   : {n_samples:,} samples  |  "
        f"{sr/1e6:.3f} MSa/s  |  {duration_ms:.3f} ms"
    )

    # Welch PSD of instantaneous frequency (identical to calibrate_mzi).
    v_f64 = np.asarray(v, dtype=np.float64)
    v_f64 -= v_f64.mean()
    inst_freq = np.diff(np.unwrap(np.angle(sg.hilbert(v_f64)))) * sr / (2 * np.pi)
    carrier = float(inst_freq.mean())
    freq, psd = sg.welch(inst_freq - carrier, fs=sr, nperseg=nperseg)

    print(f"Carrier  : {carrier/1e6:.4f} MHz")
    print(
        f"PSD freq resolution: {freq[1] - freq[0]:.2f} Hz  " f"(nperseg={nperseg:,})\n"
    )

    search_hi = 0.9 * carrier
    band = (freq >= search_lo) & (freq <= search_hi)
    fb, pb = freq[band], psd[band]

    if len(pb) > 51:
        logp_s = sg.savgol_filter(np.log10(pb), 51, 3)
    else:
        logp_s = np.log10(pb)

    dips, props = sg.find_peaks(-logp_s, prominence=0.5)

    print(f"Searched {search_lo/1e6:.3f} – {search_hi/1e6:.3f} MHz")
    print(f"Found {len(dips)} dip(s)\n")

    if not len(dips):
        print("No dips detected. Possible causes:")
        print("  • Signal too noisy — try a longer record.")
        print("  • search_lo is too high — re-run with --search_lo 50000.")
        print("  • Wrong channel — check BPD1 is the heterodyne signal.\n")
        return

    # Reference lengths for orientation
    print(
        f"{'Rank':<5} {'FSR (MHz)':<12} {'τ (ns)':<12} "
        f"{'ΔL (m)':<12} {'Contrast':<10} {'Notes'}"
    )
    print("-" * 65)

    for rank, (dip_idx, prom) in enumerate(zip(dips, props["prominences"]), start=1):
        fsr = float(fb[dip_idx])
        tau_ns = 1e9 / fsr
        dl_m = C_LIGHT / (n_core * fsr)
        contrast = 10**prom

        notes = ""
        if rank == 1:
            notes = "← first dip (used by app auto-cal)"
        if 380 <= dl_m <= 420:
            notes += "  *** matches 400 m ***"
        elif 15 <= dl_m <= 25:
            notes += "  (≈20 m, MATLAB reference length)"

        print(
            f"{rank:<5} {fsr/1e6:<12.4f} {tau_ns:<12.1f} "
            f"{dl_m:<12.2f} {contrast:<10.1f} {notes}"
        )

    print()

    # Best candidate: the dip whose ΔL is closest to round multiples of a
    # base candidate (simplest: the first dip with highest contrast that
    # isn't an obvious harmonic).
    best_dip = dips[0]
    best_fsr = float(fb[best_dip])
    best_dl = C_LIGHT / (n_core * best_fsr)
    best_tau = 1e9 / best_fsr

    # Check for harmonic structure: if any lower dip could be the fundamental
    # (i.e. dip i is near k × dip 0 for integer k), report it.
    harmonics: list[tuple[int, float, float]] = []
    for i, dip_idx in enumerate(dips[1:], start=1):
        f_i = float(fb[dip_idx])
        ratio = f_i / best_fsr
        nearest_k = round(ratio)
        if nearest_k >= 2 and abs(ratio - nearest_k) < 0.05:
            harmonics.append((i + 1, f_i, nearest_k))

    if harmonics:
        print("Harmonic check (dips that look like multiples of dip 1):")
        for rank, fi, k in harmonics:
            print(f"  Dip {rank}  @  {fi/1e6:.4f} MHz  ≈  {k}×  FSR₁")
        print()

    # Diagnosis for the 20 m vs 400 m discrepancy
    app_fsr = best_fsr  # what the app would report (first dip)
    app_dl = best_dl
    print(f"App would report (first-dip rule):")
    print(
        f"  FSR = {app_fsr/1e6:.4f} MHz  →  ΔL ≈ {app_dl:.10f} m  "
        f"(τ = {1e9/app_fsr:.10f} ns)"
    )

    # If the first dip is far from 400 m, look for a lower dip that is.
    if app_dl < 50:
        # The first dip is in the "short fiber" range.  Check if there is a
        # lower-frequency dip consistent with 400 m.
        target_fsr = C_LIGHT / (n_core * 400.0)
        candidates_400 = [
            (float(fb[d]), C_LIGHT / (n_core * float(fb[d])))
            for d in dips
            if abs(float(fb[d]) - target_fsr) / target_fsr < 0.20
        ]
        if candidates_400:
            cf, cdl = candidates_400[0]
            print(f"\n  *** A dip near the expected 400 m FSR was found: ***")
            print(f"      FSR = {cf/1e6:.4f} MHz  →  ΔL ≈ {cdl:.2f} m")
            print(f"      To use this: set Manual FSR = {cf/1e6:.4f} MHz in the app")
            print(f"      (or τ = {1e9/cf:.1f} ns, ΔL = {cdl:.2f} m)\n")
        else:
            _expected = target_fsr / 1e6
            print(
                f"\n  No dip near 400 m FSR ({_expected:.3f} MHz) found "
                f"in the searched band."
            )
            print(f"  Possible reasons:")
            print(
                f"    • The 400 m FSR ({_expected:.3f} MHz) is below "
                f"search_lo ({search_lo/1e6:.3f} MHz)."
            )
            print(f"      Re-run with --search_lo {int(target_fsr*0.5)}")
            print(f"    • The record is too short for the Welch resolution to")
            print(f"      separate {_expected:.3f} MHz from nearby peaks.")
            print(f"    • nperseg may need to be larger: try --nperseg 1048576")
    else:
        print(f"\n  First dip is consistent with ~{app_dl:.0f} m fiber.")

    print()

    if plot:
        _plot(fb, pb, logp_s, dips, props, n_core, search_lo)


def _plot(fb, pb, logp_s, dips, props, n_core, search_lo) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available — cannot plot.")
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.suptitle("MZI delay diagnostic — instantaneous-frequency PSD", fontsize=13)

    ax1.semilogy(
        fb / 1e6, pb, color="#007AFF", linewidth=0.8, alpha=0.7, label="PSD (Welch)"
    )
    if len(dips):
        ax1.semilogy(
            fb[dips] / 1e6,
            pb[dips],
            "v",
            color="#FF3B30",
            markersize=8,
            label="Dips (MZI zeros)",
        )
        for i, (di, prom) in enumerate(zip(dips, props["prominences"]), 1):
            fsr = float(fb[di])
            dl = C_LIGHT / (n_core * fsr)
            ax1.annotate(
                f"#{i}\n{fsr/1e6:.3f} MHz\n{dl:.1f} m",
                xy=(fb[di] / 1e6, pb[di]),
                xytext=(fb[di] / 1e6, pb[di] * 3),
                fontsize=7,
                ha="center",
                arrowprops=dict(arrowstyle="-", color="#333"),
            )

    ax1.set_ylabel("PSD  (arbitrary)", fontsize=10)
    ax1.legend(fontsize=9)
    ax1.grid(True, which="both", alpha=0.25)

    ax2.plot(
        fb / 1e6, logp_s, color="#34C759", linewidth=0.9, label="log₁₀(PSD) smoothed"
    )
    if len(dips):
        ax2.plot(fb[dips] / 1e6, logp_s[dips], "v", color="#FF3B30", markersize=8)
    ax2.set_xlabel("Frequency  (MHz)", fontsize=10)
    ax2.set_ylabel("log₁₀(PSD)  smoothed", fontsize=10)
    ax2.legend(fontsize=9)
    ax2.grid(True, which="both", alpha=0.25)

    for ax in (ax1, ax2):
        ax.axvline(
            search_lo / 1e6,
            color="#FF9500",
            linewidth=1,
            linestyle=":",
            alpha=0.7,
            label="search_lo",
        )

    fig.tight_layout()
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute MZI delay fiber ΔL and τ from acquired data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("data_file", type=Path, help="CSV / npy / npz data file.")
    parser.add_argument(
        "--n_core",
        type=float,
        default=1.468,
        help="Fiber effective index (default 1.468).",
    )
    parser.add_argument(
        "--search_lo",
        type=float,
        default=100_000.0,
        help="Lower FSR search bound in Hz (default 100000).",
    )
    parser.add_argument(
        "--nperseg",
        type=int,
        default=524_288,
        help="Welch segment length (default 524288).",
    )
    parser.add_argument(
        "--plot", action="store_true", help="Display annotated PSD plot."
    )
    args = parser.parse_args()

    _run(
        data_file=args.data_file,
        n_core=args.n_core,
        search_lo=args.search_lo,
        nperseg=args.nperseg,
        plot=args.plot,
    )


if __name__ == "__main__":
    main()
