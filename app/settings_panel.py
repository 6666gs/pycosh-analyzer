"""Left sidebar with all user-tunable parameters."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

C_LIGHT = 299_792_458.0

SCOPE_CHANNELS = ("C1", "C2", "C3", "C4")
NONE_CH_LABEL = "(none)"

# Data source modes
MODE_SINGLE_CSV = "single_csv"
MODE_TWO_CSV = "two_csv"
MODE_ACQUIRE = "acquire"
MODE_LABELS = {
    MODE_SINGLE_CSV: "Single CSV (3 columns: t, BPD1, BPD2)",
    MODE_TWO_CSV: "Two CSVs (one per channel)",
    MODE_ACQUIRE: "Acquire from oscilloscope (SDS7404)",
}


# ---------- helpers ----------

def _section_title(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("sectionTitle")
    return label


def _card() -> QFrame:
    card = QFrame()
    card.setObjectName("sectionCard")
    return card


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "hint")
    label.setWordWrap(True)
    return label


def _metric(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "metric")
    return label


def _secondary_btn(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("variant", "secondary")
    return btn


def _shrinkable(widget: QWidget) -> QWidget:
    """Allow this widget to shrink below its content-derived sizeHint
    when its parent layout is narrower than the content (e.g. a
    QLineEdit with a long default text inside a fixed-width sidebar).
    Without this the QStackedWidget / QFormLayout forces the whole
    section card wider than the sidebar viewport and content gets
    clipped horizontally."""
    widget.setMinimumWidth(0)
    pol = widget.sizePolicy()
    pol.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    widget.setSizePolicy(pol)
    return widget


def _grow_form(form: QFormLayout) -> QFormLayout:
    """Make a form layout's field column flex with available width
    rather than freezing at the field's sizeHint."""
    form.setFieldGrowthPolicy(
        QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    )
    return form


# ---------- value object exposed to MainWindow ----------

@dataclass(frozen=True)
class SettingsSnapshot:
    """Immutable snapshot of all settings at "Process" click time."""
    # Data
    mode: str
    file1: Path | None
    file2: Path | None
    scope_host: str
    scope_ch1: str
    scope_ch2: str | None
    scope_send_single: bool
    # Optical — delay_length_m / delay_freq_hz are None until auto-calibration
    delay_length_m: float | None
    n_core: float
    aom_freq_mhz: float
    delay_freq_hz: float | None
    # Segments
    bw_segment_hz: tuple[float, ...]
    offset_start_ratio: int
    range_start: int | None
    range_stop: int | None
    # Display
    noise_type: str               # "frequency" | "phase"
    show_bpd1: bool
    show_bpd2: bool
    show_cross: bool
    show_errorband: bool
    # Analysis
    show_beta_line: bool
    show_lorentz_floor: bool
    lorentz_f_min: float
    lorentz_f_max: float
    beta_f_min: float | None      # None → auto (use full range)
    beta_f_max: float | None


# ---------- Data section ----------

