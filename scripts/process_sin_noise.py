"""
用 pycosh 处理双 BPD 测量数据 (sin_noise.csv)。

数据格式: 单 CSV, 3 列 (t_s, C2, C4)
    - C2 → BPD1 输出
    - C4 → BPD2 输出

输出: 终端打印分析进度 + 弹出图窗:
    1. 原始两路波形对齐预览 (头部 200 点)
    2. 单 BPD PSD (psd11, psd22) vs 互相关 PSD (psd12), 用于直观看 BPD 噪声压制
    3. SSB 频率噪声 S_nu(f) (经 G(f) 补偿)

注意: 修改顶部 PARAMETERS 区域以匹配你的实际光路 (延迟光纤长度等)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# --- 让脚本能找到 vendored 的 pycosh ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENDOR = _REPO_ROOT / "vendor"
if (_VENDOR / "pycosh").exists():
    sys.path.insert(0, str(_VENDOR))
from pycosh import CoshConfig, CoshXcorr  # noqa: E402

# ============================ PARAMETERS ============================
CSV_PATH = _REPO_ROOT / "sin_noise.csv"

# 光路参数 —— 改成你这次测量实际用的值
DELAY_LEN_M = 10.0  # 延迟光纤长度 (m)
N_CORE = 1.468  # 光纤有效折射率

# 数据处理参数
# 最低分析频点 = bw_segment[0] * offset_start_ratio
# 物理下限: bw_segment[0] 必须 >> 1/总时长, 否则段数过少 → 抖动巨大
# 当前组合: 1e3 * 10 = 10 kHz 起算; 9 段, 1:3 比例, 段间台阶最平滑
BW_SEGMENT = [1e3, 3e3, 1e4, 3e4, 1e5, 3e5, 1e6, 3e6, 1e7]
OFFSET_START_RATIO = 10

# 可选裁剪输入数据 (None = 全部使用)
RANGE_START: int | None = None
RANGE_STOP: int | None = None

USE_GPU = False  # 有 CUDA 时可设为 True
# ===================================================================


def load_dual_bpd_csv(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """加载示波器导出的 3 列 CSV: 时间, BPD1, BPD2。"""
    df = pd.read_csv(path)
    t = df.iloc[:, 0].to_numpy(dtype=np.float64)
    v1 = df.iloc[:, 1].to_numpy(dtype=np.float64)
    v2 = df.iloc[:, 2].to_numpy(dtype=np.float64)
    return t, v1, v2


def estimate_sample_rate(t: np.ndarray) -> float:
    """从时间列估算采样率 (Hz)。"""
    # 用中位数避免起点/尾点的小数误差影响
    dt = float(np.median(np.diff(t[:1000])))
    return 1.0 / dt


def preview_waveforms(
    t: np.ndarray, v1: np.ndarray, v2: np.ndarray, n_points: int = 200
) -> None:
    """快速看一眼两路波形是否对齐 (同相/反相直接看出来)。"""
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(
        t[:n_points] * 1e6,
        v1[:n_points] - v1[:n_points].mean(),
        label="BPD1 (C2)",
        alpha=0.8,
    )
    ax.plot(
        t[:n_points] * 1e6,
        v2[:n_points] - v2[:n_points].mean(),
        label="BPD2 (C4)",
        alpha=0.8,
        linestyle="--",
    )
    ax.set_xlabel("Time (us)")
    ax.set_ylabel("Voltage (DC removed, V)")
    ax.set_title(f"Raw waveform preview (first {n_points} samples)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()


def check_phase_relation(cosh: CoshXcorr) -> None:
    """看互相关复数相位/实部符号, 判断两路是同相还是反相。"""
    psd12_mean = complex(np.mean(cosh.psd12))
    angle_deg = np.degrees(np.angle(psd12_mean))
    real_sign = "+" if psd12_mean.real > 0 else "-"
    print(
        f"\n[相位关系自检] mean(psd12) = {psd12_mean:.3e}\n"
        f"    复角 ≈ {angle_deg:+.1f}°, 实部符号 = {real_sign}\n"
        f"    → 接近 0° 实部为正 = 同相; 接近 ±180° 实部为负 = 反相\n"
    )


def plot_psds(cosh: CoshXcorr) -> None:
    """三张诊断图: 单 BPD PSD 对比, S_phi, S_nu。"""
    freq = cosh.freq_list
    gfilter = cosh.freq_filter  # = 4 sin^2(pi f tau), 用于 G(f) 补偿

    s_nu_11 = np.abs(cosh.psd11) / gfilter
    s_nu_22 = np.abs(cosh.psd22) / gfilter
    s_nu_12 = np.abs(cosh.psd12) / gfilter
    s_nu_12_err = np.abs(cosh.psd12_err) / gfilter
    s_phi_12 = s_nu_12 / freq**2
    s_phi_12_err = s_nu_12_err / freq**2

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Left: single-BPD vs cross-correlated (shows BPD-noise suppression)
    ax1.loglog(freq, s_nu_11, label=r"$S_{\nu,11}$ (BPD1 auto-PSD)", alpha=0.7)
    ax1.loglog(freq, s_nu_22, label=r"$S_{\nu,22}$ (BPD2 auto-PSD)", alpha=0.7)
    ax1.loglog(
        freq,
        s_nu_12,
        label=r"$S_{\nu,12}$ (cross-correlation)",
        color="k",
        linewidth=1.5,
    )
    ax1.set_xlabel("Offset frequency (Hz)")
    ax1.set_ylabel(r"$S_\nu$ (Hz$^2$/Hz)")
    ax1.set_title("Single-BPD vs cross-correlated frequency noise PSD")
    ax1.grid(True, which="both", alpha=0.3)
    ax1.legend()

    # 右: 互相关结果 (S_nu 主曲线 + S_phi 副 y 轴)
    ax2.loglog(freq, s_nu_12, label=r"$S_\nu$ (cross)", color="C0", linewidth=1.5)
    ax2.fill_between(
        freq,
        np.clip(s_nu_12 - s_nu_12_err, 1e-30, None),
        s_nu_12 + s_nu_12_err,
        alpha=0.3,
        color="C0",
    )
    ax2.set_xlabel("Offset frequency (Hz)")
    ax2.set_ylabel(r"$S_\nu$ (Hz$^2$/Hz)", color="C0")
    ax2.tick_params(axis="y", labelcolor="C0")
    ax2.grid(True, which="both", alpha=0.3)
    ax2.set_title("Final noise spectrum after cross-correlation")

    ax2b = ax2.twinx()
    ax2b.loglog(
        freq, s_phi_12, color="C3", linewidth=1.0, linestyle="--", label=r"$S_\varphi$"
    )
    ax2b.fill_between(
        freq,
        np.clip(s_phi_12 - s_phi_12_err, 1e-30, None),
        s_phi_12 + s_phi_12_err,
        alpha=0.2,
        color="C3",
    )
    ax2b.set_ylabel(r"$S_\varphi$ (rad$^2$/Hz)", color="C3")
    ax2b.tick_params(axis="y", labelcolor="C3")

    fig.tight_layout()


def main() -> None:
    print(f"读取数据: {CSV_PATH}")
    t, v1, v2 = load_dual_bpd_csv(CSV_PATH)
    print(f"    点数 = {len(t):,}")

    sample_rate = estimate_sample_rate(t)
    total_time = t[-1] - t[0]
    print(
        f"    采样率 ≈ {sample_rate / 1e6:.3f} MSa/s "
        f"({sample_rate / 1e9:.3f} GSa/s)"
    )
    print(f"    总时长 ≈ {total_time * 1e3:.3f} ms")
    print(f"    BPD1 均值/RMS = {v1.mean():+.3f} / {v1.std():.3f} V")
    print(f"    BPD2 均值/RMS = {v2.mean():+.3f} / {v2.std():.3f} V")

    preview_waveforms(t, v1, v2)

    # 计算延迟线 FSR
    c = 299_792_458.0
    tau = N_CORE * DELAY_LEN_M / c
    delay_freq = 1.0 / tau
    print(
        f"\n光路: 延迟 {DELAY_LEN_M} m × n={N_CORE} → "
        f"τ = {tau * 1e9:.2f} ns, FSR = {delay_freq / 1e6:.3f} MHz"
    )

    # 配置 + 跑 pycosh
    config = CoshConfig(
        delay_freq=delay_freq,
        bw_segment=BW_SEGMENT,
        sample_rate=sample_rate,
        offset_start_ratio=OFFSET_START_RATIO,
        range_start=RANGE_START,
        range_stop=RANGE_STOP,
    )
    cosh = CoshXcorr(trace1=v1, trace2=v2, config=config)
    if USE_GPU:
        cosh.process_gpu(print_progress=True)
    else:
        cosh.process(print_progress=True)

    check_phase_relation(cosh)
    plot_psds(cosh)
    plt.show()


if __name__ == "__main__":
    main()
