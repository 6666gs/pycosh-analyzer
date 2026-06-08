"""Unit tests for live-monitoring persistence (per-cycle spectra + trend)."""
import numpy as np

from app.monitor_io import TREND_FILENAME, MonitorRecorder, save_cycle_spectrum
from app.processor import ProcessRequest, ProcessResult


def _result() -> ProcessResult:
    freq = np.array([10.0, 100.0, 1000.0])
    gfilter = np.full(3, 2.0)
    psd = np.array([1e2, 1e1, 1e0])
    req = ProcessRequest(
        v1=np.zeros(8), v2=None, sample_rate=1e9, delay_freq=1e6,
        bw_segment=(1e3, 1e4), offset_start_ratio=10,
        range_start=None, range_stop=None,
    )
    return ProcessResult(
        freq=freq, gfilter=gfilter,
        psd11=psd, psd11_err=psd / 10.0,
        psd22=psd, psd22_err=psd / 10.0,
        psd12=psd, psd12_err=psd / 10.0,
        request=req,
    )


def test_record_writes_spectrum_npz_and_trend(tmp_path):
    rec = MonitorRecorder(tmp_path)

    path = rec.record(_result(), elapsed=1.5, lorentz_fwhm=1234.0,
                      beta_fwhm=5678.0, stamp="120000")

    assert path.exists() and path.suffix == ".npz"
    assert path.name == "spectrum_0001_120000.npz"
    assert rec.count == 1

    with np.load(path) as d:
        assert "frequency_Hz" in d
        np.testing.assert_allclose(d["S_nu_cross_Hz2_per_Hz"], _result().s_nu_12)
        np.testing.assert_allclose(d["S_phi_cross_rad2_per_Hz"], _result().s_phi_12)

    with np.load(rec.trend_path) as d:
        np.testing.assert_allclose(d["elapsed_s"], [1.5])
        np.testing.assert_allclose(d["lorentz_fwhm_hz"], [1234.0])
        np.testing.assert_allclose(d["beta_fwhm_hz"], [5678.0])


def test_trend_accumulates_and_stores_nan_for_missing_fits(tmp_path):
    rec = MonitorRecorder(tmp_path)
    rec.record(_result(), 1.0, 100.0, 200.0)
    rec.record(_result(), 2.0, 110.0, 210.0)
    rec.record(_result(), 3.0, None, None)  # no clean fit this cycle

    assert rec.count == 3
    with np.load(rec.trend_path) as d:
        np.testing.assert_allclose(d["elapsed_s"], [1.0, 2.0, 3.0])
        np.testing.assert_allclose(d["lorentz_fwhm_hz"][:2], [100.0, 110.0])
        assert np.isnan(d["lorentz_fwhm_hz"][2])
        assert np.isnan(d["beta_fwhm_hz"][2])

    specs = sorted(tmp_path.glob("spectrum_*.npz"))
    assert [p.name for p in specs] == [
        "spectrum_0001.npz", "spectrum_0002.npz", "spectrum_0003.npz",
    ]


def test_recorder_creates_missing_directory(tmp_path):
    target = tmp_path / "run" / "nested"
    rec = MonitorRecorder(target)
    assert target.is_dir()
    rec.record(_result(), 0.0, 1.0, 2.0)
    assert (target / TREND_FILENAME).exists()


def test_save_cycle_spectrum_standalone(tmp_path):
    out = tmp_path / "one.npz"
    save_cycle_spectrum(out, _result())
    with np.load(out) as d:
        np.testing.assert_allclose(d["frequency_Hz"], _result().freq)
