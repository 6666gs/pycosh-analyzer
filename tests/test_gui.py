"""Offscreen GUI smoke tests.

Qt runs headless — conftest.py sets QT_QPA_PLATFORM=offscreen. These cover the
wiring changes: error-band default, SSB title + β-subtitle gating, combined
export, and auto-process on calibration success.
"""
import numpy as np
import pandas as pd

from app.analysis import integrate_beta
from app.plot_widget import DisplayOptions, SpectrumPlot
from app.processor import ProcessRequest, ProcessResult


def _make_result(v2=None) -> ProcessResult:
    freq = np.array([1.0, 10.0, 100.0, 1000.0])
    gfilter = np.full(4, 2.0)
    psd = np.array([1e3, 1e2, 1e1, 1e0])
    req = ProcessRequest(
        v1=np.zeros(16), v2=v2, sample_rate=1e9, delay_freq=1e6,
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


def test_error_band_defaults_off(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    assert win.settings.display.c_errband.isChecked() is False


def test_title_uses_ssb_and_beta_subtitle_follows_checkbox(qtbot):
    plot = SpectrumPlot()
    qtbot.addWidget(plot)
    r = _make_result()
    beta = integrate_beta(r.freq, r.s_nu_12)
    assert beta is not None

    base = dict(
        noise_type="frequency", show_bpd1=False, show_bpd2=False,
        show_cross=True, show_errorband=False, show_lorentz_floor=False,
        lorentz_fit=None, beta_fit=beta,
    )

    plot.render(r, DisplayOptions(show_beta_line=False, **base))
    title_off = plot._ax.get_title()
    assert "SSB" in title_off
    assert "β-integrated" not in title_off

    plot.render(r, DisplayOptions(show_beta_line=True, **base))
    assert "β-integrated" in plot._ax.get_title()


def test_export_writes_both_frequency_and_phase_spectra(qtbot, tmp_path, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win._result = _make_result(v2=np.zeros(16))
    out = tmp_path / "spec.csv"

    class _FakeDialog:
        @staticmethod
        def getSaveFileName(*args, **kwargs):
            return (str(out), "CSV (*.csv)")

    monkeypatch.setattr("app.main_window.QFileDialog", _FakeDialog)
    win._export()

    df = pd.read_csv(out, comment="#")
    cols = df.columns.tolist()
    assert "S_nu_cross_Hz2_per_Hz" in cols
    assert "S_phi_cross_rad2_per_Hz" in cols
    assert "S_nu_BPD2_Hz2_per_Hz" in cols  # second channel present
    assert "# spectrum_convention: single-sideband (SSB)" in out.read_text()


def test_calibration_success_auto_processes(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    calls = []
    monkeypatch.setattr(win, "_start_process", lambda: calls.append(True))

    class _Res:
        fsr_hz = 214e3
        reliable = True
        delta_L_m = 1.0
        contrast = 50.0
        carrier_hz = 80e6

    win._on_calibrate_ok(_Res())
    assert calls == [True]


def test_trend_plot_renders_points(qtbot):
    from app.plot_widget import TrendPlot

    trend = TrendPlot()
    qtbot.addWidget(trend)
    trend.update_trend([0.0, 1.5, 3.0], [1.2e4, 9.0e3, float("nan")])

    title = trend._ax.get_title()
    assert "FWHM" in title
    # one Line2D drawn for the trend
    assert len(trend._ax.get_lines()) >= 1


def test_trend_plot_clear(qtbot):
    from app.plot_widget import TrendPlot

    trend = TrendPlot()
    qtbot.addWidget(trend)
    trend.update_trend([0.0, 1.0], [1e4, 2e4])
    trend.clear()
    assert len(trend._ax.get_lines()) == 0
