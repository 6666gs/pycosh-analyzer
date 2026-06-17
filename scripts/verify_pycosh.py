"""
Numerical verification of pycosh: feed simulated white frequency noise
through an analytic self-heterodyne model, check that the recovered
S_nu(f) matches the input (flat) level.

Pass criterion: |recovered / input - 1| within ~10% in well-sampled bands.
"""
from __future__ import annotations
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# --- 让脚本能找到 vendored 的 pycosh ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENDOR = _REPO_ROOT / "vendor"
if (_VENDOR / "pycosh").exists():
    sys.path.insert(0, str(_VENDOR))
from pycosh import CoshConfig, CoshXcorr  # noqa: E402


def simulate_self_heterodyne(
    s_nu_true: float,
    sample_rate: float,
    duration: float,
    f_carrier: float,
    tau: float,
    bpd_noise_std: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build V(t) = cos(2 pi f_c t + phi(t) - phi(t - tau)) with white
    frequency noise of two-sided PSD s_nu_true (Hz^2/Hz). Returns two
    independent-BPD-noise traces."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / sample_rate
    n_tau = int(round(tau / dt))
    n_samples = int(sample_rate * duration)
    n_total = n_samples + n_tau

    # White freq noise: per-sample variance = s_nu_true * sample_rate
    # (so that two-sided PSD = variance / sample_rate = s_nu_true)
    nu = rng.standard_normal(n_total) * np.sqrt(s_nu_true * sample_rate)
    phi = 2.0 * np.pi * np.cumsum(nu) * dt

    t = np.arange(n_samples) * dt
    delta_phi = phi[n_tau:] - phi[:-n_tau]
    base = np.cos(2.0 * np.pi * f_carrier * t + delta_phi)

    n1 = rng.standard_normal(n_samples) * bpd_noise_std
    n2 = rng.standard_normal(n_samples) * bpd_noise_std
    return base + n1, base + n2


def run_one_test(s_nu_true: float, ax=None, label=None) -> dict:
    sample_rate = 1e9
    duration = 5e-3
    f_carrier = 55e6
    n_core = 1.468
    delay_len = 10.0
    tau = n_core * delay_len / 299_792_458.0

    v1, v2 = simulate_self_heterodyne(
        s_nu_true, sample_rate, duration, f_carrier, tau,
        bpd_noise_std=0.02,
    )

    config = CoshConfig(
        delay_freq=1.0 / tau,
        bw_segment=[1e4, 3e4, 1e5, 3e5, 1e6, 3e6, 1e7],
        sample_rate=sample_rate,
        offset_start_ratio=20,
    )
    cosh = CoshXcorr(trace1=v1, trace2=v2, config=config)
    cosh.process(print_progress=False)

    freq = cosh.freq_list
    s_nu_meas = np.abs(cosh.psd12) / cosh.freq_filter

    # Restrict to "clean" middle band (avoid edges and Nyquist tail)
    mask = (freq > 5e4) & (freq < 5e7)
    ratio = np.median(s_nu_meas[mask]) / s_nu_true

    if ax is not None:
        ax.loglog(freq, s_nu_meas, label=f"{label} (meas)", alpha=0.7)
        ax.axhline(s_nu_true, linestyle="--", alpha=0.5,
                   label=f"{label} (input)")

    return {
        "input": s_nu_true,
        "measured_median": float(np.median(s_nu_meas[mask])),
        "ratio": float(ratio),
    }


def main() -> None:
    test_levels = [1e3, 1e5, 1e7]
    print("Numerical verification of pycosh (white frequency noise)")
    print("=" * 60)

    fig, ax = plt.subplots(figsize=(10, 6))
    results = []
    for s0 in test_levels:
        r = run_one_test(s0, ax=ax, label=f"S0={s0:.0e}")
        results.append(r)
        print(f"  input S_nu = {r['input']:.3e}  Hz^2/Hz  ->  "
              f"measured median = {r['measured_median']:.3e}  "
              f"(ratio = {r['ratio']:.3f})")

    ax.set_xlabel("Offset frequency (Hz)")
    ax.set_ylabel(r"$S_\nu$ (Hz$^2$/Hz)")
    ax.set_title("pycosh verification: recovered vs input white noise level")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()

    print("\nPass criterion: ratio ~ 1.0 (typically 0.9 - 1.1)")
    print("All ratios:", [f"{r['ratio']:.3f}" for r in results])
    plt.show()


if __name__ == "__main__":
    main()
