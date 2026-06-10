"""Unit tests for multi-record frequency-noise averaging (Plot_Linewidth port)."""
import numpy as np
import pandas as pd
import pytest

from app.averaging import (
    AveragedResult,
    PsdAverager,
    average_records,
    even_checkpoints,
    save_averaged,
)


def _record(n: int = 4096, sr: float = 1e6, fc: float = 1e5, seed: int = 0):
    """A self-heterodyne beat with a little random-walk phase noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    phase = 2 * np.pi * fc * t + 0.01 * np.cumsum(rng.standard_normal(n))
    return np.sin(phase)


def test_average_records_shapes_and_linewidth_relation():
    recs = [_record(seed=i) for i in range(5)]
    res = average_records(recs, sample_rate=1e6, fsr_hz=1e6,
                          n_skip=10, fmax=4e5)

    n_trim = 4096 - 2 * 10
    assert res.freq.size == n_trim // 2 + 1
    assert res.s_nu.shape == res.freq.shape
    assert res.n_avg == 5
    assert np.isfinite(res.floor_hz2_per_hz) and res.floor_hz2_per_hz > 0
    # Di Domenico: FWHM = π · S₀
    assert res.linewidth_hz == pytest.approx(np.pi * res.floor_hz2_per_hz)
    # S_phi = S_nu / f² where f > 0
    pos = res.freq > 0
    np.testing.assert_allclose(res.s_phi[pos],
                               res.s_nu[pos] / res.freq[pos] ** 2, rtol=1e-9)


def test_incremental_averager_matches_batch():
    recs = [_record(seed=i) for i in range(4)]
    batch = average_records(recs, 1e6, 1e6, n_skip=10, fmax=4e5)

    avg = PsdAverager(n_skip=10)
    for r in recs:
        avg.add(r)
    incr = avg.result(1e6, 1e6, fmax=4e5)

    assert incr.n_avg == 4
    np.testing.assert_allclose(incr.s_nu, batch.s_nu, rtol=1e-12)


def test_averaging_smooths_the_spectrum():
    # Averaging N independent power spectra cuts the per-bin variance ~1/N, so
    # the bin-to-bin scatter of the spectrum shrinks — the whole point of the
    # method (a cleaner spectrum → a more reliable noise floor).
    recs = [_record(seed=i) for i in range(8)]
    one = average_records(recs[:1], 1e6, 1e6, n_skip=10, fmax=4e5)
    many = average_records(recs, 1e6, 1e6, n_skip=10, fmax=4e5)
    band = (one.freq > 1e4) & (one.freq < 1e5)
    assert np.std(np.log10(many.s_nu[band])) < np.std(np.log10(one.s_nu[band]))


def test_record_too_short_to_trim_raises():
    avg = PsdAverager(n_skip=3000)
    with pytest.raises(ValueError):
        avg.add(_record(n=4096))


def test_mismatched_record_lengths_raise():
    avg = PsdAverager(n_skip=10)
    avg.add(_record(n=4096))
    with pytest.raises(ValueError):
        avg.add(_record(n=2048))


def test_even_checkpoints():
    assert even_checkpoints(10) == [2, 4, 6, 8, 10]
    assert even_checkpoints(5, 5) == [1, 2, 3, 4, 5]
    assert even_checkpoints(1) == [1]
    assert even_checkpoints(100, 5) == [20, 40, 60, 80, 100]


def _make_result() -> AveragedResult:
    return average_records([_record(seed=i) for i in range(3)],
                           1e6, 1e6, n_skip=10, fmax=4e5)


def test_save_averaged_csv_round_trips(tmp_path):
    res = _make_result()
    out = tmp_path / "avg.csv"
    save_averaged(out, res)

    text = out.read_text()
    assert f"# n_avg: {res.n_avg}" in text
    assert "# linewidth_Hz:" in text
    df = pd.read_csv(out, comment="#")
    assert list(df.columns) == ["frequency_Hz", "S_nu_Hz2_per_Hz", "S_phi_rad2_per_Hz"]
    np.testing.assert_allclose(df["S_nu_Hz2_per_Hz"].to_numpy(), res.s_nu)


def test_save_averaged_npz_round_trips(tmp_path):
    res = _make_result()
    out = tmp_path / "avg.npz"
    save_averaged(out, res)
    with np.load(out) as d:
        np.testing.assert_allclose(d["S_nu_Hz2_per_Hz"], res.s_nu)
        assert int(d["n_avg"]) == res.n_avg
        assert np.isclose(d["linewidth_hz"], res.linewidth_hz)


def test_save_averaged_rejects_unknown_suffix(tmp_path):
    with pytest.raises(ValueError):
        save_averaged(tmp_path / "avg.txt", _make_result())
