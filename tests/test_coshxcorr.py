"""Unit tests for the parallel CPU / auto-GPU CoshXcorr implementation.

Guards two things the refactor must preserve:
1. The parallel ``process()`` is numerically identical to a plain serial
   reference computed straight from the config geometry.
2. ``process_gpu()`` degrades to the CPU path when no CUDA device exists
   (the common case on Mac / iGPU / AMD-on-Windows machines).
"""
import numpy as np
import scipy.signal
import pytest

from app.processor import ensure_pycosh_importable

ensure_pycosh_importable()
from pycosh import CoshConfig, CoshXcorr  # type: ignore  # noqa: E402

from conftest import synthetic_beat  # noqa: E402


def _serial_reference(trace1, trace2, cfg):
    """Plain single-threaded reimplementation of the per-band math, used as
    ground truth for the threaded ``process()``."""
    def _phase(tr):
        tr = np.asarray(tr, dtype=np.float64)
        return np.mod(np.diff(np.angle(scipy.signal.hilbert(tr - np.mean(tr)))), 2 * np.pi)

    pc1 = _phase(trace1)
    pc2 = _phase(trace2)
    scale_base = 1.0 / np.power(2 * np.pi * cfg.time_unit, 2)
    psd11, psd12, freq = [], [], []
    for ii, bw in enumerate(cfg.bw_segment[:-1]):
        seg_len = int(np.round(1 / (bw * cfg.time_unit)))
        seg_cnt = int(np.floor(len(pc1) / seg_len)) if seg_len else 0
        if seg_cnt < 1:
            continue
        bw_next = cfg.bw_segment[ii + 1]
        op = list(range(cfg.offset_start_ratio,
                        int(np.round(cfg.offset_start_ratio * bw_next / bw))))
        if not op:
            continue
        n = seg_cnt * seg_len
        f1 = np.fft.fft(pc1[:n].reshape(seg_cnt, seg_len)) / seg_len
        f2 = np.fft.fft(pc2[:n].reshape(seg_cnt, seg_len)) / seg_len
        scale = scale_base / bw
        psd11.append(np.mean(f1[:, op] * np.conj(f1[:, op]) * scale, axis=0))
        psd12.append(np.mean(f1[:, op] * np.conj(f2[:, op]) * scale, axis=0))
        freq.append(np.array(cfg.offset_freq_list[ii]))
    return (np.concatenate(psd11), np.concatenate(psd12), np.concatenate(freq))


def _config(sr):
    # Bands chosen so several fit inside the 4096-sample synthetic beat.
    return CoshConfig(delay_freq=1e5, bw_segment=[1e3, 1e4, 5e4],
                      sample_rate=sr, offset_start_ratio=10)


def test_parallel_process_matches_serial_reference():
    _, v1, v2, sr = synthetic_beat()
    cfg = _config(sr)
    cosh = CoshXcorr(trace1=v1, trace2=v2, config=cfg)
    cosh.process(print_progress=False)

    ref11, ref12, ref_freq = _serial_reference(v1, v2, cfg)
    np.testing.assert_allclose(cosh.freq_list, ref_freq)
    np.testing.assert_allclose(np.abs(cosh.psd11), np.abs(ref11), rtol=1e-9, atol=1e-30)
    np.testing.assert_allclose(np.abs(cosh.psd12), np.abs(ref12), rtol=1e-9, atol=1e-30)
    assert cosh.psd12.shape == cosh.freq_list.shape


def test_autocorrelation_gives_equal_channels():
    _, v1, _, sr = synthetic_beat()
    cosh = CoshXcorr(trace1=v1, trace2=v1, config=_config(sr))
    cosh.process(print_progress=False)
    # Identical traces → psd11 == psd22 == |psd12|, all finite.
    np.testing.assert_allclose(cosh.psd11, cosh.psd22)
    np.testing.assert_allclose(np.abs(cosh.psd12), cosh.psd11, rtol=1e-9)
    assert np.all(np.isfinite(cosh.psd11))


def test_short_data_skips_bands_without_nans():
    # 600 samples: the 1 kHz band needs 1000-sample segments → skipped,
    # while the 10 kHz band (100-sample segments) survives.
    _, v1, v2, sr = synthetic_beat(n=600)
    cfg = CoshConfig(delay_freq=1e5, bw_segment=[1e3, 1e4, 5e4],
                     sample_rate=sr, offset_start_ratio=10)
    cosh = CoshXcorr(trace1=v1, trace2=v2, config=cfg)
    cosh.process(print_progress=False)
    assert cosh.freq_list.size > 0
    assert np.all(np.isfinite(cosh.psd11))
    assert cosh.psd11.shape == cosh.freq_list.shape


def test_all_bands_too_short_raises():
    _, v1, v2, sr = synthetic_beat(n=50)
    cosh = CoshXcorr(trace1=v1, trace2=v2, config=_config(sr))
    with pytest.raises(RuntimeError):
        cosh.process(print_progress=False)


def test_process_gpu_falls_back_to_cpu_without_torch():
    # On this machine torch/CUDA is absent, so process_gpu must produce the
    # same result as the CPU process().
    _, v1, v2, sr = synthetic_beat()
    cfg = _config(sr)
    a = CoshXcorr(trace1=v1, trace2=v2, config=cfg)
    a.process_gpu(print_progress=False)
    b = CoshXcorr(trace1=v1, trace2=v2, config=cfg)
    b.process(print_progress=False)
    np.testing.assert_allclose(a.psd12, b.psd12)
    np.testing.assert_allclose(a.freq_list, b.freq_list)
