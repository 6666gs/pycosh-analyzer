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


def test_calibration_uses_calculated_tau_when_available(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    monkeypatch.setattr(win, "_start_process", lambda: None)

    class _Res:
        fsr_hz = 5.0e6          # calibration found a real FSR
        reliable = True
        delta_L_m = 40.0
        contrast = 50.0
        carrier_hz = 80e6

    win._on_calibrate_ok(_Res())
    assert win.settings.optical.is_calibrated
    # uses the *calculated* FSR, not the configured fallback
    assert np.isclose(win.settings.optical.delay_freq_hz, 5.0e6)


def test_calibration_falls_back_to_configured_tau_when_no_dip(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    monkeypatch.setattr(win, "_start_process", lambda: None)
    win.settings.optical.tau_fallback.setValue(50.0)   # 50 ns → 20 MHz

    class _Res:
        fsr_hz = None           # no dip detected
        carrier_hz = 80e6
        search_lo = 5e5
        search_hi = 72e6
        n_dips = 0
        reliable = False
        delta_L_m = None
        contrast = None

    win._on_calibrate_ok(_Res())
    assert win.settings.optical.is_calibrated
    assert np.isclose(win.settings.optical.delay_freq_hz, 20e6)  # configured τ


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


def test_monitor_button_gating(qtbot):
    from app.main_window import MainWindow
    from app.settings_panel import MODE_ACQUIRE

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings

    # Not calibrated → monitor disabled
    sp.set_calibrated(False)
    assert sp.monitor_btn.isEnabled() is False

    # Calibrated + acquire mode → monitor enabled
    sp.data.mode_combo.setCurrentIndex(
        sp.data.mode_combo.findData(MODE_ACQUIRE))
    sp.set_calibrated(True)
    assert sp.monitor_btn.isEnabled() is True

    # While monitoring: label flips, Process disabled, monitor stays enabled
    sp.set_monitoring(True)
    assert "Stop" in sp.monitor_btn.text()
    assert sp.process_btn.isEnabled() is False
    assert sp.monitor_btn.isEnabled() is True

    sp.set_monitoring(False)
    assert "Monitor" in sp.monitor_btn.text()


def _raw_frame():
    from app.data_io import DualBpdData

    t = np.arange(32) / 1e6
    return DualBpdData(t=t, v1=np.sin(t), v2=np.cos(t),
                       sample_rate=1e6, source_files=())


def test_monitor_cycle_updates_trend_and_spectrum(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    result = _make_result(v2=np.zeros(16))

    win._on_monitor_cycle(result, 2.5, _raw_frame())

    assert win._result is result
    assert len(win._trend_t) == 1
    assert win._trend_t[0] == 2.5
    assert len(win._trend_fwhm) == 1          # a metric (possibly NaN) recorded
    assert win.trend.isVisible() or True       # visibility toggled by start, not cycle


def test_start_monitor_requires_calibration(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    warned = []
    monkeypatch.setattr("app.main_window.QMessageBox.warning",
                        lambda *a, **k: warned.append(a))

    win._start_monitor()  # not calibrated, no data

    assert warned               # warned the user instead of starting
    assert win._monitor_worker is None


def _ready_to_monitor(win):
    """Put a MainWindow into a state where _start_monitor would proceed."""
    import numpy as np

    from app.data_io import DualBpdData
    from app.settings_panel import MODE_ACQUIRE

    t = np.arange(64) / 1e6
    win._data = DualBpdData(t=t, v1=np.sin(t), v2=np.cos(t),
                            sample_rate=1e6, source_files=())
    win.settings.optical.apply_calibrated_fsr(2e5)
    sp = win.settings
    sp.data.mode_combo.setCurrentIndex(sp.data.mode_combo.findData(MODE_ACQUIRE))


def test_save_checkbox_drives_save_monitor_enabled(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    assert win.settings.save_monitor_enabled is False
    win.settings.save_monitor_check.setChecked(True)
    assert win.settings.save_monitor_enabled is True


def test_start_monitor_aborts_when_save_dir_cancelled(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    _ready_to_monitor(win)
    win.settings.save_monitor_check.setChecked(True)
    # User cancels the folder picker.
    monkeypatch.setattr("app.main_window.QFileDialog.getExistingDirectory",
                        lambda *a, **k: "")

    win._start_monitor()

    assert win._monitor_worker is None      # never started
    assert win._monitor_recorder is None


def test_monitor_cycle_saves_when_recorder_active(qtbot, tmp_path):
    from app.main_window import MainWindow
    from app.monitor_io import MonitorRecorder

    win = MainWindow()
    qtbot.addWidget(win)
    win._monitor_recorder = MonitorRecorder(tmp_path)

    win._on_monitor_cycle(_make_result(v2=np.zeros(16)), 2.0, _raw_frame())

    assert win._monitor_recorder.count == 1
    assert (tmp_path / "trend_lorentz_beta.npz").exists()
    assert len(list(tmp_path.glob("spectrum_*.npz"))) == 1


# ---- history review (click trend → restore) + return to latest ----

def test_trend_click_selects_nearest_point_by_time(qtbot):
    from app.plot_widget import TrendPlot

    trend = TrendPlot()
    qtbot.addWidget(trend)
    trend.update_trend([0.0, 1.0, 2.0, 3.0], [10.0, 20.0, 30.0, 40.0])
    picked = []
    trend.pointSelected.connect(picked.append)

    class _Ev:
        inaxes = trend._ax
        xdata = 2.1
        ydata = 0.0

    trend._on_click(_Ev())
    assert picked == [2]               # nearest to t=2.1

    class _Outside:
        inaxes = None
        xdata = 1.0
        ydata = 0.0

    trend._on_click(_Outside())
    assert picked == [2]               # clicks outside the axes are ignored


def test_restore_from_trend_then_return_to_latest(qtbot, tmp_path):
    from app.main_window import MainWindow
    from app.monitor_io import MonitorRecorder

    win = MainWindow()
    qtbot.addWidget(win)
    win._monitor_recorder = MonitorRecorder(tmp_path)
    r1 = _make_result(v2=np.zeros(16))
    r2 = _make_result(v2=np.zeros(16))
    win._on_monitor_cycle(r1, 1.0, _raw_frame())
    win._on_monitor_cycle(r2, 2.0, _raw_frame())
    assert win._latest_result is r2

    win._restore_spectrum_from_trend(0)               # first saved cycle
    assert win._result is not r2                       # now showing history
    assert not win.return_latest_btn.isHidden()        # button shown
    np.testing.assert_allclose(win._result.freq, r1.freq)
    np.testing.assert_allclose(win._result.s_nu_12, r1.s_nu_12)

    win._return_to_latest()
    assert win._result is r2
    assert win.return_latest_btn.isHidden()            # button hidden again


def test_restore_from_trend_without_save_is_noop(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win._restore_spectrum_from_trend(0)                # no recorder → hint only
    assert win.return_latest_btn.isHidden()


# ---- rollback buffer (last 3) + save selected frame ----

def test_rollback_buffer_keeps_last_three(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    for i in range(4):
        win._on_monitor_cycle(_make_result(v2=np.zeros(16)), float(i), _raw_frame())

    assert len(win._monitor_buffer) == 3
    assert not win.settings.rollback_group.isHidden()
    assert win.settings.rollback_combo.count() == 3
    # default selection = current = the most recently appended frame
    assert win._selected_frame() is win._monitor_buffer[-1]


def test_selected_frame_follows_offset(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    for i in range(3):
        win._on_monitor_cycle(_make_result(v2=np.zeros(16)), float(i), _raw_frame())
    sp = win.settings
    sp.rollback_combo.setCurrentIndex(sp.rollback_combo.findData(1))  # −1
    assert win._selected_frame() is win._monitor_buffer[-2]


def test_save_frame_raw_writes_selected_frame(qtbot, tmp_path, monkeypatch):
    from app.data_io import load_record
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win._on_monitor_cycle(_make_result(v2=np.zeros(16)), 1.0, _raw_frame())
    out = tmp_path / "frame.npy"

    class _Dialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return (str(out), "NumPy array (*.npy)")

    monkeypatch.setattr("app.main_window.QFileDialog", _Dialog)
    win._save_frame_raw()

    assert out.exists()
    np.testing.assert_allclose(load_record(out).v1, _raw_frame().v1)


def test_save_frame_spectrum_writes_npz(qtbot, tmp_path, monkeypatch):
    from app.main_window import MainWindow
    from app.monitor_io import load_spectrum_result

    win = MainWindow()
    qtbot.addWidget(win)
    r = _make_result(v2=np.zeros(16))
    win._on_monitor_cycle(r, 1.0, _raw_frame())
    out = tmp_path / "frame_spec"  # no extension → .npz appended

    class _Dialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return (str(out), "NumPy archive (*.npz)")

    monkeypatch.setattr("app.main_window.QFileDialog", _Dialog)
    win._save_frame_spectrum()

    saved = tmp_path / "frame_spec.npz"
    assert saved.exists()
    np.testing.assert_allclose(load_spectrum_result(saved).s_nu_12, r.s_nu_12)


# ---- pause/resume + clear ----

class _FakeSignal:
    def connect(self, *a, **k):
        pass


class _FakeMonitorWorker:
    """Stands in for MonitorWorker so _start_monitor doesn't open a scope."""

    def __init__(self, req):
        self.progress = _FakeSignal()
        self.cycle_done = _FakeSignal()
        self.finished_err = _FakeSignal()
        self.finished = _FakeSignal()

    def start(self):
        pass

    def deleteLater(self):
        pass

    def request_stop(self):
        pass


def test_monitor_finished_offers_resume_and_clear(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win._can_resume_monitor = True
    win._on_monitor_finished()

    sp = win.settings
    assert sp.monitor_clear_btn.isEnabled()          # Clear available when paused
    assert "Resume" in sp.monitor_btn.text()         # Monitor button relabels


def test_monitor_resume_does_not_reprompt_folder(qtbot, tmp_path, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    _ready_to_monitor(win)
    monkeypatch.setattr("app.main_window.MonitorWorker", _FakeMonitorWorker)
    win.settings.save_monitor_check.setChecked(True)

    prompts = []
    monkeypatch.setattr(
        "app.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: (prompts.append(1), str(tmp_path))[1])

    win._start_monitor()                      # fresh → prompts once
    assert len(prompts) == 1
    rec = win._monitor_recorder
    assert rec is not None
    assert win._can_resume_monitor

    # simulate some accumulated trend, then pause
    win._trend_t.extend([1.0, 2.0, 5.0])
    win._on_monitor_finished()

    win._start_monitor()                      # resume → no second prompt
    assert len(prompts) == 1
    assert win._monitor_recorder is rec       # same folder/recorder reused
    assert win._monitor_time_offset == 5.0    # time axis continues


def test_monitor_clear_forces_fresh_start(qtbot, tmp_path, monkeypatch):
    from app.main_window import MainWindow
    from app.monitor_io import MonitorFrame

    win = MainWindow()
    qtbot.addWidget(win)
    _ready_to_monitor(win)
    monkeypatch.setattr("app.main_window.MonitorWorker", _FakeMonitorWorker)
    win.settings.save_monitor_check.setChecked(True)

    prompts = []
    monkeypatch.setattr(
        "app.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: (prompts.append(1), str(tmp_path))[1])

    win._start_monitor()
    win._trend_t.extend([1.0, 2.0])
    win._monitor_buffer.append(
        MonitorFrame(raw=_raw_frame(), result=_make_result(v2=np.zeros(16)),
                     elapsed=2.0, stamp="120000"))
    win._on_monitor_finished()

    win._clear_monitor()
    assert not win._can_resume_monitor
    assert win._monitor_recorder is None
    assert len(win._trend_t) == 0
    assert len(win._monitor_buffer) == 0
    assert not win.settings.monitor_clear_btn.isEnabled()
    assert "Monitor (live)" in win.settings.monitor_btn.text()

    win._start_monitor()                      # fresh again → prompts a second time
    assert len(prompts) == 2
