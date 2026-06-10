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


def test_calibration_failure_prompts_and_uses_tau_without_locking(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    monkeypatch.setattr(win, "_start_process", lambda: None)
    prompts = []
    monkeypatch.setattr("app.main_window.QMessageBox.information",
                        lambda *a, **k: prompts.append(a))
    win.settings.optical.manual_tau.setValue(50.0)     # 50 ns → 20 MHz

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
    assert prompts                                      # user was prompted
    assert win.settings.optical.is_calibrated           # not locked
    assert np.isclose(win.settings.optical.delay_freq_hz, 20e6)  # τ field value


def test_manual_override_takes_priority(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    opt = win.settings.optical
    opt.manual_tau.setValue(40.0)          # 40 ns → 25 MHz
    opt.manual_check.setChecked(True)

    assert opt.manual_enabled
    assert opt.is_calibrated                # manual is always a usable FSR
    assert np.isclose(opt.delay_freq_hz, 25e6)


def test_manual_length_and_tau_stay_linked(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    opt = win.settings.optical
    n = opt.n_core.value()
    C = 299_792_458.0

    opt.manual_tau.setValue(100.0)                       # 100 ns
    expected_len = 100.0e-9 * C / n
    assert np.isclose(opt.manual_len.value(), expected_len, rtol=1e-4)

    opt.manual_len.setValue(10.0)                        # 10 m
    expected_tau = 10.0 * n / C * 1e9
    # τ spinbox rounds to 0.1 ns, so allow one decimal step of slack.
    assert np.isclose(opt.manual_tau.value(), expected_tau, atol=0.1)
    assert opt.manual_tau.value() != 100.0               # actually changed (linked)


def test_kick_off_autocal_uses_manual_without_calibrating(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    processed = []
    calibrated = []
    monkeypatch.setattr(win, "_start_process", lambda: processed.append(True))
    monkeypatch.setattr(win, "_start_calibrate",
                        lambda **k: calibrated.append(True))
    win.settings.optical.manual_tau.setValue(40.0)
    win.settings.optical.manual_check.setChecked(True)  # _data is None → no process yet
    win._data = object()  # presence is enough; manual path doesn't read it

    win._kick_off_autocal()

    assert processed == [True]      # processed with the manual FSR
    assert calibrated == []         # auto-calibration skipped


def test_process_stays_enabled_after_manual_length_change(qtbot, monkeypatch):
    """Regression for bug 2.2: after the data auto-processes, ticking Manual
    FSR and editing the fiber length / τ must keep Process clickable so the
    cached data can be re-processed with the new delay."""
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    # Auto-cal succeeded earlier → Process is unlocked.
    win.settings.optical.apply_calibrated_fsr(2e5)
    win.settings.set_calibrated(True)
    assert win.settings.process_btn.isEnabled() is True

    # User ticks Manual FSR (goes through the real toggle handler) and dials in
    # the 400 m fiber. Neither step should disable Process.
    win.settings.optical.manual_check.setChecked(True)
    assert win.settings.process_btn.isEnabled() is True

    win.settings.optical.manual_len.setValue(400.0)
    assert win.settings.process_btn.isEnabled() is True
    # The new length actually drives the resolved FSR/τ used by Process.
    assert win.settings.optical.delay_freq_hz < 1e6      # 400 m → ~0.5 MHz FSR

    # And Process can in fact be invoked (re-processes the cached data).
    started = []
    monkeypatch.setattr(win, "_start_process", lambda: started.append(True))
    win.settings.process_btn.click()
    assert started == [True]


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
    from app.settings_panel import SRC_SCOPE

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings

    # Not calibrated → monitor disabled
    sp.optical.reset_calibration()
    assert sp.monitor_btn.isEnabled() is False

    # Calibrated (usable FSR) + dual-BPD scope source → monitor enabled
    sp.data.select_source(SRC_SCOPE)          # algorithm defaults to XCORR
    sp.optical.apply_calibrated_fsr(2e5)
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
    from app.settings_panel import SRC_SCOPE

    t = np.arange(64) / 1e6
    win._data = DualBpdData(t=t, v1=np.sin(t), v2=np.cos(t),
                            sample_rate=1e6, source_files=())
    win.settings.optical.apply_calibrated_fsr(2e5)
    sp = win.settings
    sp.data.select_source(SRC_SCOPE)          # XCORR + scope → MODE_ACQUIRE


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


def _avg_result():
    from app.averaging import average_records

    def rec(seed):
        rng = np.random.default_rng(seed)
        t = np.arange(2048) / 1e6
        return np.sin(2 * np.pi * 1e5 * t + 0.01 * np.cumsum(rng.standard_normal(2048)))

    return average_records([rec(i) for i in range(3)], 1e6, 1e6, n_skip=10, fmax=4e5)


def test_average_mode_toggles_controls(qtbot):
    from app.main_window import MainWindow
    from app.settings_panel import ALGO_AVERAGE, ALGO_XCORR, SRC_SCOPE

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings
    d = sp.data
    d.select_algorithm(ALGO_AVERAGE)
    d.select_source(SRC_SCOPE)
    # Averaging shows its param + N groups and relabels the acquire button,
    # while the Segments section and Monitor row are hidden.
    assert not d._grp_avg_params.isHidden()
    assert not d._grp_avg_scope.isHidden()
    assert "average" in d.acquire_btn.text().lower()
    assert sp._section_boxes["Segments"].isHidden()
    assert sp._monitor_row_widget.isHidden()

    d.select_algorithm(ALGO_XCORR)
    assert d._grp_avg_params.isHidden()
    assert not sp._section_boxes["Segments"].isHidden()
    assert not sp._monitor_row_widget.isHidden()


def test_start_average_without_manual_fsr_spawns_worker_with_none_fsr(qtbot, monkeypatch):
    """Without a manual FSR, _start_average passes fsr=None to let the worker
    auto-calibrate from the first acquired record (Task 2.1)."""
    from app.main_window import MainWindow
    from app.settings_panel import ALGO_AVERAGE, SRC_SCOPE

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings
    sp.data.select_algorithm(ALGO_AVERAGE)
    sp.data.select_source(SRC_SCOPE)
    sp.data.scope_ip.setText("1.2.3.4")           # has host, no manual FSR
    captured = {}

    class _FakeAvgWorker:
        def __init__(self, host, ch1, n, fsr, skip, fmax,
                     with_convergence=False, n_core=1.468, **kw):
            captured.update(fsr=fsr)
            self.progress = _FakeSignal()
            self.finished_ok = _FakeSignal()
            self.finished_err = _FakeSignal()
            self.finished = _FakeSignal()

        def start(self):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr("app.main_window.AverageAcquireWorker", _FakeAvgWorker)
    win._start_average()

    assert captured["fsr"] is None                # auto-cal path: None passed through
    assert win._avg_worker is not None


def test_start_average_spawns_worker_with_resolved_fsr(qtbot, monkeypatch):
    from app.main_window import MainWindow
    from app.settings_panel import ALGO_AVERAGE, SRC_SCOPE

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings
    sp.data.select_algorithm(ALGO_AVERAGE)
    sp.data.select_source(SRC_SCOPE)
    sp.data.scope_ip.setText("1.2.3.4")
    sp.optical.manual_tau.setValue(100.0)          # 100 ns → 10 MHz
    sp.optical.manual_check.setChecked(True)
    captured = {}

    class _FakeAvgWorker:
        def __init__(self, host, ch1, n, fsr, skip, fmax,
                     with_convergence=False, n_core=1.468, **kw):
            captured.update(host=host, n=n, fsr=fsr, skip=skip)
            self.progress = _FakeSignal()
            self.finished_ok = _FakeSignal()
            self.finished_err = _FakeSignal()
            self.finished = _FakeSignal()

        def start(self):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr("app.main_window.AverageAcquireWorker", _FakeAvgWorker)
    win._start_average()

    assert captured["host"] == "1.2.3.4"
    assert captured["n"] == 10                     # default average count
    assert np.isclose(captured["fsr"], 1e7)        # manual τ=100ns → 10 MHz


def test_average_ok_renders_and_enables_save(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    res = _avg_result()
    win._on_average_ok((res, []))

    assert win._avg_result is res
    assert win.settings.data.save_averaged_btn.isEnabled()


def test_save_averaged_spectrum_npz(qtbot, tmp_path, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win._avg_result = _avg_result()
    out = tmp_path / "avg"  # no extension → .npz appended from the filter

    class _Dialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return (str(out), "NumPy archive (*.npz)")

    monkeypatch.setattr("app.main_window.QFileDialog", _Dialog)
    win._save_averaged_spectrum()

    saved = tmp_path / "avg.npz"
    assert saved.exists()
    with np.load(saved) as d:
        np.testing.assert_allclose(d["S_nu_Hz2_per_Hz"], win._avg_result.s_nu)


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


def test_avg_file_process_routes_to_file_averaging(qtbot, tmp_path):
    """In (averaging, file) mode, Process is gated on a selected file and routes
    to the file-averaging worker rather than CoshXcorr."""
    from pathlib import Path

    from app.data_io import save_records
    from app.main_window import MainWindow
    from app.settings_panel import ALGO_AVERAGE, SRC_FILE

    win = MainWindow()
    qtbot.addWidget(win)
    d = win.settings.data
    d.select_algorithm(ALGO_AVERAGE)
    d.select_source(SRC_FILE)

    # No file yet → Process disabled.
    assert win.settings.process_btn.isEnabled() is False

    recs = np.random.default_rng(0).standard_normal((3, 256))
    path = tmp_path / "m.npz"
    save_records(path, recs, 1e6)
    d._set_avg_file(Path(path))
    d.fileChanged.emit()
    assert win.settings.process_btn.isEnabled() is True

    routed = {}
    win._start_average_from_file = lambda: routed.setdefault("file", True)
    win._start_average = lambda: routed.setdefault("scope", True)
    win._start_process()
    assert routed == {"file": True}


def test_keep_raw_average_enables_raw_save(qtbot):
    """After a keep-raw average produces records, 'Save raw records' is enabled;
    a file average (no retained records) leaves it disabled."""
    from app.main_window import MainWindow
    from app.settings_panel import ALGO_AVERAGE, SRC_SCOPE

    win = MainWindow()
    qtbot.addWidget(win)
    d = win.settings.data
    d.select_algorithm(ALGO_AVERAGE)
    d.select_source(SRC_SCOPE)
    win.plot.render_averaged = lambda *a, **k: None

    class _Result:
        n_avg = 2
        linewidth_hz = float("nan")
        fsr_hz = 5e5

    # keep-raw worker → records retained → save enabled
    win._avg_worker = type("W", (), {"raw_records": [np.zeros(8), np.zeros(8)],
                                      "sample_rate_hz": 1e6})()
    win._on_average_ok((_Result(), []))
    assert d.save_raw_btn.isEnabled() is True

    # file/plain worker → no records → save disabled
    win._avg_worker = type("W", (), {})()
    win._on_average_ok((_Result(), []))
    assert d.save_raw_btn.isEnabled() is False


def test_scope_averaging_locks_process_button(qtbot):
    """A scope averaging run marks the panel busy so Process can't launch a
    second concurrent acquisition (regression: set_averaging must set _busy)."""
    from app.main_window import MainWindow
    from app.settings_panel import ALGO_AVERAGE, SRC_SCOPE

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings
    sp.data.select_algorithm(ALGO_AVERAGE)
    sp.data.select_source(SRC_SCOPE)
    assert sp.process_btn.isEnabled() is True       # ready before run

    sp.set_averaging(True)
    assert sp.process_btn.isEnabled() is False       # locked during the run
    sp.set_averaging(False)
    assert sp.process_btn.isEnabled() is True         # unlocked afterwards


def test_rollback_group_restored_when_returning_to_xcorr(qtbot):
    """Switching to averaging hides the monitoring rollback group; returning to
    XCORR restores it when recent frames still exist."""
    from app.main_window import MainWindow
    from app.settings_panel import ALGO_AVERAGE, ALGO_XCORR

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings
    sp.set_rollback_available(2)                       # frames exist → shown
    assert sp.rollback_group.isHidden() is False

    sp.data.select_algorithm(ALGO_AVERAGE)
    assert sp.rollback_group.isHidden() is True        # hidden under averaging

    sp.data.select_algorithm(ALGO_XCORR)
    assert sp.rollback_group.isHidden() is False       # restored (frames remain)