class DataSection(QFrame):
    fileChanged = Signal()
    acquireRequested = Signal()
    saveAcquiredRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self._file1: Path | None = None
        self._file2: Path | None = None
        self._has_acquired: bool = False

        # Mode selector
        self.mode_combo = QComboBox()
        for key in (MODE_SINGLE_CSV, MODE_TWO_CSV, MODE_ACQUIRE):
            self.mode_combo.addItem(MODE_LABELS[key], userData=key)
        self.mode_combo.currentIndexChanged.connect(self._update_mode)
        _shrinkable(self.mode_combo)

        # --- File-mode widgets ---
        self.file1_edit = QLineEdit()
        self.file1_edit.setPlaceholderText("BPD1 / dual CSV path…")
        self.file1_edit.setReadOnly(True)
        _shrinkable(self.file1_edit)
        self.file1_btn = _secondary_btn("Browse")
        self.file1_btn.clicked.connect(lambda: self._pick(1))

        self.file2_edit = QLineEdit()
        self.file2_edit.setPlaceholderText("BPD2 path (only in two-file mode)…")
        self.file2_edit.setReadOnly(True)
        _shrinkable(self.file2_edit)
        self.file2_btn = _secondary_btn("Browse")
        self.file2_btn.clicked.connect(lambda: self._pick(2))

        file_widget = QWidget()
        file_layout = QVBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(6)
        for edit, btn in ((self.file1_edit, self.file1_btn),
                          (self.file2_edit, self.file2_btn)):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(edit, 1)
            row.addWidget(btn)
            file_layout.addLayout(row)

        # --- Scope-mode widgets ---
        self.scope_ip = QLineEdit("192.168.1.50")
        self.scope_ip.setPlaceholderText("IP or hostname")
        _shrinkable(self.scope_ip)

        self.scope_ch1 = QComboBox()
        for ch in SCOPE_CHANNELS:
            self.scope_ch1.addItem(ch)
        self.scope_ch1.setCurrentText("C2")

        self.scope_ch2 = QComboBox()
        for ch in SCOPE_CHANNELS:
            self.scope_ch2.addItem(ch)
        self.scope_ch2.addItem(NONE_CH_LABEL)
        self.scope_ch2.setCurrentText("C4")

        self.scope_single = QCheckBox("Send SINGle trigger first")

        self.acquire_btn = QPushButton("⏺  Acquire from scope")
        self.acquire_btn.setProperty("variant", "secondary")
        self.acquire_btn.clicked.connect(self.acquireRequested.emit)

        self.save_acquired_btn = _secondary_btn("Save acquired CSV…")
        self.save_acquired_btn.setEnabled(False)
        self.save_acquired_btn.clicked.connect(self.saveAcquiredRequested.emit)

        scope_widget = QWidget()
        scope_form = _grow_form(QFormLayout(scope_widget))
        scope_form.setContentsMargins(0, 0, 0, 0)
        scope_form.setSpacing(6)
        scope_form.addRow("Scope IP", self.scope_ip)
        scope_form.addRow("BPD1 channel", self.scope_ch1)
        scope_form.addRow("BPD2 channel", self.scope_ch2)
        scope_form.addRow("", self.scope_single)
        # Stack the two action buttons vertically: side-by-side they would
        # force a minimum row width that overflows the sidebar (the
        # QStackedWidget reserves the wider page's sizeHint for both modes).
        scope_btn_col = QVBoxLayout()
        scope_btn_col.setSpacing(6)
        scope_btn_col.setContentsMargins(0, 0, 0, 0)
        scope_btn_col.addWidget(self.acquire_btn)
        scope_btn_col.addWidget(self.save_acquired_btn)
        scope_form.addRow("", self._row_widget(scope_btn_col))

        # Single stack: page 0 = file pickers (shared by single_csv + two_csv,
        # the file2 row is just disabled in single_csv mode), page 1 = scope.
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(file_widget)     # page 0
        self.mode_stack.addWidget(scope_widget)    # page 1

        self.info_label = _hint("No data loaded.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.mode_combo)
        layout.addWidget(self.mode_stack)
        layout.addWidget(self.info_label)

        self._update_mode()

    @staticmethod
    def _row_widget(layout: QHBoxLayout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    # ---- public ----
    @property
    def mode(self) -> str:
        return self.mode_combo.currentData() or MODE_SINGLE_CSV

    @property
    def file1(self) -> Path | None:
        return self._file1

    @property
    def file2(self) -> Path | None:
        return self._file2

    @property
    def scope_host(self) -> str:
        return self.scope_ip.text().strip()

    @property
    def scope_ch1_name(self) -> str:
        return self.scope_ch1.currentText()

    @property
    def scope_ch2_name(self) -> str | None:
        text = self.scope_ch2.currentText()
        return None if text == NONE_CH_LABEL else text

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)

    def mark_acquired(self, acquired: bool) -> None:
        self._has_acquired = acquired
        self.save_acquired_btn.setEnabled(acquired)

    def _update_mode(self) -> None:
        mode = self.mode
        if mode == MODE_ACQUIRE:
            self.mode_stack.setCurrentIndex(1)
        else:
            self.mode_stack.setCurrentIndex(0)
            two_file = (mode == MODE_TWO_CSV)
            self.file2_edit.setEnabled(two_file)
            self.file2_btn.setEnabled(two_file)
            if not two_file:
                self._file2 = None
                self.file2_edit.clear()
        self.fileChanged.emit()

    def _pick(self, idx: int) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CSV", "", "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        if idx == 1:
            self._file1 = Path(path)
            self.file1_edit.setText(path)
        else:
            self._file2 = Path(path)
            self.file2_edit.setText(path)
        self.fileChanged.emit()


# ---------- Optical section ----------

class _FsrState(Enum):
    IDLE = auto()         # no data loaded yet — Process locked
    CALIBRATING = auto()  # auto-cal worker in flight
    CALIBRATED = auto()   # got a clean FSR — Process unlocked
    FAILED = auto()       # cal ran but found no usable zero


# Label text for every non-CALIBRATED state. CALIBRATED writes a derived
# string in `_refresh_display`, so it isn't listed here.
_FSR_STATE_TEXTS: dict[_FsrState, tuple[str, str]] = {
    _FsrState.IDLE:        ("FSR: not calibrated (load data first)", "Fiber length: —"),
    _FsrState.CALIBRATING: ("Calibrating FSR from data …",           "Fiber length: —"),
    _FsrState.FAILED:      ("FSR: auto-calibration failed",           "Fiber length: —"),
}


class OpticalSection(QFrame):
    """Optical path. FSR is auto-detected from the loaded data — the
    user only sets n_core (refractive index) and the AOM carrier; the
    fiber length is derived from FSR and shown for human reference.

    Calibration runs through an explicit 4-state machine (`_FsrState`)
    so the label text and Re-calibrate-button enablement live in a
    single place (`_apply_state`)."""
    calibrateRequested = Signal()
    fsrChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")

        self._fsr_hz: float | None = None
        self._state: _FsrState = _FsrState.IDLE

        self.n_core = QDoubleSpinBox()
        self.n_core.setRange(1.0, 2.0)
        self.n_core.setDecimals(4)
        self.n_core.setSingleStep(0.001)
        self.n_core.setValue(1.468)
        self.n_core.valueChanged.connect(self._refresh_display)
        _shrinkable(self.n_core)

        self.aom_freq = QDoubleSpinBox()
        self.aom_freq.setRange(0.0, 5_000.0)
        self.aom_freq.setSuffix(" MHz")
        self.aom_freq.setDecimals(3)
        self.aom_freq.setValue(80.0)
        _shrinkable(self.aom_freq)

        self.fsr_display = _metric(_FSR_STATE_TEXTS[_FsrState.IDLE][0])
        self.fsr_display.setWordWrap(True)
        self.delay_len_display = _metric(_FSR_STATE_TEXTS[_FsrState.IDLE][1])
        self.delay_len_display.setWordWrap(True)

        self.calibrate_btn = _secondary_btn("Re-calibrate FSR")
        self.calibrate_btn.setEnabled(False)
        self.calibrate_btn.clicked.connect(self.calibrateRequested.emit)

        # n_core + AOM share one row to save vertical space
        editable_row = QHBoxLayout()
        editable_row.setSpacing(6)
        nlbl = QLabel("n")
        nlbl.setMinimumWidth(14)
        editable_row.addWidget(nlbl)
        editable_row.addWidget(self.n_core, 1)
        editable_row.addSpacing(8)
        editable_row.addWidget(QLabel("AOM"))
        editable_row.addWidget(self.aom_freq, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(editable_row)
        layout.addWidget(self.fsr_display)
        layout.addWidget(self.delay_len_display)
        layout.addWidget(self.calibrate_btn)

    # ---- state ----
    @property
    def is_calibrated(self) -> bool:
        return self._state == _FsrState.CALIBRATED

    @property
    def delay_freq_hz(self) -> float | None:
        return self._fsr_hz

    @property
    def delay_length_m(self) -> float | None:
        if self._fsr_hz is None:
            return None
        return C_LIGHT / (self.n_core.value() * self._fsr_hz)

    # ---- transitions driven by MainWindow ----
    def set_calibrating(self) -> None:
        self._apply_state(_FsrState.CALIBRATING)

    def apply_calibrated_fsr(self, fsr_hz: float) -> None:
        self._fsr_hz = fsr_hz
        self._apply_state(_FsrState.CALIBRATED)

    def calibration_failed(self) -> None:
        """Called when auto-cal couldn't find a clean FSR zero."""
        self._fsr_hz = None
        self._apply_state(_FsrState.FAILED)

    def reset_calibration(self) -> None:
        """Called when the loaded data changes — invalidate the old FSR."""
        self._fsr_hz = None
        self._apply_state(_FsrState.IDLE)

    # ---- internal ----
    def _apply_state(self, state: _FsrState) -> None:
        """Single source of truth for label text + button-enabled. Every
        state transition goes through here so the four states can't
        desync."""
        self._state = state
        if state == _FsrState.CALIBRATED:
            self._refresh_display()
            self.calibrate_btn.setEnabled(True)
        else:
            text_fsr, text_len = _FSR_STATE_TEXTS[state]
            self.fsr_display.setText(text_fsr)
            self.delay_len_display.setText(text_len)
            # Allow retry after a real failure; lock during cal / when idle.
            self.calibrate_btn.setEnabled(state == _FsrState.FAILED)
        self.fsrChanged.emit()

    def _refresh_display(self) -> None:
        """Refresh the FSR / fiber-length text. Called by `_apply_state`
        on transition to CALIBRATED, and by `n_core.valueChanged` when
        the user edits the refractive index after calibration."""
        if self._fsr_hz is None:
            return
        fsr_mhz = self._fsr_hz / 1e6
        tau_ns = 1e9 / self._fsr_hz
        # Read through the property so the C_LIGHT/(n·fsr) compute lives
        # in one place.
        delay_m = self.delay_length_m
        self.fsr_display.setText(
            f"FSR  {fsr_mhz:.4f} MHz   (τ = {tau_ns:.2f} ns)"
        )
        self.delay_len_display.setText(f"Fiber  {delay_m:.4f} m")


# ---------- Segment section ----------

class SegmentSection(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")

        self.bw_edit = QLineEdit("1, 3, 10, 30, 100, 300, 1000, 3000, 10000")
        self.bw_edit.setPlaceholderText("comma-separated, in kHz")
        _shrinkable(self.bw_edit)

        self.ratio = QSpinBox()
        self.ratio.setRange(2, 200)
        self.ratio.setValue(10)

        self.range_start = QLineEdit()
        self.range_start.setPlaceholderText("auto")
        _shrinkable(self.range_start)
        self.range_stop = QLineEdit()
        self.range_stop.setPlaceholderText("auto")
        _shrinkable(self.range_stop)

        form = _grow_form(QFormLayout())
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("BW bins (kHz)", self.bw_edit)

        ratio_range_row = QHBoxLayout()
        ratio_range_row.setSpacing(6)
        ratio_range_row.addWidget(QLabel("ratio"))
        ratio_range_row.addWidget(self.ratio)
        ratio_range_row.addSpacing(8)
        ratio_range_row.addWidget(QLabel("range"))
        ratio_range_row.addWidget(self.range_start)
        sep = QLabel("→")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ratio_range_row.addWidget(sep)
        ratio_range_row.addWidget(self.range_stop)
        form.addRow("", ratio_range_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(form)

    def parsed_bw_hz(self) -> tuple[float, ...]:
        raw = self.bw_edit.text().replace(";", ",")
        try:
            khz_values = [float(x) for x in raw.split(",") if x.strip()]
        except ValueError as exc:
            raise ValueError(f"Could not parse bw list: {exc}") from exc
        if len(khz_values) < 2:
            raise ValueError("Need at least two bandwidth values.")
        hz_values = sorted({round(v * 1000.0, 6) for v in khz_values})
        return tuple(hz_values)

    def parsed_range(self) -> tuple[int | None, int | None]:
        def _parse(text: str) -> int | None:
            t = text.strip()
            return int(t) if t else None
        return _parse(self.range_start.text()), _parse(self.range_stop.text())


# ---------- Display section ----------

class DisplaySection(QFrame):
    optionsChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")

        self.r_freq = QRadioButton("Frequency noise  Sν")
        self.r_phase = QRadioButton("Phase noise  Sφ")
        self.r_freq.setChecked(True)

        self.c_bpd1 = QCheckBox("BPD1 single")
        self.c_bpd2 = QCheckBox("BPD2 single")
        self.c_cross = QCheckBox("Cross-correlation")
        self.c_cross.setChecked(True)
        self.c_errband = QCheckBox("Show error band")
        self.c_errband.setChecked(True)

        for w in (self.r_freq, self.r_phase, self.c_bpd1, self.c_bpd2,
                  self.c_cross, self.c_errband):
            w.toggled.connect(self.optionsChanged.emit)

        # Compact: 2 radio buttons on one row, 4 checkboxes on a 2×2 grid
        radio_row = QHBoxLayout()
        radio_row.setSpacing(12)
        radio_row.addWidget(self.r_freq)
        radio_row.addWidget(self.r_phase)
        radio_row.addStretch(1)

        chk_row1 = QHBoxLayout()
        chk_row1.setSpacing(12)
        chk_row1.addWidget(self.c_bpd1)
        chk_row1.addWidget(self.c_bpd2)
        chk_row1.addStretch(1)

        chk_row2 = QHBoxLayout()
        chk_row2.setSpacing(12)
        chk_row2.addWidget(self.c_cross)
        chk_row2.addWidget(self.c_errband)
        chk_row2.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(radio_row)
        layout.addLayout(chk_row1)
        layout.addLayout(chk_row2)


# ---------- Analysis section (Lorentz floor + β-separation) ----------

class AnalysisSection(QFrame):
    optionsChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")

        # Toggles. Text kept short so the two checkboxes fit on one row
        # inside a narrow sidebar without forcing horizontal overflow.
        self.c_show_beta = QCheckBox("β-line")
        self.c_show_beta.setChecked(True)
        self.c_show_lorentz = QCheckBox("Lorentz floor")
        self.c_show_lorentz.setChecked(True)
        for w in (self.c_show_beta, self.c_show_lorentz):
            w.toggled.connect(self.optionsChanged.emit)

        # Lorentz fit range
        self.lorentz_fmin = QLineEdit("1e6")
        self.lorentz_fmax = QLineEdit("1e7")
        self.lorentz_fmin.editingFinished.connect(self.optionsChanged.emit)
        self.lorentz_fmax.editingFinished.connect(self.optionsChanged.emit)
        _shrinkable(self.lorentz_fmin)
        _shrinkable(self.lorentz_fmax)

        # β integration range (None = auto = use full freq range)
        self.beta_fmin = QLineEdit()
        self.beta_fmin.setPlaceholderText("auto")
        self.beta_fmax = QLineEdit()
        self.beta_fmax.setPlaceholderText("auto")
        self.beta_fmin.editingFinished.connect(self.optionsChanged.emit)
        self.beta_fmax.editingFinished.connect(self.optionsChanged.emit)
        _shrinkable(self.beta_fmin)
        _shrinkable(self.beta_fmax)

        # Result displays — wrap long lines instead of forcing card wider
        self.lorentz_display = _metric("Lorentz FWHM: —")
        self.lorentz_display.setWordWrap(True)
        self.beta_display = _metric("β-FWHM (Gauss): —")
        self.beta_display.setWordWrap(True)

        form = _grow_form(QFormLayout())
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)

        lz_row = QHBoxLayout()
        lz_row.setSpacing(6)
        lz_row.addWidget(self.lorentz_fmin)
        lab = QLabel("→")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lz_row.addWidget(lab)
        lz_row.addWidget(self.lorentz_fmax)
        form.addRow("Lorentz fit (Hz)", DataSection._row_widget(lz_row))

        bt_row = QHBoxLayout()
        bt_row.setSpacing(6)
        bt_row.addWidget(self.beta_fmin)
        lab2 = QLabel("→")
        lab2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bt_row.addWidget(lab2)
        bt_row.addWidget(self.beta_fmax)
        form.addRow("β integration (Hz)", DataSection._row_widget(bt_row))

        toggles_row = QHBoxLayout()
        toggles_row.setSpacing(12)
        toggles_row.addWidget(self.c_show_beta)
        toggles_row.addWidget(self.c_show_lorentz)
        toggles_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(toggles_row)
        layout.addLayout(form)
        layout.addWidget(self.lorentz_display)
        layout.addWidget(self.beta_display)

    def update_results(self, lorentz_text: str, beta_text: str) -> None:
        self.lorentz_display.setText(lorentz_text)
        self.beta_display.setText(beta_text)

    def parse_float(self, edit: QLineEdit) -> float | None:
        t = edit.text().strip()
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None


# ---------- main panel ----------

class SettingsPanel(QWidget):
    processRequested = Signal()
    exportRequested = Signal()
    optionsChanged = Signal()
    fileChanged = Signal()
    calibrateRequested = Signal()
    acquireRequested = Signal()
    saveAcquiredRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        # Width range chosen so even the widest section content (DataSection
        # in scope mode) fits at the minimum without horizontal clipping.
        self.setMinimumWidth(380)
        self.setMaximumWidth(440)

        self.data = DataSection()
        self.optical = OpticalSection()
        self.segments = SegmentSection()
        self.display = DisplaySection()
        self.analysis = AnalysisSection()

        self.process_btn = QPushButton("▶  Process")
        self.process_btn.setMinimumHeight(44)
        process_font = self.process_btn.font()
        process_font.setPointSize(process_font.pointSize() + 1)
        process_font.setWeight(process_font.Weight.DemiBold)
        self.process_btn.setFont(process_font)
        self.process_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #007AFF; color: white;"
            "  border: none; border-radius: 8px;"
            "  padding: 10px 16px;"
            "}"
            "QPushButton:hover    { background-color: #0066D6; }"
            "QPushButton:pressed  { background-color: #0055B3; }"
            "QPushButton:disabled { background-color: #C7C7CC; color: white; }"
        )
        # The Process button is gated by two independent flags below:
        # _busy (a process run is already in flight) and _calibrated (we
        # have a usable FSR). They're composed in `_refresh_process_btn`.
        self._busy: bool = False
        self._calibrated: bool = False
        self.process_btn.setEnabled(False)

        self.export_btn = _secondary_btn("Export spectrum…")
        self.export_btn.setEnabled(False)

        # forward signals
        self.data.fileChanged.connect(self.fileChanged.emit)
        self.data.acquireRequested.connect(self.acquireRequested.emit)
        self.data.saveAcquiredRequested.connect(self.saveAcquiredRequested.emit)
        self.display.optionsChanged.connect(self.optionsChanged.emit)
        self.analysis.optionsChanged.connect(self.optionsChanged.emit)
        self.optical.calibrateRequested.connect(self.calibrateRequested.emit)
        self.process_btn.clicked.connect(self.processRequested.emit)
        self.export_btn.clicked.connect(self.exportRequested.emit)

        # Sections live in a vertical-scroll-only container. We never
        # want the sidebar to scroll horizontally — when the user shrinks
        # the window, content should clip / wrap, not slide sideways —
        # and we want vertical scroll only as a fallback when the window
        # is too short to show every section at its natural size.
        sections_widget = QWidget()
        sections_layout = QVBoxLayout(sections_widget)
        sections_layout.setContentsMargins(16, 16, 16, 8)
        sections_layout.setSpacing(8)
        for title, widget in (
            ("Data", self.data),
            ("Optical path", self.optical),
            ("Segments", self.segments),
            ("Display", self.display),
            ("Analysis", self.analysis),
        ):
            sections_layout.addWidget(_section_title(title))
            sections_layout.addWidget(widget)
        sections_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("sidebarScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setWidget(sections_widget)

        button_row = QFrame()
        button_row.setStyleSheet(
            "background: transparent; border-top: 1px solid #E5E5EA;"
        )
        btn_layout = QVBoxLayout(button_row)
        btn_layout.setContentsMargins(16, 10, 16, 12)
        btn_layout.setSpacing(8)
        btn_layout.addWidget(self.process_btn)
        btn_layout.addWidget(self.export_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll, 1)
        layout.addWidget(button_row)

    def set_export_enabled(self, enabled: bool) -> None:
        self.export_btn.setEnabled(enabled)

    def set_processing(self, busy: bool) -> None:
        self._busy = busy
        self.process_btn.setText("Processing…" if busy else "▶  Process")
        self._refresh_process_btn()

    def set_calibrated(self, calibrated: bool) -> None:
        """Called by MainWindow after auto-cal succeeds/fails."""
        self._calibrated = calibrated
        self._refresh_process_btn()

    def _refresh_process_btn(self) -> None:
        self.process_btn.setEnabled(self._calibrated and not self._busy)

    def set_acquiring(self, busy: bool) -> None:
        self.data.acquire_btn.setEnabled(not busy)
        self.data.acquire_btn.setText(
            "Acquiring…" if busy else "⏺  Acquire from scope"
        )

    def snapshot(self) -> SettingsSnapshot:
        rstart, rstop = self.segments.parsed_range()
        lz_min = self.analysis.parse_float(self.analysis.lorentz_fmin) or 1e6
        lz_max = self.analysis.parse_float(self.analysis.lorentz_fmax) or 1e7
        return SettingsSnapshot(
            mode=self.data.mode,
            file1=self.data.file1,
            file2=self.data.file2,
            scope_host=self.data.scope_host,
            scope_ch1=self.data.scope_ch1_name,
            scope_ch2=self.data.scope_ch2_name,
            scope_send_single=self.data.scope_single.isChecked(),
            delay_length_m=self.optical.delay_length_m,
            n_core=self.optical.n_core.value(),
            aom_freq_mhz=self.optical.aom_freq.value(),
            delay_freq_hz=self.optical.delay_freq_hz,
            bw_segment_hz=self.segments.parsed_bw_hz(),
            offset_start_ratio=self.segments.ratio.value(),
            range_start=rstart,
            range_stop=rstop,
            noise_type=("phase" if self.display.r_phase.isChecked()
                        else "frequency"),
            show_bpd1=self.display.c_bpd1.isChecked(),
            show_bpd2=self.display.c_bpd2.isChecked(),
            show_cross=self.display.c_cross.isChecked(),
            show_errorband=self.display.c_errband.isChecked(),
            show_beta_line=self.analysis.c_show_beta.isChecked(),
            show_lorentz_floor=self.analysis.c_show_lorentz.isChecked(),
            lorentz_f_min=lz_min,
            lorentz_f_max=lz_max,
            beta_f_min=self.analysis.parse_float(self.analysis.beta_fmin),
            beta_f_max=self.analysis.parse_float(self.analysis.beta_fmax),
        )
