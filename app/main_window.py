"""Main application window wiring all sub-panels together."""
from __future__ import annotations

import datetime as dt
from collections import deque
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .analysis import fit_lorentz_floor, integrate_beta
from .data_io import (
    DualBpdData,
    export_spectrum,
    from_arrays,
    load_record,
    load_two_records,
    save_record,
)
from .mzi_calibrate import MziResult
from .plot_widget import DisplayOptions, SpectrumPlot, TrendPlot, _format_hz
from .processor import (
    CalibrateWorker,
    ProcessRequest,
    ProcessResult,
    ProcessWorker,
)
from .monitor import MonitorRequest, MonitorWorker
from .monitor_io import (
    MonitorFrame,
    MonitorRecorder,
    load_spectrum_result,
    save_cycle_spectrum,
)
from .scope import AcquireWorker, TestConnectionWorker, frame_to_arrays
from .settings_panel import (
    MODE_ACQUIRE,
    MODE_SINGLE_CSV,
    MODE_TWO_CSV,
    SettingsPanel,
)


def _resolve_save_path(path: str, selected_filter: str) -> Path:
    """Pick the final save path. Honour an explicit .csv/.npy suffix the user
    typed; otherwise append the extension implied by the chosen dialog filter
    (defaults to .csv)."""
    p = Path(path)
    if p.suffix.lower() in (".csv", ".npy"):
        return p
    ext = ".npy" if "npy" in selected_filter.lower() else ".csv"
    return p.with_suffix(ext)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Dual-BPD Noise Analyzer")
        # The sidebar scrolls vertically when too short to show every
        # section, so the window minimum can be modest. Default size aims
        # to fit every card without needing the scrollbar on most displays.
        self.setMinimumSize(1000, 680)
        self.resize(1380, 980)

        self._data: DualBpdData | None = None
        self._result: ProcessResult | None = None
        self._proc_worker: ProcessWorker | None = None
        self._cal_worker: CalibrateWorker | None = None
        self._acq_worker: AcquireWorker | None = None
        self._conn_worker: TestConnectionWorker | None = None
        self._monitor_worker: MonitorWorker | None = None
        self._monitor_recorder: MonitorRecorder | None = None
        # Last up-to-3 cycles (raw trace + result) kept for rollback save.
        self._monitor_buffer: deque[MonitorFrame] = deque(maxlen=3)
        # Most recent live result, so we can return after browsing history.
        self._latest_result: ProcessResult | None = None
        # Monitoring can be paused (Stop) and resumed unless cleared.
        self._can_resume_monitor: bool = False
        self._monitor_time_offset: float = 0.0
        self._trend_t: deque[float] = deque(maxlen=1000)
        self._trend_fwhm: deque[float] = deque(maxlen=1000)
        # Whether the calibration currently running was launched by an
        # explicit user click (True) or automatically on data load (False).
        # Used to decide whether failure popups appear.
        self._cal_user_initiated: bool = False

        self.settings = SettingsPanel()
        self.plot = SpectrumPlot()
        self.trend = TrendPlot()
        self.trend.setVisible(False)

        # "Return to latest" sits in the top-right corner above the trend; it
        # only shows while viewing a restored historical spectrum.
        self.return_latest_btn = QPushButton("↩ Latest")
        self.return_latest_btn.setProperty("variant", "secondary")
        self.return_latest_btn.clicked.connect(self._return_to_latest)
        self.return_latest_btn.setVisible(False)
        history_bar = QHBoxLayout()
        history_bar.setContentsMargins(8, 2, 8, 2)
        history_bar.addStretch(1)
        history_bar.addWidget(self.return_latest_btn)

        plot_container = QWidget()
        plot_col = QVBoxLayout(plot_container)
        plot_col.setContentsMargins(0, 0, 0, 0)
        plot_col.setSpacing(0)
        plot_col.addWidget(self.plot, 1)
        plot_col.addLayout(history_bar)
        plot_col.addWidget(self.trend)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.settings)
        layout.addWidget(plot_container, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

        # signals
        self.settings.fileChanged.connect(self._on_file_changed)
        self.settings.optionsChanged.connect(self._redraw)
        self.settings.processRequested.connect(self._start_process)
        self.settings.exportRequested.connect(self._export)
        # User-initiated calibration: show popups on failure.
        self.settings.calibrateRequested.connect(
            lambda: self._start_calibrate(user_initiated=True)
        )
        self.settings.acquireRequested.connect(self._start_acquire)
        self.settings.testConnectionRequested.connect(self._test_connection)
        self.settings.saveAcquiredRequested.connect(self._save_acquired)
        self.settings.monitorStartRequested.connect(self._start_monitor)
        self.settings.monitorStopRequested.connect(self._stop_monitor)
        self.settings.monitorClearRequested.connect(self._clear_monitor)
        self.settings.saveFrameRawRequested.connect(self._save_frame_raw)
        self.settings.saveFrameSpectrumRequested.connect(self._save_frame_spectrum)
        self.trend.pointSelected.connect(self._restore_spectrum_from_trend)
        self.settings.set_monitor_resumable(False)

        # Drag a .csv/.npy/.npz file (or two) onto the window to load it.
        self.setAcceptDrops(True)

    # ---------- drag & drop ----------

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if self.settings.data.accept_dropped(paths):
            event.acceptProposedAction()
        else:
            self.statusBar().showMessage(
                "Dropped file ignored (need a .csv, .npy or .npz)."
            )

    # ---------- data loading ----------

    def _on_file_changed(self) -> None:
        mode = self.settings.data.mode
        if mode == MODE_ACQUIRE:
            # Scope mode: data is loaded by the Acquire button, not file paths.
            # Don't reset existing acquired data when user just switches mode.
            return

        f1 = self.settings.data.file1
        f2 = self.settings.data.file2
        if not f1:
            self._reset_loaded_data()
            return
        try:
            if mode == MODE_SINGLE_CSV:
                self._data = load_record(f1)
            else:  # MODE_TWO_CSV
                if not f2:
                    self.settings.data.set_info(
                        f"Loaded BPD1 ({f1.name}). Select BPD2 to enable processing."
                    )
                    return
                self._data = load_two_records(f1, f2)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Load error", str(exc))
            return

        self._update_data_info()
        self.settings.data.mark_acquired(False)
        self._kick_off_autocal()

    def _reset_loaded_data(self) -> None:
        """Drop any loaded data and lock Process again."""
        self._data = None
        self.settings.data.set_info("No data loaded.")
        self.settings.optical.reset_calibration()
        self.settings.set_calibrated(False)

    def _kick_off_autocal(self) -> None:
        """Fresh data → invalidate any previous FSR and start auto-cal.
        Process stays locked until calibration succeeds."""
        self.settings.optical.reset_calibration()
        self.settings.set_calibrated(False)
        self._start_calibrate(user_initiated=False)

    @staticmethod
    def _launch_worker(worker, ok_slot, err_slot, *, done_slot=None) -> None:
        """Wire the three common signals (finished_ok / finished_err /
        finished→deleteLater) on a QThread worker, then start it. Any
        worker-specific signal (e.g. `progress`) should be connected by
        the caller before invoking this helper."""
        worker.finished_ok.connect(ok_slot)
        worker.finished_err.connect(err_slot)
        if done_slot is not None:
            worker.finished.connect(done_slot)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _update_data_info(self, extra: str = "") -> None:
        if self._data is None:
            return
        d = self._data
        v2_status = "present" if d.v2 is not None else "—"
        info = (
            f"{d.n_samples:,} samples · {d.sample_rate/1e6:.2f} MSa/s · "
            f"{d.duration_s*1e3:.3f} ms · BPD2: {v2_status}"
        )
        if extra:
            info += f"\n{extra}"
        self.settings.data.set_info(info)
        self.statusBar().showMessage(
            f"Loaded {d.n_samples:,} samples at {d.sample_rate/1e6:.2f} MSa/s."
        )

    # ---------- scope acquisition ----------

    # ---------- scope connection test ----------

    def _test_connection(self) -> None:
        host = self.settings.data.scope_host
        if not host:
            self.settings.data.set_connection_status("fail", "no IP entered")
            return
        if self._conn_worker is not None and self._conn_worker.isRunning():
            return  # a test is already in flight
        self.settings.data.set_connection_status("testing")
        self.settings.data.test_conn_btn.setEnabled(False)
        self.statusBar().showMessage(f"Testing connection to {host} …")
        self._conn_worker = TestConnectionWorker(host)
        self._launch_worker(
            self._conn_worker, self._on_conn_ok, self._on_conn_err,
            done_slot=lambda: self.settings.data.test_conn_btn.setEnabled(True),
        )

    def _on_conn_ok(self, idn: str) -> None:
        self.settings.data.set_connection_status("ok", idn)
        self.statusBar().showMessage(f"Scope connected: {idn}")

    def _on_conn_err(self, message: str) -> None:
        self.settings.data.set_connection_status("fail", message)
        self.statusBar().showMessage("Scope connection failed.")

    def _start_acquire(self) -> None:
        host = self.settings.data.scope_host
        ch1 = self.settings.data.scope_ch1_name
        ch2 = self.settings.data.scope_ch2_name
        send_single = self.settings.data.scope_single.isChecked()
        if not host:
            QMessageBox.warning(self, "No host",
                                "Enter the oscilloscope IP first.")
            return
        if ch1 == ch2:
            QMessageBox.warning(self, "Channel conflict",
                                "BPD1 and BPD2 cannot be the same channel.")
            return

        self.settings.set_acquiring(True)
        self.statusBar().showMessage(f"Connecting to {host} …")
        self._acq_worker = AcquireWorker(host, ch1, ch2, send_single)
        self._acq_worker.progress.connect(self.statusBar().showMessage)
        self._launch_worker(
            self._acq_worker, self._on_acquire_ok, self._on_acquire_err,
            done_slot=lambda: self.settings.set_acquiring(False),
        )

    def _on_acquire_ok(self, payload) -> None:
        frame, ch1, ch2 = payload
        t, v1, v2, sr = frame_to_arrays(frame, ch1, ch2)
        label = f"scope@{dt.datetime.now().strftime('%H:%M:%S')}"
        self._data = from_arrays(t, v1, v2, sr, label=label)
        chs = ch1 + (f" + {ch2}" if ch2 else "")
        self._update_data_info(extra=f"From scope · {chs}")
        self.settings.data.mark_acquired(True)
        self._kick_off_autocal()

    def _on_acquire_err(self, message: str) -> None:
        self.statusBar().showMessage("Acquisition failed.")
        QMessageBox.critical(self, "Acquisition failed", message)

    def _save_acquired(self) -> None:
        if self._data is None:
            return
        stem = f"acquired_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        path, selected = QFileDialog.getSaveFileName(
            self, "Save acquired data", stem,
            "CSV (*.csv);;NumPy array (*.npy)",
        )
        if not path:
            return
        out = _resolve_save_path(path, selected)
        try:
            save_record(out, self._data)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved acquired data → {out}")

    # ---------- processing ----------

    def _start_process(self) -> None:
        if self._data is None:
            QMessageBox.warning(self, "No data",
                                "Load a CSV file or acquire from scope first.")
            return
        if not self.settings.optical.is_calibrated:
            QMessageBox.warning(
                self, "FSR not calibrated",
                "FSR auto-calibration hasn't succeeded yet. The G(f) "
                "compensation needs the true delay-line FSR — click "
                "Re-calibrate or load cleaner data.",
            )
            return
        try:
            snap = self.settings.snapshot()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        req = ProcessRequest(
            v1=self._data.v1,
            v2=self._data.v2,
            sample_rate=self._data.sample_rate,
            delay_freq=snap.delay_freq_hz,  # guaranteed non-None by check above
            bw_segment=snap.bw_segment_hz,
            offset_start_ratio=snap.offset_start_ratio,
            range_start=snap.range_start,
            range_stop=snap.range_stop,
        )
        self.settings.set_processing(True)
        self.statusBar().showMessage("Starting pycosh worker…")

        self._proc_worker = ProcessWorker(req)
        self._proc_worker.progress.connect(self.statusBar().showMessage)
        self._launch_worker(
            self._proc_worker, self._on_process_ok, self._on_process_err,
        )

    def _on_process_ok(self, result: ProcessResult) -> None:
        self._result = result
        self.settings.set_processing(False)
        self.settings.set_export_enabled(True)
        n = result.freq.size
        self.statusBar().showMessage(
            f"Done. {n} frequency points "
            f"({result.freq.min():.0f} – {result.freq.max():.0f} Hz)."
        )
        self._redraw()

    def _on_process_err(self, message: str) -> None:
        self.settings.set_processing(False)
        self.statusBar().showMessage("Processing failed.")
        QMessageBox.critical(self, "Processing failed", message)

    # ---------- live monitoring ----------

    def _start_monitor(self) -> None:
        if self._data is None or not self.settings.optical.is_calibrated:
            QMessageBox.warning(
                self, "Not ready",
                "Acquire once to calibrate the FSR before starting live "
                "monitoring.",
            )
            return
        if self.settings.data.mode != MODE_ACQUIRE:
            QMessageBox.warning(self, "Acquire mode only",
                                "Live monitoring needs the oscilloscope mode.")
            return
        try:
            snap = self.settings.snapshot()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        # A monitoring session can be paused (Stop) and resumed: clicking
        # Monitor again CONTINUES the same run (same folder, same trend,
        # continued cycle numbering) unless the user pressed Clear, which forces
        # a fresh start (new folder prompt + everything reset to zero).
        fresh = not self._can_resume_monitor
        if fresh:
            recorder = None
            if self.settings.save_monitor_enabled:
                directory = QFileDialog.getExistingDirectory(
                    self, "Choose a folder to save monitoring data")
                if not directory:
                    self.statusBar().showMessage("Monitoring cancelled (no folder chosen).")
                    return
                try:
                    recorder = MonitorRecorder(directory)
                except OSError as exc:
                    QMessageBox.critical(self, "Cannot save here", str(exc))
                    return
            self._monitor_recorder = recorder
            self._monitor_buffer.clear()
            self._latest_result = None
            self._trend_t.clear()
            self._trend_fwhm.clear()
            self.trend.clear()
            self._monitor_time_offset = 0.0
            self.settings.set_rollback_available(0)
        else:
            # Resume: keep recorder/trend/buffer; continue the elapsed-time axis
            # from where it left off so the trend stays monotonic.
            self._monitor_time_offset = self._trend_t[-1] if self._trend_t else 0.0

        self.return_latest_btn.setVisible(False)
        self.trend.setVisible(True)
        self.settings.set_monitoring(True)
        self._can_resume_monitor = True
        self.statusBar().showMessage(
            "Live monitoring started…" if fresh else "Live monitoring resumed…")

        req = MonitorRequest(
            host=self.settings.data.scope_host,
            ch1=self.settings.data.scope_ch1_name,
            ch2=self.settings.data.scope_ch2_name,
            send_single=False,  # monitoring free-runs (AUTO); never waits a trigger
            delay_freq=snap.delay_freq_hz,
            bw_segment=snap.bw_segment_hz,
            offset_start_ratio=snap.offset_start_ratio,
            range_start=snap.range_start,
            range_stop=snap.range_stop,
        )

        self._monitor_worker = MonitorWorker(req)
        self._monitor_worker.progress.connect(self.statusBar().showMessage)
        self._monitor_worker.cycle_done.connect(self._on_monitor_cycle)
        self._monitor_worker.finished_err.connect(self._on_monitor_err)
        self._monitor_worker.finished.connect(self._on_monitor_finished)
        self._monitor_worker.finished.connect(self._monitor_worker.deleteLater)
        self._monitor_worker.start()

    def _on_monitor_cycle(self, result: ProcessResult, elapsed: float,
                          raw: DualBpdData) -> None:
        # Continue the elapsed-time axis across pause/resume (0 on a fresh run).
        elapsed = self._monitor_time_offset + elapsed
        self._result = result
        self._latest_result = result
        self.settings.set_export_enabled(True)  # latest frame is exportable once stopped
        # A new live frame leaves any historical view.
        self.return_latest_btn.setVisible(False)

        stamp = dt.datetime.now().strftime("%H%M%S")
        self._monitor_buffer.append(
            MonitorFrame(raw=raw, result=result, elapsed=elapsed, stamp=stamp))
        self.settings.set_rollback_available(len(self._monitor_buffer))

        lorentz_fwhm = None
        beta_fwhm = None
        try:
            snap = self.settings.snapshot()
        except ValueError:
            snap = None
        if snap is not None:
            lz = fit_lorentz_floor(result.freq, result.s_nu_12,
                                   f_min=snap.lorentz_f_min,
                                   f_max=snap.lorentz_f_max)
            if lz is not None:
                lorentz_fwhm = lz.fwhm_hz
            beta = integrate_beta(result.freq, result.s_nu_12,
                                  f_min=snap.beta_f_min,
                                  f_max=snap.beta_f_max)
            if beta is not None:
                beta_fwhm = beta.fwhm_gauss_hz

        self._trend_t.append(elapsed)
        self._trend_fwhm.append(
            lorentz_fwhm if lorentz_fwhm is not None else float("nan"))
        self.trend.update_trend(list(self._trend_t), list(self._trend_fwhm))

        # Persist this cycle's spectrum + the Lorentz/β trend if enabled.
        saved_note = ""
        if self._monitor_recorder is not None:
            try:
                self._monitor_recorder.record(
                    result, elapsed, lorentz_fwhm, beta_fwhm, stamp=stamp,
                )
                saved_note = f" · saved {self._monitor_recorder.count}"
            except Exception as exc:  # noqa: BLE001
                saved_note = f" · save failed: {exc}"

        self._redraw()

        shown = _format_hz(lorentz_fwhm) if lorentz_fwhm is not None else "—"
        self.statusBar().showMessage(
            f"Monitoring… {len(self._trend_t)} frames · last FWHM = {shown}{saved_note}"
        )

    def _stop_monitor(self) -> None:
        if self._monitor_worker is not None:
            self.statusBar().showMessage("Stopping after current frame…")
            self._monitor_worker.request_stop()

    def _on_monitor_finished(self) -> None:
        self._monitor_worker = None
        self.settings.set_monitoring(False)
        # Paused, not finished: the next Monitor click resumes this session
        # (unless Clear is pressed first).
        self.settings.set_monitor_resumable(True)
        self.statusBar().showMessage(
            f"Monitoring paused. {len(self._trend_t)} frames captured — "
            f"click Monitor to resume, or Clear to start fresh."
        )

    def _clear_monitor(self) -> None:
        """Discard the current monitoring session so the next start is fresh
        (new folder prompt, trend/buffer/cycle count reset to zero)."""
        self._monitor_recorder = None
        self._monitor_buffer.clear()
        self._latest_result = None
        self._trend_t.clear()
        self._trend_fwhm.clear()
        self.trend.clear()
        self.trend.setVisible(False)
        self._monitor_time_offset = 0.0
        self._can_resume_monitor = False
        self.return_latest_btn.setVisible(False)
        self.settings.set_rollback_available(0)
        self.settings.set_monitor_resumable(False)
        self.statusBar().showMessage("Monitoring session cleared — next start is fresh.")

    def _on_monitor_err(self, message: str) -> None:
        self.statusBar().showMessage("Monitoring failed.")
        QMessageBox.critical(self, "Monitoring failed", message)

    # ---------- history review + rollback save ----------

    def _restore_spectrum_from_trend(self, index: int) -> None:
        """Clicking a trend point restores the upper spectrum to that cycle,
        loaded from its saved .npz (requires monitoring with 'Save' enabled)."""
        rec = self._monitor_recorder
        if rec is None or not rec.spectrum_paths:
            self.statusBar().showMessage(
                "Enable 'Save' before monitoring to revisit a cycle's spectrum.")
            return
        # The trend strip shows only the last len(_trend_t) cycles, while
        # spectrum_paths holds every cycle — offset the clicked display index
        # into the full list so the mapping stays correct past 1000 cycles.
        file_index = len(rec.spectrum_paths) - len(self._trend_t) + index
        if not 0 <= file_index < len(rec.spectrum_paths):
            self.statusBar().showMessage("No saved spectrum for that point.")
            return
        try:
            self._result = load_spectrum_result(rec.spectrum_paths[file_index])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Cannot load spectrum", str(exc))
            return
        self.return_latest_btn.setVisible(self._latest_result is not None)
        self._redraw()
        self.statusBar().showMessage(
            f"Showing saved cycle {file_index + 1} — click ↩ Latest to return.")

    def _return_to_latest(self) -> None:
        if self._latest_result is None:
            return
        self._result = self._latest_result
        self.return_latest_btn.setVisible(False)
        self._redraw()
        self.statusBar().showMessage("Back to the latest spectrum.")

    def _selected_frame(self) -> MonitorFrame | None:
        """The rollback-buffer frame the user picked (current / −1 / −2)."""
        offset = self.settings.rollback_offset
        if offset + 1 > len(self._monitor_buffer):
            return None
        return self._monitor_buffer[-(offset + 1)]

    def _save_frame_raw(self) -> None:
        frame = self._selected_frame()
        if frame is None:
            return
        stem = f"frame_{frame.stamp}"
        path, selected = QFileDialog.getSaveFileName(
            self, "Save frame raw data", stem,
            "CSV (*.csv);;NumPy array (*.npy)",
        )
        if not path:
            return
        out = _resolve_save_path(path, selected)
        try:
            save_record(out, frame.raw)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved frame raw data → {out}")

    def _save_frame_spectrum(self) -> None:
        frame = self._selected_frame()
        if frame is None:
            return
        stem = f"spectrum_{frame.stamp}.npz"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save frame spectrum", stem, "NumPy archive (*.npz)",
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() != ".npz":
            out = out.with_suffix(".npz")
        try:
            save_cycle_spectrum(out, frame.result)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved frame spectrum → {out}")

    # ---------- display + analysis ----------

    def _redraw(self) -> None:
        if self._result is None:
            return
        try:
            snap = self.settings.snapshot()
        except ValueError:
            return

        # Always compute Lorentz + β on the cross-correlation Sν (laser noise)
        freq = self._result.freq
        s_nu = self._result.s_nu_12

        lz = fit_lorentz_floor(freq, s_nu,
                               f_min=snap.lorentz_f_min,
                               f_max=snap.lorentz_f_max)
        beta = integrate_beta(freq, s_nu,
                              f_min=snap.beta_f_min,
                              f_max=snap.beta_f_max)

        # Update text readouts in the sidebar
        if lz is None:
            lz_text = "Lorentz FWHM: — (fit range has no points)"
        else:
            lz_text = (
                f"Lorentz FWHM = {_format_hz(lz.fwhm_hz)}  "
                f"(S₀ = {lz.s0_hz2_per_hz:.3g} Hz²/Hz, "
                f"{lz.n_points} bins)"
            )
        if beta is None:
            beta_text = "β-FWHM (Gauss): — (no points in range)"
        else:
            beta_text = (
                f"β-FWHM (Gauss) = {_format_hz(beta.fwhm_gauss_hz)}  "
                f"(A = {beta.area_hz2:.3g} Hz², "
                f"{beta.fraction_above_beta*100:.0f}% above β)"
            )
        self.settings.analysis.update_results(lz_text, beta_text)

        options = DisplayOptions(
            noise_type=snap.noise_type,
            show_bpd1=snap.show_bpd1,
            show_bpd2=snap.show_bpd2 and self._result.request.v2 is not None,
            show_cross=snap.show_cross,
            show_errorband=snap.show_errorband,
            show_beta_line=snap.show_beta_line,
            show_lorentz_floor=snap.show_lorentz_floor,
            lorentz_fit=lz,
            beta_fit=beta,
        )
        self.plot.render(self._result, options)

    # ---------- calibration ----------

    def _start_calibrate(self, *, user_initiated: bool = False) -> None:
        """Run FSR auto-calibration. `user_initiated=True` means the user
        clicked Re-calibrate (popup on failure); False means we kicked
        it off from a data-load event (silent on failure)."""
        if self._data is None:
            if user_initiated:
                QMessageBox.warning(
                    self, "No data",
                    "Load a CSV file or acquire first — calibration reads "
                    "the FSR off a real beat trace.",
                )
            return
        self._cal_user_initiated = user_initiated
        snap = self.settings.snapshot()
        self.statusBar().showMessage("Auto-calibrating FSR (Welch PSD + dip search)…")
        self.settings.optical.set_calibrating()
        self._cal_worker = CalibrateWorker(
            self._data.v1, self._data.sample_rate, snap.n_core,
        )
        self._launch_worker(
            self._cal_worker, self._on_calibrate_ok, self._on_calibrate_err,
        )

    def _on_calibrate_ok(self, res: MziResult) -> None:
        if res.fsr_hz is None:
            fsr_fb = self.settings.optical.tau_fallback_hz
            tau_ns = 1e9 / fsr_fb
            self.settings.optical.apply_calibrated_fsr(fsr_fb)
            self.settings.set_calibrated(True)
            # Surface why detection failed — a bogus carrier (e.g. from a torn
            # or triggerless frame) collapses the search band and hides the dip.
            self.statusBar().showMessage(
                f"No FSR dip found (carrier {res.carrier_hz / 1e6:.2f} MHz, "
                f"searched {res.search_lo / 1e6:.2f}–{res.search_hi / 1e6:.2f} MHz)"
                f" — using configured τ = {tau_ns:.1f} ns (FSR {fsr_fb / 1e6:.4f} MHz)."
            )
            self._start_process()
            return
        self.settings.optical.apply_calibrated_fsr(res.fsr_hz)
        self.settings.set_calibrated(True)
        verdict = "reliable" if res.reliable else "weak — verify manually"
        self.statusBar().showMessage(
            f"Calibrated FSR = {res.fsr_hz/1e6:.4f} MHz "
            f"→ ΔL ≈ {res.delta_L_m:.3f} m  ({verdict}, "
            f"contrast {res.contrast:.0f}×)."
        )
        # A good FSR is the only thing Process was waiting on, so run it
        # automatically. This covers every path that reaches calibration —
        # CSV load, scope acquire, and manual re-calibrate — so the user
        # never needs a separate Process click.
        self._start_process()

    def _on_calibrate_err(self, message: str) -> None:
        self.settings.optical.calibration_failed()
        self.settings.set_calibrated(False)
        self.statusBar().showMessage("Calibration failed.")
        if self._cal_user_initiated:
            QMessageBox.critical(self, "Calibration failed", message)

    # ---------- export ----------

    def _export(self) -> None:
        if self._result is None:
            return
        snap = self.settings.snapshot()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export spectra", "noise_spectrum.csv", "CSV (*.csv)",
        )
        if not path:
            return
        r = self._result

        # Export both the frequency-noise (Sν) and phase-noise (Sφ) spectra in
        # one file, independent of the on-screen display toggle. BPD2 columns
        # are only meaningful when a second channel was acquired.
        has_bpd2 = r.request.v2 is not None
        cols = {
            "S_nu_BPD1_Hz2_per_Hz": r.s_nu_11,
        }
        if has_bpd2:
            cols["S_nu_BPD2_Hz2_per_Hz"] = r.s_nu_22
        cols["S_nu_cross_Hz2_per_Hz"] = r.s_nu_12
        cols["S_nu_cross_err_Hz2_per_Hz"] = r.s_nu_12_err
        cols["S_phi_BPD1_rad2_per_Hz"] = r.s_phi_11
        if has_bpd2:
            cols["S_phi_BPD2_rad2_per_Hz"] = r.s_phi_22
        cols["S_phi_cross_rad2_per_Hz"] = r.s_phi_12
        cols["S_phi_cross_err_rad2_per_Hz"] = r.s_phi_12_err

        lz = fit_lorentz_floor(r.freq, r.s_nu_12,
                               f_min=snap.lorentz_f_min,
                               f_max=snap.lorentz_f_max)
        beta = integrate_beta(r.freq, r.s_nu_12,
                              f_min=snap.beta_f_min,
                              f_max=snap.beta_f_max)
        meta = {
            "spectrum_convention": "single-sideband (SSB)",
            "delay_length_m": (f"{snap.delay_length_m:.6g}"
                               if snap.delay_length_m is not None else "n/a"),
            "n_core": f"{snap.n_core}",
            "FSR_Hz": (f"{snap.delay_freq_hz:.6f}"
                       if snap.delay_freq_hz is not None else "n/a"),
            "AOM_carrier_MHz": f"{snap.aom_freq_mhz}",
            "bw_segment_Hz": ",".join(f"{v:.0f}" for v in snap.bw_segment_hz),
            "offset_start_ratio": f"{snap.offset_start_ratio}",
            "sample_rate_Hz": f"{(self._data.sample_rate if self._data else 0):.0f}",
        }
        if lz is not None:
            meta["lorentz_FWHM_Hz"] = f"{lz.fwhm_hz:.6g}"
            meta["lorentz_S0_Hz2_per_Hz"] = f"{lz.s0_hz2_per_hz:.6g}"
            meta["lorentz_fit_band_Hz"] = f"{lz.f_min:.0f}-{lz.f_max:.0f}"
        if beta is not None:
            meta["beta_FWHM_gauss_Hz"] = f"{beta.fwhm_gauss_hz:.6g}"
            meta["beta_area_Hz2"] = f"{beta.area_hz2:.6g}"
            meta["beta_integration_band_Hz"] = f"{beta.f_min:.0f}-{beta.f_max:.0f}"
        try:
            export_spectrum(Path(path), r.freq, cols, metadata=meta)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Exported → {path}")
