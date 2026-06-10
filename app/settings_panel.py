"""Left sidebar with all user-tunable parameters."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import Qt, Signal

from .data_io import is_data_path
from PySide6.QtWidgets import (
    QButtonGroup,
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
    QVBoxLayout,
    QWidget,
)

C_LIGHT = 299_792_458.0


# ---------- accidental-scroll-proof input widgets ----------
# Spin boxes and combos eat wheel events by default, so scrolling the sidebar
# over one silently changes its value. These variants ignore the wheel unless
# the widget is focused (click first), letting the scroll reach the panel.

class _NoWheelMixin:
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt override name)
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    pass


class NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    pass


class NoWheelComboBox(_NoWheelMixin, QComboBox):
    pass


SCOPE_CHANNELS = ("C1", "C2", "C3", "C4")
NONE_CH_LABEL = "(none)"

# ---- Algorithm (top-level tab) × data source (sub-toggle) ----
# The data panel is organised along two orthogonal axes: which processing
# algorithm runs, and where its data comes from.
ALGO_XCORR = "xcorr"        # BW-segmented dual-BPD cross-correlation (CoshXcorr)
ALGO_AVERAGE = "avg"        # multi-record single-BPD Hann averaging
ALGO_LABELS = {
    ALGO_XCORR: "BW 分段 · 双 BPD",
    ALGO_AVERAGE: "多次平均 · 单 BPD (Hann)",
}

SRC_FILE = "file"
SRC_SCOPE = "scope"
SRC_LABELS = {SRC_FILE: "文件读取", SRC_SCOPE: "从示波器读取"}

# Legacy compatibility modes derived from (algorithm, source). Consumed by
# MainWindow routing and Monitor gating so most of that code is untouched.
MODE_SINGLE_CSV = "single_csv"      # (XCORR, FILE)  — single 3-col file
MODE_ACQUIRE = "acquire"            # (XCORR, SCOPE) — dual-channel grab
MODE_AVERAGE = "average"            # (AVG,   SCOPE) — acquire ×N & average
MODE_AVERAGE_FILE = "average_file"  # (AVG,   FILE)  — average a multi-record file


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
    algorithm: str
    source: str
    mode: str
    file1: Path | None
    avg_file: Path | None
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

# Scope connection-indicator states → (dot colour, label text).
CONN_STATES = {
    "idle":    ("#8E8E93", "Not tested"),
    "testing": ("#FF9F0A", "Testing…"),
    "ok":      ("#34C759", "Connected"),
    "fail":    ("#FF3B30", "No connection"),
}


_SEGMENT_QSS = (
    'QPushButton[variant="segment"] {'
    '  background:#F2F2F7; color:#1C1C1E; border:1px solid #D1D1D6;'
    '  padding:7px 8px; border-radius:7px;'
    '}'
    'QPushButton[variant="segment"]:checked {'
    '  background:#007AFF; color:white; border-color:#007AFF; font-weight:600;'
    '}'
    'QPushButton[variant="segment"]:hover:!checked { background:#E5E5EA; }'
)


def _segment_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCheckable(True)
    btn.setProperty("variant", "segment")
    btn.setFocusPolicy(Qt.NoFocus)
    return btn


class DataSection(QFrame):
    """Data source panel organised along two axes: the processing *algorithm*
    (top tab) and its *data source* (file vs scope). The content groups below
    are shown/hidden for the active (algorithm, source) pair."""
    fileChanged = Signal()
    algorithmChanged = Signal(str)
    acquireRequested = Signal()
    saveAcquiredRequested = Signal()
    saveAveragedRequested = Signal()
    saveRawRecordsRequested = Signal()
    testConnectionRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self._file1: Path | None = None
        self._avg_file: Path | None = None
        self._has_acquired: bool = False
        self._raw_available: bool = False

        # ---- algorithm tab + source toggle (the two-axis selector) ----
        self.algo_xcorr_btn = _segment_button(ALGO_LABELS[ALGO_XCORR])
        self.algo_avg_btn = _segment_button(ALGO_LABELS[ALGO_AVERAGE])
        self.algo_xcorr_btn.setChecked(True)
        self._algo_group = QButtonGroup(self)
        self._algo_group.setExclusive(True)
        self._algo_group.addButton(self.algo_xcorr_btn)
        self._algo_group.addButton(self.algo_avg_btn)
        self._algo_group.buttonClicked.connect(self._on_algorithm_clicked)

        self.src_file_btn = _segment_button(SRC_LABELS[SRC_FILE])
        self.src_scope_btn = _segment_button(SRC_LABELS[SRC_SCOPE])
        self.src_file_btn.setChecked(True)
        self._src_group = QButtonGroup(self)
        self._src_group.setExclusive(True)
        self._src_group.addButton(self.src_file_btn)
        self._src_group.addButton(self.src_scope_btn)
        self._src_group.buttonClicked.connect(self._on_source_clicked)

        # ---- (XCORR, FILE): one 3-column file t, BPD1, BPD2 ----
        self.file1_edit = QLineEdit()
        self.file1_edit.setPlaceholderText("Single file: t, BPD1, BPD2 (csv/npy/npz)…")
        self.file1_edit.setReadOnly(True)
        _shrinkable(self.file1_edit)
        self.file1_btn = _secondary_btn("Browse")
        self.file1_btn.clicked.connect(self._pick_file1)
        self._grp_xcorr_file = self._browse_row(self.file1_edit, self.file1_btn)

        # ---- (AVG, FILE): one multi-record file (N×BPD1) ----
        self.avg_file_edit = QLineEdit()
        self.avg_file_edit.setPlaceholderText("Multi-record file: N×BPD1 (.npz)…")
        self.avg_file_edit.setReadOnly(True)
        _shrinkable(self.avg_file_edit)
        self.avg_file_btn = _secondary_btn("Browse")
        self.avg_file_btn.clicked.connect(self._pick_avg_file)
        self._grp_avg_file = self._browse_row(self.avg_file_edit, self.avg_file_btn)

        # ---- shared scope inputs (IP, BPD1, connection light) ----
        self.scope_ip = QLineEdit("192.168.1.50")
        self.scope_ip.setPlaceholderText("IP or hostname")
        _shrinkable(self.scope_ip)
        self.scope_ch1 = NoWheelComboBox()
        for ch in SCOPE_CHANNELS:
            self.scope_ch1.addItem(ch)
        self.scope_ch1.setCurrentText("C2")
        self.test_conn_btn = _secondary_btn("Test connection")
        self.test_conn_btn.clicked.connect(self.testConnectionRequested.emit)
        self.conn_status = QLabel()
        self.conn_status.setTextFormat(Qt.RichText)
        self._conn_state = "idle"
        self.set_connection_status("idle")
        common_form = _grow_form(QFormLayout())
        common_form.setContentsMargins(0, 0, 0, 0)
        common_form.setSpacing(6)
        common_form.addRow("Scope IP", self.scope_ip)
        common_form.addRow("BPD1 channel", self.scope_ch1)
        conn_row = QHBoxLayout()
        conn_row.setSpacing(8)
        conn_row.setContentsMargins(0, 0, 0, 0)
        conn_row.addWidget(self.test_conn_btn)
        conn_row.addWidget(self.conn_status, 1)
        common_form.addRow("", self._row_widget(conn_row))
        self._grp_scope_common = self._form_widget(common_form)

        # ---- (XCORR, SCOPE) extra: BPD2 + SINGle trigger ----
        self.scope_ch2 = NoWheelComboBox()
        for ch in SCOPE_CHANNELS:
            self.scope_ch2.addItem(ch)
        self.scope_ch2.addItem(NONE_CH_LABEL)
        self.scope_ch2.setCurrentText("C4")
        self.scope_single = QCheckBox("Send SINGle trigger first")
        xs_form = _grow_form(QFormLayout())
        xs_form.setContentsMargins(0, 0, 0, 0)
        xs_form.setSpacing(6)
        xs_form.addRow("BPD2 channel", self.scope_ch2)
        xs_form.addRow("", self.scope_single)
        self._grp_xcorr_scope = self._form_widget(xs_form)

        # ---- (AVG, SCOPE) extra: Average N + opt-in keep-raw ----
        self.avg_count = NoWheelSpinBox()
        self.avg_count.setRange(2, 100_000)
        self.avg_count.setValue(10)
        self.avg_count.setToolTip("How many consecutive acquisitions to average.")
        self.keep_raw_check = QCheckBox("保留原始记录 (便于保存为多记录文件)")
        self.keep_raw_check.setToolTip(
            "Keep the N raw traces in memory so they can be saved as one "
            "multi-record file. N records may total several GB.")
        avs_form = _grow_form(QFormLayout())
        avs_form.setContentsMargins(0, 0, 0, 0)
        avs_form.setSpacing(6)
        avs_form.addRow("Average N", self.avg_count)
        avs_form.addRow("", self.keep_raw_check)
        self._grp_avg_scope = self._form_widget(avs_form)

        # ---- AVG common params (both sources): edge skip + convergence ----
        self.avg_skip = NoWheelSpinBox()
        self.avg_skip.setRange(0, 100_000_000)
        self.avg_skip.setSingleStep(1000)
        self.avg_skip.setValue(10_000)
        self.avg_skip.setToolTip("Samples trimmed from each end of every record.")
        self.avg_convergence = QCheckBox("Show convergence curves")
        avp_form = _grow_form(QFormLayout())
        avp_form.setContentsMargins(0, 0, 0, 0)
        avp_form.setSpacing(6)
        avp_form.addRow("Edge skip", self.avg_skip)
        avp_form.addRow("", self.avg_convergence)
        self._grp_avg_params = self._form_widget(avp_form)

        # ---- action / save buttons ----
        self.acquire_btn = QPushButton("⏺  Acquire from scope")
        self.acquire_btn.setProperty("variant", "secondary")
        self.acquire_btn.clicked.connect(self.acquireRequested.emit)

        self.save_acquired_btn = _secondary_btn("Save acquired (CSV/npy)…")
        self.save_acquired_btn.setEnabled(False)
        self.save_acquired_btn.clicked.connect(self.saveAcquiredRequested.emit)

        self.save_averaged_btn = _secondary_btn("Save averaged spectrum (CSV/npz)…")
        self.save_averaged_btn.setEnabled(False)
        self.save_averaged_btn.clicked.connect(self.saveAveragedRequested.emit)
        self.save_raw_btn = _secondary_btn("Save raw records (N→1 .npz)…")
        self.save_raw_btn.setEnabled(False)
        self.save_raw_btn.clicked.connect(self.saveRawRecordsRequested.emit)
        avsave_col = QVBoxLayout()
        avsave_col.setContentsMargins(0, 0, 0, 0)
        avsave_col.setSpacing(6)
        avsave_col.addWidget(self.save_averaged_btn)
        avsave_col.addWidget(self.save_raw_btn)
        self._grp_avg_save = self._row_widget(avsave_col)

        self.info_label = _hint("No data loaded.")

        # ---- assemble: selectors on top, content groups, then info ----
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._segmented_row(self.algo_xcorr_btn, self.algo_avg_btn))
        layout.addWidget(self._segmented_row(self.src_file_btn, self.src_scope_btn))
        for grp in (self._grp_xcorr_file, self._grp_avg_file,
                    self._grp_scope_common, self._grp_xcorr_scope,
                    self._grp_avg_scope, self._grp_avg_params,
                    self.acquire_btn, self.save_acquired_btn, self._grp_avg_save):
            layout.addWidget(grp)
        layout.addWidget(self.info_label)

        self._update_mode_widgets()

    # ---- small layout helpers ----
    @staticmethod
    def _row_widget(layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    @staticmethod
    def _form_widget(form: QFormLayout) -> QWidget:
        w = QWidget()
        w.setLayout(form)
        return w

    @staticmethod
    def _browse_row(edit: QLineEdit, btn: QPushButton) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(edit, 1)
        row.addWidget(btn)
        return DataSection._row_widget(row)

    @staticmethod
    def _segmented_row(b1: QPushButton, b2: QPushButton) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(b1, 1)
        lay.addWidget(b2, 1)
        box.setStyleSheet(_SEGMENT_QSS)
        return box

    # ---- public state ----
    @property
    def algorithm(self) -> str:
        return ALGO_AVERAGE if self.algo_avg_btn.isChecked() else ALGO_XCORR

    @property
    def source(self) -> str:
        return SRC_SCOPE if self.src_scope_btn.isChecked() else SRC_FILE

    @property
    def mode(self) -> str:
        """Legacy (algorithm, source) → flat mode string for MainWindow routing."""
        if self.algorithm == ALGO_AVERAGE:
            return MODE_AVERAGE if self.source == SRC_SCOPE else MODE_AVERAGE_FILE
        return MODE_ACQUIRE if self.source == SRC_SCOPE else MODE_SINGLE_CSV

    @property
    def file1(self) -> Path | None:
        return self._file1

    @property
    def avg_file(self) -> Path | None:
        return self._avg_file

    @property
    def keep_raw_on(self) -> bool:
        return self.keep_raw_check.isChecked()

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

    @property
    def connection_state(self) -> str:
        return self._conn_state

    def set_connection_status(self, state: str, detail: str = "") -> None:
        """Update the scope connection light. ``state`` is one of
        ``idle / testing / ok / fail``. ``detail`` (the instrument IDN on
        success or the error on failure) goes to the tooltip only — never
        inline — so a long string can't widen the sidebar; the panel just
        shows a coloured dot and a short fixed label."""
        color, label = CONN_STATES.get(state, CONN_STATES["idle"])
        self._conn_state = state if state in CONN_STATES else "idle"
        self.conn_status.setText(
            f'<span style="color:{color}; font-size:15px;">●</span> {label}'
        )
        self.conn_status.setToolTip(detail)

    def mark_acquired(self, acquired: bool) -> None:
        self._has_acquired = acquired
        self.save_acquired_btn.setEnabled(acquired)

    def set_selectors_enabled(self, enabled: bool) -> None:
        """Lock/unlock the algorithm + source toggles (e.g. while monitoring)."""
        for btn in (self.algo_xcorr_btn, self.algo_avg_btn,
                    self.src_file_btn, self.src_scope_btn):
            btn.setEnabled(enabled)

    # ---- multi-acquire average ----
    @property
    def avg_count_n(self) -> int:
        return self.avg_count.value()

    @property
    def avg_skip_n(self) -> int:
        return self.avg_skip.value()

    @property
    def avg_convergence_on(self) -> bool:
        return self.avg_convergence.isChecked()

    def mark_averaged(self, available: bool) -> None:
        self.save_averaged_btn.setEnabled(available)

    def mark_raw_available(self, available: bool) -> None:
        """Enable 'Save raw records' once a keep-raw average has produced them."""
        self._raw_available = available
        self.save_raw_btn.setEnabled(available)

    # ---- selection / visibility ----
    def _on_algorithm_clicked(self, _btn=None) -> None:
        self._update_mode_widgets()
        self.algorithmChanged.emit(self.algorithm)
        self.fileChanged.emit()

    def _on_source_clicked(self, _btn=None) -> None:
        self._update_mode_widgets()
        self.fileChanged.emit()

    def select_algorithm(self, algorithm: str) -> None:
        """Programmatically switch algorithm tab (emits the change signals)."""
        (self.algo_avg_btn if algorithm == ALGO_AVERAGE
         else self.algo_xcorr_btn).setChecked(True)
        self._on_algorithm_clicked()

    def select_source(self, source: str) -> None:
        """Programmatically switch data source (emits fileChanged)."""
        (self.src_scope_btn if source == SRC_SCOPE
         else self.src_file_btn).setChecked(True)
        self._on_source_clicked()

    def _update_mode_widgets(self) -> None:
        is_avg = self.algorithm == ALGO_AVERAGE
        is_scope = self.source == SRC_SCOPE
        self._grp_xcorr_file.setVisible(not is_avg and not is_scope)
        self._grp_avg_file.setVisible(is_avg and not is_scope)
        self._grp_scope_common.setVisible(is_scope)
        self._grp_xcorr_scope.setVisible(not is_avg and is_scope)
        self._grp_avg_scope.setVisible(is_avg and is_scope)
        self._grp_avg_params.setVisible(is_avg)
        self.acquire_btn.setVisible(is_scope)
        self.save_acquired_btn.setVisible(not is_avg and is_scope)
        self._grp_avg_save.setVisible(is_avg)
        self.save_raw_btn.setVisible(is_avg and is_scope)
        self.acquire_btn.setText(
            "⏺  Acquire ×N & average" if is_avg else "⏺  Acquire from scope")

    def _pick_file1(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select data file", "",
            "Data files (*.csv *.npy *.npz);;CSV (*.csv);;"
            "NumPy (*.npy *.npz);;All files (*)",
        )
        if not path:
            return
        self._set_file1(Path(path))
        self.fileChanged.emit()

    def _pick_avg_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select multi-record file", "",
            "Multi-record NumPy (*.npz);;All files (*)",
        )
        if not path:
            return
        self._set_avg_file(Path(path))
        self.fileChanged.emit()

    def _set_file1(self, path: Path) -> None:
        self._file1 = path
        self.file1_edit.setText(str(path))

    def _set_avg_file(self, path: Path) -> None:
        self._avg_file = path
        self.avg_file_edit.setText(str(path))

    def accept_dropped(self, paths: list[Path]) -> bool:
        """Load a data file dropped onto the window → dual-BPD single-file mode
        (first file only; the two-file workflow was removed). Returns True if
        anything was accepted."""
        paths = [p for p in paths if is_data_path(p)]
        if not paths:
            return False
        # Switch to (XCORR, FILE) without re-emitting — we emit once below.
        self.algo_xcorr_btn.setChecked(True)
        self.src_file_btn.setChecked(True)
        self._update_mode_widgets()
        self._set_file1(paths[0])
        self.algorithmChanged.emit(self.algorithm)
        self.fileChanged.emit()
        return True


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
    manualToggled = Signal(bool)   # "Manual FSR" checkbox flipped

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")

        self._fsr_hz: float | None = None   # auto-calibration result
        self._state: _FsrState = _FsrState.IDLE
        self._syncing: bool = False         # guards the length <-> τ link

        self.n_core = NoWheelDoubleSpinBox()
        self.n_core.setRange(1.0, 2.0)
        self.n_core.setDecimals(4)
        self.n_core.setSingleStep(0.001)
        self.n_core.setValue(1.468)
        self.n_core.valueChanged.connect(self._on_n_core_changed)
        _shrinkable(self.n_core)

        self.aom_freq = NoWheelDoubleSpinBox()
        self.aom_freq.setRange(0.0, 5_000.0)
        self.aom_freq.setSuffix(" MHz")
        self.aom_freq.setDecimals(3)
        self.aom_freq.setValue(80.0)
        _shrinkable(self.aom_freq)

        # Manual FSR override. When ticked, the FSR comes from the fiber length
        # / τ below (which take priority over auto-calibration); the two fields
        # are two views of the same delay (τ = n·ΔL/c, FSR = 1/τ) and stay
        # linked. When unticked, the FSR is auto-calibrated from the data.
        self.manual_check = QCheckBox("Manual FSR (set fiber length / τ)")
        self.manual_check.toggled.connect(self._on_manual_toggled)

        self.manual_len = NoWheelDoubleSpinBox()
        self.manual_len.setRange(0.001, 100_000.0)
        self.manual_len.setSuffix(" m")
        self.manual_len.setDecimals(4)
        self.manual_len.setToolTip("MZI path-length difference ΔL.")
        self.manual_len.valueChanged.connect(self._on_len_changed)
        _shrinkable(self.manual_len)

        self.manual_tau = NoWheelDoubleSpinBox()
        self.manual_tau.setRange(0.1, 100_000.0)
        self.manual_tau.setSuffix(" ns")
        self.manual_tau.setDecimals(1)
        self.manual_tau.setValue(100.0)
        self.manual_tau.setToolTip("Delay-line round-trip time τ = 1/FSR.")
        self.manual_tau.valueChanged.connect(self._on_tau_changed)
        _shrinkable(self.manual_tau)

        self._sync_len_from_tau()                 # initialise ΔL from the default τ
        self.manual_len.setEnabled(False)
        self.manual_tau.setEnabled(False)

        self.fsr_display = _metric(_FSR_STATE_TEXTS[_FsrState.IDLE][0])
        self.fsr_display.setWordWrap(True)
        self.delay_len_display = _metric(_FSR_STATE_TEXTS[_FsrState.IDLE][1])
        self.delay_len_display.setWordWrap(True)

        self.calibrate_btn = _secondary_btn("Re-calibrate FSR")
        self.calibrate_btn.setEnabled(False)
        self.calibrate_btn.clicked.connect(self.calibrateRequested.emit)

        # n_core + AOM share one row; τ fallback gets its own row
        editable_row = QHBoxLayout()
        editable_row.setSpacing(6)
        nlbl = QLabel("n")
        nlbl.setMinimumWidth(14)
        editable_row.addWidget(nlbl)
        editable_row.addWidget(self.n_core, 1)
        editable_row.addSpacing(8)
        editable_row.addWidget(QLabel("AOM"))
        editable_row.addWidget(self.aom_freq, 1)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(6)
        manual_row.addWidget(QLabel("ΔL"))
        manual_row.addWidget(self.manual_len, 1)
        manual_row.addSpacing(8)
        manual_row.addWidget(QLabel("τ"))
        manual_row.addWidget(self.manual_tau, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(editable_row)
        layout.addWidget(self.manual_check)
        layout.addLayout(manual_row)
        layout.addWidget(self.fsr_display)
        layout.addWidget(self.delay_len_display)
        layout.addWidget(self.calibrate_btn)

    # ---- state ----
    @property
    def manual_enabled(self) -> bool:
        return self.manual_check.isChecked()

    @property
    def manual_fsr_hz(self) -> float:
        """FSR implied by the manual τ field (= 1/τ; ΔL gives the same value)."""
        return 1.0 / (self.manual_tau.value() * 1e-9)

    @property
    def is_calibrated(self) -> bool:
        # Manual override is always a usable FSR; otherwise auto-cal must succeed.
        return self.manual_enabled or self._state == _FsrState.CALIBRATED

    @property
    def delay_freq_hz(self) -> float | None:
        if self.manual_enabled:
            return self.manual_fsr_hz
        return self._fsr_hz

    @property
    def delay_length_m(self) -> float | None:
        fsr = self.delay_freq_hz
        if fsr is None:
            return None
        return C_LIGHT / (self.n_core.value() * fsr)

    # ---- transitions driven by MainWindow ----
    def set_calibrating(self) -> None:
        self._apply_state(_FsrState.CALIBRATING)

    def apply_calibrated_fsr(self, fsr_hz: float) -> None:
        self._fsr_hz = fsr_hz
        self._apply_state(_FsrState.CALIBRATED)

    def apply_manual_fsr(self) -> None:
        """Refresh the labels to show the manual FSR. ``delay_freq_hz`` already
        returns it while the override is ticked, so no state change is needed."""
        self._refresh_display()
        self.fsrChanged.emit()

    def calibration_failed(self) -> None:
        """Called when auto-cal couldn't find a clean FSR zero."""
        self._fsr_hz = None
        self._apply_state(_FsrState.FAILED)

    def reset_calibration(self) -> None:
        """Called when the loaded data changes — invalidate the old FSR."""
        self._fsr_hz = None
        self._apply_state(_FsrState.IDLE)

    # ---- length <-> τ linking ----
    def _sync_len_from_tau(self) -> None:
        self._syncing = True
        dl = self.manual_tau.value() * 1e-9 * C_LIGHT / self.n_core.value()
        self.manual_len.setValue(dl)
        self._syncing = False

    def _sync_tau_from_len(self) -> None:
        self._syncing = True
        tau_ns = self.manual_len.value() * self.n_core.value() / C_LIGHT * 1e9
        self.manual_tau.setValue(tau_ns)
        self._syncing = False

    def _on_tau_changed(self) -> None:
        if self._syncing:
            return
        self._sync_len_from_tau()
        self._refresh_display()
        if self.manual_enabled:
            self.fsrChanged.emit()

    def _on_len_changed(self) -> None:
        if self._syncing:
            return
        self._sync_tau_from_len()
        self._refresh_display()
        if self.manual_enabled:
            self.fsrChanged.emit()

    def _on_n_core_changed(self) -> None:
        # Hold ΔL (physical) and recompute τ for the new index.
        if not self._syncing:
            self._sync_tau_from_len()
        self._refresh_display()
        if self.manual_enabled:
            self.fsrChanged.emit()

    def _on_manual_toggled(self, checked: bool) -> None:
        self.manual_len.setEnabled(checked)
        self.manual_tau.setEnabled(checked)
        self._refresh_calibrate_btn()
        self._refresh_display()
        self.manualToggled.emit(checked)

    # ---- internal ----
    def _refresh_calibrate_btn(self) -> None:
        # Re-calibrate is only meaningful in auto mode (manual ignores it).
        self.calibrate_btn.setEnabled(
            not self.manual_enabled
            and self._state in (_FsrState.CALIBRATED, _FsrState.FAILED))

    def _apply_state(self, state: _FsrState) -> None:
        """Single source of truth for label text + button-enabled."""
        self._state = state
        if self.manual_enabled or state == _FsrState.CALIBRATED:
            self._refresh_display()
        else:
            text_fsr, text_len = _FSR_STATE_TEXTS[state]
            self.fsr_display.setText(text_fsr)
            self.delay_len_display.setText(text_len)
        self._refresh_calibrate_btn()
        self.fsrChanged.emit()

    def _refresh_display(self) -> None:
        """Refresh the FSR / fiber-length labels from the resolved FSR
        (manual override or auto-calibration result)."""
        fsr = self.delay_freq_hz
        if fsr is None:
            return
        src = "manual" if self.manual_enabled else "auto"
        self.fsr_display.setText(
            f"FSR  {fsr / 1e6:.4f} MHz   (τ = {1e9 / fsr:.2f} ns, {src})"
        )
        self.delay_len_display.setText(f"Fiber  {self.delay_length_m:.4f} m")


