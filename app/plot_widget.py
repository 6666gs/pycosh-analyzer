"""Matplotlib plot widget for noise-spectrum display."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .analysis import BETA_SLOPE, BetaIntegration, LorentzFit, beta_line
from .processor import ProcessResult


@dataclass(frozen=True)
class DisplayOptions:
    """User selections that control what's drawn."""
    noise_type: str             # "frequency" -> S_nu, "phase" -> S_phi
    show_bpd1: bool
    show_bpd2: bool
    show_cross: bool
    show_errorband: bool
    show_beta_line: bool
    show_lorentz_floor: bool
    lorentz_fit: LorentzFit | None    # already computed by MainWindow
    beta_fit: BetaIntegration | None


class SpectrumPlot(QWidget):
    """Matplotlib FigureCanvas embedded in a Qt widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(8, 5), facecolor="white")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setStyleSheet(
            "QToolBar { background: #F5F5F7; border: none; padding: 2px; }"
            "QToolButton { padding: 4px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        self._ax = self.figure.add_subplot(111)
        self._draw_placeholder()

    # --- public API ---
    def clear(self) -> None:
        self._ax.clear()
        self._draw_placeholder()
        self.canvas.draw_idle()

    def render(self, result: ProcessResult, options: DisplayOptions) -> None:
        self._ax.clear()

        freq = result.freq
        if options.noise_type == "phase":
            y_label = r"$S_\varphi$  (rad$^2$/Hz)"
            y_11 = result.s_phi_11
            y_22 = result.s_phi_22
            y_12 = result.s_phi_12
            y_12_err = result.s_phi_12_err
            title = "Phase noise spectrum"
        else:
            y_label = r"$S_\nu$  (Hz$^2$/Hz)"
            y_11 = result.s_nu_11
            y_22 = result.s_nu_22
            y_12 = result.s_nu_12
            y_12_err = result.s_nu_12_err
            title = "Frequency noise spectrum"

        if options.show_bpd1:
            self._ax.loglog(freq, y_11, label="BPD1 (single)",
                            color="#8E8E93", alpha=0.7, linewidth=1.0)
        if options.show_bpd2 and result.request.v2 is not None:
            self._ax.loglog(freq, y_22, label="BPD2 (single)",
                            color="#C7C7CC", alpha=0.7, linewidth=1.0,
                            linestyle="--")
        if options.show_cross:
            self._ax.loglog(freq, y_12, label="Cross-correlation",
                            color="#007AFF", linewidth=1.6)
            if options.show_errorband:
                lo = np.clip(y_12 - y_12_err, 1e-30, None)
                hi = y_12 + y_12_err
                self._ax.fill_between(freq, lo, hi, color="#007AFF", alpha=0.18)

        # β-separation line + Lorentz floor only make sense for Sν
        if options.noise_type == "frequency":
            if options.show_beta_line:
                self._ax.loglog(
                    freq, beta_line(freq),
                    color="#FF9500", linewidth=1.0, linestyle=":",
                    label=fr"β-line  (8 ln2/π² · f, slope {BETA_SLOPE:.3f})",
                )
            if options.show_lorentz_floor and options.lorentz_fit is not None:
                s0 = options.lorentz_fit.s0_hz2_per_hz
                fwhm = options.lorentz_fit.fwhm_hz
                self._ax.axhline(
                    s0, color="#FF3B30", linestyle="--", linewidth=1.0,
                    label=(f"Lorentz floor  S₀ = {s0:.3g} Hz²/Hz  "
                           f"→ FWHM = {_format_hz(fwhm)}"),
                )

        self._ax.set_xlabel("Offset frequency (Hz)", color="#1D1D1F")
        self._ax.set_ylabel(y_label, color="#1D1D1F")

        # Add a subtitle with β-integration result if available
        if (options.noise_type == "frequency"
                and options.beta_fit is not None):
            subtitle = (
                f"β-integrated Gaussian FWHM = "
                f"{_format_hz(options.beta_fit.fwhm_gauss_hz)}"
                f"   (area = {options.beta_fit.area_hz2:.3g} Hz²)"
            )
            self._ax.set_title(f"{title}\n{subtitle}",
                               color="#1D1D1F", fontsize=11, pad=10)
        else:
            self._ax.set_title(title, color="#1D1D1F", fontsize=12, pad=10)

        self._ax.grid(True, which="both", alpha=0.25, linewidth=0.5)
        self._ax.tick_params(colors="#1D1D1F")
        for spine in self._ax.spines.values():
            spine.set_color("#E5E5EA")
        self._ax.set_facecolor("white")

        if self._ax.has_data():
            self._ax.legend(loc="best", frameon=True, framealpha=0.95,
                            edgecolor="#E5E5EA", fontsize=9)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    # --- internal ---
    def _draw_placeholder(self) -> None:
        self._ax.clear()
        self._ax.text(
            0.5, 0.5,
            "Load data and press Process to display the noise spectrum.",
            ha="center", va="center", transform=self._ax.transAxes,
            color="#86868B", fontsize=12,
        )
        self._ax.set_xticks([])
        self._ax.set_yticks([])
        for spine in self._ax.spines.values():
            spine.set_color("#E5E5EA")
        self._ax.set_facecolor("white")
        self.figure.tight_layout()


def _format_hz(value: float) -> str:
    """Pretty-print a Hz value with auto-scaled unit (Hz / kHz / MHz)."""
    if value >= 1e6:
        return f"{value / 1e6:.3g} MHz"
    if value >= 1e3:
        return f"{value / 1e3:.3g} kHz"
    return f"{value:.3g} Hz"
