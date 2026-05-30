"""Main application window wiring all sub-panels together."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QWidget,
)

from .analysis import fit_lorentz_floor, integrate_beta
from .data_io import (
    DualBpdData,
    export_spectrum,
    from_arrays,
    load_csv,
    load_two_csv,
    save_dual_bpd_csv,
)
from .mzi_calibrate import MziResult
from .plot_widget import DisplayOptions, SpectrumPlot, _format_hz
from .processor import (
    CalibrateWorker,
    ProcessRequest,
    ProcessResult,
    ProcessWorker,
)
from .scope import AcquireWorker, frame_to_arrays
from .settings_panel import (
    MODE_ACQUIRE,
    MODE_SINGLE_CSV,
    MODE_TWO_CSV,
    SettingsPanel,
)


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
        # Whether the calibration currently running was launched by an
        # explicit user click (True) or automatically on data load (False).
        # Used to decide whether failure popups appear.
        self._cal_user_initiated: bool = False

        self.settings = SettingsPanel()
        self.plot = SpectrumPlot()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.settings)
        layout.addWidget(self.plot, 1)
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
        self.settings.saveAcquiredRequested.connect(self._save_acquired)

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
                self._data = load_csv(f1)
            else:  # MODE_TWO_CSV
                if not f2:
                    self.settings.data.set_info(
                        f"Loaded BPD1 ({f1.name}). Select BPD2 to enable processing."
                    )
                    return
                self._data = load_two_csv(f1, f2)
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
        default = (f"acquired_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save acquired data", default, "CSV (*.csv)",
        )
        if not path:
            return
        try:
            save_dual_bpd_csv(Path(path), self._data)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved acquired data → {path}")

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
            self.settings.optical.calibration_failed()
            self.settings.set_calibrated(False)
            self.statusBar().showMessage(
                "Calibration: no clean FSR zero detected."
            )
            if self._cal_user_initiated:
                QMessageBox.information(
                    self, "Calibration",
                    "FSR auto-calibration could not find a clean MZI fringe "
                    f"zero (carrier ≈ {res.carrier_hz/1e6:.3f} MHz). "
                    "Process is locked until calibration succeeds — try a "
                    "longer record or check that the beat signal is clean."
                )
            return
        self.settings.optical.apply_calibrated_fsr(res.fsr_hz)
        self.settings.set_calibrated(True)
        verdict = "reliable" if res.reliable else "weak — verify manually"
        self.statusBar().showMessage(
            f"Calibrated FSR = {res.fsr_hz/1e6:.4f} MHz "
            f"→ ΔL ≈ {res.delta_L_m:.3f} m  ({verdict}, "
            f"contrast {res.contrast:.0f}×)."
        )

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
        suggested = ("noise_spectrum_phase.csv"
                     if snap.noise_type == "phase"
                     else "noise_spectrum_frequency.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export spectrum", suggested, "CSV (*.csv)",
        )
        if not path:
            return
        r = self._result
        if snap.noise_type == "phase":
            cols = {
                "S_phi_BPD1_rad2_per_Hz": r.s_phi_11,
                "S_phi_BPD2_rad2_per_Hz": r.s_phi_22,
                "S_phi_cross_rad2_per_Hz": r.s_phi_12,
                "S_phi_cross_err_rad2_per_Hz": r.s_phi_12_err,
            }
        else:
            cols = {
                "S_nu_BPD1_Hz2_per_Hz": r.s_nu_11,
                "S_nu_BPD2_Hz2_per_Hz": r.s_nu_22,
                "S_nu_cross_Hz2_per_Hz": r.s_nu_12,
                "S_nu_cross_err_Hz2_per_Hz": r.s_nu_12_err,
            }

        lz = fit_lorentz_floor(r.freq, r.s_nu_12,
                               f_min=snap.lorentz_f_min,
                               f_max=snap.lorentz_f_max)
        beta = integrate_beta(r.freq, r.s_nu_12,
                              f_min=snap.beta_f_min,
                              f_max=snap.beta_f_max)
        meta = {
            "noise_type": snap.noise_type,
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