# ---------- Segment section ----------

class SegmentSection(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")

        self.bw_edit = QLineEdit("1, 3, 10, 30, 100, 300, 1000, 3000, 10000")
        self.bw_edit.setPlaceholderText("comma-separated, in kHz")
        _shrinkable(self.bw_edit)

        self.ratio = NoWheelSpinBox()
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
        # Error band is opt-in: off by default to keep the plot clean.
        self.c_errband = QCheckBox("Show error band")
        self.c_errband.setChecked(False)

        for w in (self.r_freq, self.r_phase, self.c_bpd1, self.c_bpd2,
                  self.c_cross, self.c_errband):
            w.toggled.connect(self.optionsChanged.emit)

        # Compact: 2 radio buttons on one row, 4 checkboxes on a 2×2 grid
        radio_row = QHBoxLayout()
        radio_row.setSpacing(12)
        radio_row.addWidget(self.r_freq)
        radio_row.addWidget(self.r_phase)
        radio_row.addStretch(1)

        # BPD1/BPD2 share a row (hidden as a unit for single-BPD averaging).
        chk_row1 = QHBoxLayout()
        chk_row1.setContentsMargins(0, 0, 0, 0)
        chk_row1.setSpacing(12)
        chk_row1.addWidget(self.c_bpd1)
        chk_row1.addWidget(self.c_bpd2)
        chk_row1.addStretch(1)
        self._bpd_single_row = QWidget()
        self._bpd_single_row.setLayout(chk_row1)

        chk_row2 = QHBoxLayout()
        chk_row2.setSpacing(12)
        chk_row2.addWidget(self.c_cross)
        chk_row2.addWidget(self.c_errband)
        chk_row2.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(radio_row)
        layout.addWidget(self._bpd_single_row)
        layout.addLayout(chk_row2)

    def set_single_bpd(self, single: bool) -> None:
        """Single-BPD averaging shows one curve, so hide the per-channel
        BPD1/BPD2 and cross-correlation toggles (the freq/phase radios and the
        error-band toggle still apply)."""
        self._bpd_single_row.setVisible(not single)
        self.c_cross.setVisible(not single)


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
    saveAveragedRequested = Signal()
    saveRawRecordsRequested = Signal()
    testConnectionRequested = Signal()
    monitorStartRequested = Signal()
    monitorStopRequested = Signal()
    monitorClearRequested = Signal()
    saveFrameRawRequested = Signal()
    saveFrameSpectrumRequested = Signal()

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
        # Process gating composes _busy (a run is already in flight), the live
        # FSR state (optical.is_calibrated), the active algorithm/source, and
        # _monitoring — all evaluated in `_refresh_process_btn`.
        self._busy: bool = False
        self.process_btn.setEnabled(False)

        self.export_btn = _secondary_btn("Export spectra…")
        self.export_btn.setEnabled(False)
        self._can_export: bool = False

        self.monitor_btn = _secondary_btn("▶  Monitor (live)")
        self.monitor_btn.setEnabled(False)
        self._monitoring: bool = False

        # Clear ends the current (paused) monitoring session so the next start
        # is fresh. Enabled only when stopped with a resumable session.
        self.monitor_clear_btn = _secondary_btn("Clear")
        self.monitor_clear_btn.setEnabled(False)
        self.monitor_clear_btn.setToolTip(
            "Clear the paused monitoring session. The next Monitor start picks a "
            "new folder and restarts from zero; without Clear, Monitor resumes."
        )
        self.monitor_clear_btn.clicked.connect(self.monitorClearRequested.emit)

        # When checked, starting Monitor first asks for a folder and then
        # auto-saves every cycle's spectrum (.npz) plus the Lorentz/β trend.
        self.save_monitor_check = QCheckBox("Save")
        self.save_monitor_check.setToolTip(
            "Save each monitoring cycle's noise spectrum (.npz) and the "
            "Lorentz/β trend to a chosen folder."
        )

        # Rollback save: the last up-to-3 monitoring cycles are kept in memory
        # so the user can grab a good frame's raw trace and/or spectrum.
        self.rollback_combo = NoWheelComboBox()
        self.save_frame_raw_btn = _secondary_btn("Save raw…")
        self.save_frame_raw_btn.clicked.connect(self.saveFrameRawRequested.emit)
        self.save_frame_spec_btn = _secondary_btn("Save spectrum…")
        self.save_frame_spec_btn.clicked.connect(self.saveFrameSpectrumRequested.emit)
        self.rollback_group = self._build_rollback_group()
        self.set_rollback_available(0)

        # forward signals
        self.data.fileChanged.connect(self.fileChanged.emit)
        self.data.acquireRequested.connect(self.acquireRequested.emit)
        self.data.saveAcquiredRequested.connect(self.saveAcquiredRequested.emit)
        self.data.saveAveragedRequested.connect(self.saveAveragedRequested.emit)
        self.data.saveRawRecordsRequested.connect(self.saveRawRecordsRequested.emit)
        self.data.testConnectionRequested.connect(self.testConnectionRequested.emit)
        self.display.optionsChanged.connect(self.optionsChanged.emit)
        self.analysis.optionsChanged.connect(self.optionsChanged.emit)
        self.optical.calibrateRequested.connect(self.calibrateRequested.emit)
        # Any FSR change (manual override / length / τ edits) re-evaluates the
        # Process + Monitor buttons so they track the live calibration state.
        self.optical.fsrChanged.connect(self._refresh_process_btn)
        self.optical.fsrChanged.connect(self._refresh_monitor_btn)
        self.process_btn.clicked.connect(self.processRequested.emit)
        self.export_btn.clicked.connect(self.exportRequested.emit)
        self.monitor_btn.clicked.connect(self._on_monitor_clicked)
        # Mode changes (emitted via data.fileChanged) re-evaluate the action
        # buttons; switching algorithm tab also toggles section visibility.
        self.data.fileChanged.connect(self._refresh_monitor_btn)
        self.data.fileChanged.connect(self._refresh_process_btn)
        self.data.algorithmChanged.connect(self._on_algorithm_changed)

        # Sections live in a vertical-scroll-only container. We never
        # want the sidebar to scroll horizontally — when the user shrinks
        # the window, content should clip / wrap, not slide sideways —
        # and we want vertical scroll only as a fallback when the window
        # is too short to show every section at its natural size.
        sections_widget = QWidget()
        sections_layout = QVBoxLayout(sections_widget)
        sections_layout.setContentsMargins(16, 16, 16, 8)
        sections_layout.setSpacing(8)
        # Each section is a (title + card) box so it can be hidden as a unit.
        # The averaging algorithm has no BW segments, so its box is toggled off.
        self._section_boxes: dict[str, QWidget] = {}
        for title, widget in (
            ("Data", self.data),
            ("Optical path", self.optical),
            ("Segments", self.segments),
            ("Display", self.display),
            ("Analysis", self.analysis),
        ):
            box = QWidget()
            box_col = QVBoxLayout(box)
            box_col.setContentsMargins(0, 0, 0, 0)
            box_col.setSpacing(8)
            box_col.addWidget(_section_title(title))
            box_col.addWidget(widget)
            self._section_boxes[title] = box
            sections_layout.addWidget(box)
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
        # Monitoring (live) is a dual-BPD-only feature; the whole row is hidden
        # under the averaging algorithm. Wrapped so it toggles as one unit.
        monitor_row = QHBoxLayout()
        monitor_row.setSpacing(8)
        monitor_row.setContentsMargins(0, 0, 0, 0)
        monitor_row.addWidget(self.monitor_btn, 1)
        monitor_row.addWidget(self.save_monitor_check)
        monitor_row.addWidget(self.monitor_clear_btn)
        self._monitor_row_widget = QWidget()
        self._monitor_row_widget.setLayout(monitor_row)
        btn_layout.addWidget(self._monitor_row_widget)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.rollback_group)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll, 1)
        layout.addWidget(button_row)

        # Apply the initial algorithm's section visibility (default = XCORR).
        self._on_algorithm_changed(self.data.algorithm)

    @property
    def save_monitor_enabled(self) -> bool:
        return self.save_monitor_check.isChecked()

    def _build_rollback_group(self) -> QWidget:
        """A small 'save a recent frame' panel: pick current/−1/−2, then save
        its raw trace and/or spectrum. Hidden until a cycle has been seen."""
        group = QWidget()
        col = QVBoxLayout(group)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(6)
        pick = QHBoxLayout()
        pick.setSpacing(6)
        pick.addWidget(QLabel("Recent frame"))
        pick.addWidget(self.rollback_combo, 1)
        save = QHBoxLayout()
        save.setSpacing(6)
        save.addWidget(self.save_frame_raw_btn, 1)
        save.addWidget(self.save_frame_spec_btn, 1)
        col.addLayout(pick)
        col.addLayout(save)
        return group

    @property
    def rollback_offset(self) -> int:
        """How far back the selected frame is: 0 = current, 1 = −1, 2 = −2."""
        data = self.rollback_combo.currentData()
        return int(data) if data is not None else 0

    def set_rollback_available(self, n_frames: int) -> None:
        """Populate the frame picker with the ``n_frames`` (0–3) cycles kept in
        memory and enable saving when at least one exists."""
        self.rollback_group.setVisible(n_frames > 0)
        prev = self.rollback_combo.currentData()
        labels = ["current", "−1 (previous)", "−2 (two ago)"]
        blocked = self.rollback_combo.blockSignals(True)
        self.rollback_combo.clear()
        for offset in range(min(n_frames, 3)):
            self.rollback_combo.addItem(labels[offset], userData=offset)
        # Keep the previous selection if still valid.
        if prev is not None:
            idx = self.rollback_combo.findData(prev)
            if idx >= 0:
                self.rollback_combo.setCurrentIndex(idx)
        self.rollback_combo.blockSignals(blocked)
        has = n_frames > 0
        self.save_frame_raw_btn.setEnabled(has)
        self.save_frame_spec_btn.setEnabled(has)

    def set_export_enabled(self, enabled: bool) -> None:
        self._can_export = enabled
        self.export_btn.setEnabled(enabled and not self._monitoring)

    def set_processing(self, busy: bool) -> None:
        self._busy = busy
        self.process_btn.setText("Processing…" if busy else "▶  Process")
        self._refresh_process_btn()
        self._refresh_monitor_btn()

    def set_calibrated(self, calibrated: bool) -> None:
        """Called by MainWindow after auto-cal succeeds/fails. The button gating
        reads the live ``optical.is_calibrated`` state, so this just re-evaluates
        the Process / Monitor buttons (the ``calibrated`` arg is advisory)."""
        self._refresh_process_btn()
        self._refresh_monitor_btn()

    def _refresh_process_btn(self) -> None:
        # Process means "run the active algorithm". Gating differs by algorithm:
        #  • XCORR: needs a usable FSR (manual or auto-cal).
        #  • AVG+FILE: needs a multi-record file (the worker auto-cals the FSR).
        #  • AVG+SCOPE: the in-panel 'Acquire ×N & average' button is the action,
        #    so Process re-runs it too — enabled whenever not busy.
        if self.data.algorithm == ALGO_AVERAGE:
            if self.data.source == SRC_FILE:
                ready = self.data.avg_file is not None
            else:
                ready = True
        else:
            ready = self.optical.is_calibrated
        self.process_btn.setEnabled(
            ready and not self._busy and not self._monitoring
        )

    def _on_algorithm_changed(self, algorithm: str) -> None:
        """Show only the sections the active algorithm uses. The averaging
        algorithm is single-BPD with no BW segments and no live monitoring, so
        its Segments box, the per-channel Display options, and the whole Monitor
        row are hidden."""
        is_avg = algorithm == ALGO_AVERAGE
        self._section_boxes["Segments"].setVisible(not is_avg)
        self.display.set_single_bpd(is_avg)
        self._monitor_row_widget.setVisible(not is_avg)
        # Rollback (a monitoring feature) is hidden under averaging; back on
        # XCORR, restore it only if recent frames actually exist.
        self.rollback_group.setVisible(
            not is_avg and self.rollback_combo.count() > 0)
        self._refresh_process_btn()
        self._refresh_monitor_btn()

    def _on_monitor_clicked(self) -> None:
        if self._monitoring:
            self.monitorStopRequested.emit()
        else:
            self.monitorStartRequested.emit()

    def _refresh_monitor_btn(self) -> None:
        can_start = (self.optical.is_calibrated and not self._busy
                     and self.data.mode == MODE_ACQUIRE)
        self.monitor_btn.setEnabled(self._monitoring or can_start)

    def set_monitoring(self, monitoring: bool) -> None:
        """Lock conflicting controls while the live loop runs."""
        self._monitoring = monitoring
        self.monitor_btn.setText(
            "■  Stop monitoring" if monitoring else "▶  Monitor (live)"
        )
        self.data.set_selectors_enabled(not monitoring)
        self.data.acquire_btn.setEnabled(not monitoring)
        self.save_monitor_check.setEnabled(not monitoring)
        self.export_btn.setEnabled(self._can_export and not monitoring)
        if monitoring:
            self.monitor_clear_btn.setEnabled(False)  # can't clear mid-run
        self._refresh_process_btn()
        self._refresh_monitor_btn()

    def set_monitor_resumable(self, resumable: bool) -> None:
        """Reflect whether a paused session can be resumed: enable Clear and
        relabel Monitor as 'Resume' (only meaningful while stopped)."""
        self.monitor_clear_btn.setEnabled(resumable and not self._monitoring)
        if not self._monitoring:
            self.monitor_btn.setText(
                "▶  Resume monitoring" if resumable else "▶  Monitor (live)")

    def set_acquiring(self, busy: bool) -> None:
        self.data.acquire_btn.setEnabled(not busy)
        self.data.acquire_btn.setText(
            "Acquiring…" if busy else "⏺  Acquire from scope"
        )

    def set_averaging(self, busy: bool) -> None:
        # Mark the panel busy so the Process button can't launch a second
        # averaging run (concurrent scope acquisition) while one is in flight.
        self._busy = busy
        self.data.acquire_btn.setEnabled(not busy)
        self.data.acquire_btn.setText(
            "Averaging…" if busy else "⏺  Acquire ×N & average"
        )
        self._refresh_process_btn()
        self._refresh_monitor_btn()

    def snapshot(self) -> SettingsSnapshot:
        rstart, rstop = self.segments.parsed_range()
        lz_min = self.analysis.parse_float(self.analysis.lorentz_fmin) or 1e6
        lz_max = self.analysis.parse_float(self.analysis.lorentz_fmax) or 1e7
        return SettingsSnapshot(
            algorithm=self.data.algorithm,
            source=self.data.source,
            mode=self.data.mode,
            file1=self.data.file1,
            avg_file=self.data.avg_file,
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
