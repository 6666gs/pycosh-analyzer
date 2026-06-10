"""Offscreen GUI tests for the accidental-scroll guard and drag-and-drop.

Qt runs headless via conftest.py (QT_QPA_PLATFORM=offscreen).
"""
from pathlib import Path

import numpy as np

from app.data_io import DualBpdData, save_dual_bpd_npz
from app.settings_panel import (
    ALGO_XCORR,
    MODE_SINGLE_CSV,
    SRC_FILE,
    DataSection,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
)


class _FakeWheel:
    """Stand-in for a QWheelEvent — we only need .ignore()."""

    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


def _npz(tmp_path: Path, name: str) -> Path:
    t = np.arange(128) / 1e6
    data = DualBpdData(t=t, v1=np.sin(t), v2=None, sample_rate=1e6, source_files=())
    out = tmp_path / name
    save_dual_bpd_npz(out, data)
    return out


def test_nowheel_spinbox_ignores_wheel_when_unfocused(qtbot):
    box = NoWheelSpinBox()
    qtbot.addWidget(box)
    box.setRange(0, 10)
    box.setValue(5)

    ev = _FakeWheel()
    box.wheelEvent(ev)  # widget has no focus in an offscreen test

    assert ev.ignored is True
    assert box.value() == 5  # value untouched


def test_nowheel_widgets_use_click_focus(qtbot):
    from PySide6.QtCore import Qt

    for widget in (NoWheelSpinBox(), NoWheelDoubleSpinBox(), NoWheelComboBox()):
        qtbot.addWidget(widget)
        assert widget.focusPolicy() == Qt.StrongFocus


def test_panel_widgets_are_nowheel(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    assert isinstance(win.settings.optical.n_core, NoWheelDoubleSpinBox)
    assert isinstance(win.settings.segments.ratio, NoWheelSpinBox)
    assert isinstance(win.settings.data.scope_ch1, NoWheelComboBox)


def test_drop_single_file_sets_xcorr_file_mode_and_emits(qtbot, tmp_path):
    sec = DataSection()
    qtbot.addWidget(sec)
    npz = _npz(tmp_path, "rec.npz")

    with qtbot.waitSignal(sec.fileChanged, timeout=1000):
        accepted = sec.accept_dropped([npz])

    assert accepted is True
    assert sec.algorithm == ALGO_XCORR and sec.source == SRC_FILE
    assert sec.mode == MODE_SINGLE_CSV
    assert sec.file1 == npz


def test_drop_two_files_takes_first_only(qtbot, tmp_path):
    # The two-file workflow was removed: dropping several files loads only the
    # first into the single-file dual-BPD source.
    sec = DataSection()
    qtbot.addWidget(sec)
    p1 = _npz(tmp_path, "a.npz")
    p2 = _npz(tmp_path, "b.npz")

    accepted = sec.accept_dropped([p1, p2])

    assert accepted is True
    assert sec.mode == MODE_SINGLE_CSV
    assert sec.file1 == p1


def test_drop_unsupported_file_is_rejected(qtbot, tmp_path):
    sec = DataSection()
    qtbot.addWidget(sec)
    junk = tmp_path / "notes.txt"
    junk.write_text("nope")

    assert sec.accept_dropped([junk]) is False


def test_connection_status_light_states(qtbot):
    sec = DataSection()
    qtbot.addWidget(sec)

    assert sec.connection_state == "idle"

    sec.set_connection_status("ok", "FAKE,SDS7404")
    assert sec.connection_state == "ok"
    assert "34C759" in sec.conn_status.text()              # green dot
    # IDN must NOT appear inline (would widen the sidebar); it's tooltip-only.
    assert "FAKE,SDS7404" not in sec.conn_status.text()
    assert "FAKE,SDS7404" in sec.conn_status.toolTip()     # available on hover

    sec.set_connection_status("fail", "a very long error message string")
    assert sec.connection_state == "fail"
    assert "FF3B30" in sec.conn_status.text()              # red dot
    assert "very long error" not in sec.conn_status.text()  # detail stays in tooltip
    assert "very long error" in sec.conn_status.toolTip()


def test_test_connection_button_emits_request(qtbot):
    sec = DataSection()
    qtbot.addWidget(sec)
    with qtbot.waitSignal(sec.testConnectionRequested, timeout=1000):
        sec.test_conn_btn.click()


def test_test_connection_without_host_shows_red(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.settings.data.scope_ip.setText("")  # no IP

    win._test_connection()

    assert win.settings.data.connection_state == "fail"
    assert win._conn_worker is None  # no worker spawned


def test_connection_result_handlers_update_light(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    win._on_conn_ok("FAKE,SDS7404,1,2")
    assert win.settings.data.connection_state == "ok"

    win._on_conn_err("TimeoutError: no route to host")
    assert win.settings.data.connection_state == "fail"


def test_main_window_dropevent_forwards_local_paths(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    captured: dict = {}
    monkeypatch.setattr(
        win.settings.data, "accept_dropped",
        lambda paths: captured.setdefault("paths", paths) is None,
    )

    class _Url:
        def __init__(self, p):
            self._p = p

        def isLocalFile(self):
            return True

        def toLocalFile(self):
            return self._p

    class _Mime:
        def urls(self):
            return [_Url("/tmp/a.npz"), _Url("/tmp/b.csv")]

    class _Ev:
        def mimeData(self):
            return _Mime()

        def acceptProposedAction(self):
            captured["accepted"] = True

    win.dropEvent(_Ev())

    assert captured["paths"] == [Path("/tmp/a.npz"), Path("/tmp/b.csv")]


# ---- save-acquired format selection ----

def test_resolve_save_path_honours_explicit_suffix():
    from app.main_window import _resolve_save_path

    assert _resolve_save_path("/d/rec.npy", "CSV (*.csv)") == Path("/d/rec.npy")
    assert _resolve_save_path("/d/rec.csv", "NumPy array (*.npy)") == Path("/d/rec.csv")


def test_resolve_save_path_appends_from_filter():
    from app.main_window import _resolve_save_path

    assert _resolve_save_path("/d/rec", "NumPy array (*.npy)") == Path("/d/rec.npy")
    assert _resolve_save_path("/d/rec", "CSV (*.csv)") == Path("/d/rec.csv")


def _win_with_data(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    t = np.arange(64) / 1e6
    win._data = DualBpdData(t=t, v1=np.sin(t), v2=np.cos(t),
                            sample_rate=1e6, source_files=())
    return win


def test_save_acquired_as_npy(qtbot, tmp_path, monkeypatch):
    from app.data_io import load_record

    win = _win_with_data(qtbot)
    out = tmp_path / "acq.npy"

    class _Dialog:
        @staticmethod
        def getSaveFileName(*args, **kwargs):
            return (str(out), "NumPy array (*.npy)")

    monkeypatch.setattr("app.main_window.QFileDialog", _Dialog)
    win._save_acquired()

    assert out.exists()
    loaded = load_record(out)
    np.testing.assert_allclose(loaded.v1, win._data.v1)


def test_save_acquired_as_csv(qtbot, tmp_path, monkeypatch):
    from app.data_io import load_record

    win = _win_with_data(qtbot)
    out = tmp_path / "acq.csv"

    class _Dialog:
        @staticmethod
        def getSaveFileName(*args, **kwargs):
            return (str(out), "CSV (*.csv)")

    monkeypatch.setattr("app.main_window.QFileDialog", _Dialog)
    win._save_acquired()

    assert out.exists()
    loaded = load_record(out)
    np.testing.assert_allclose(loaded.v1, win._data.v1)


def test_save_acquired_appends_extension_when_missing(qtbot, tmp_path, monkeypatch):
    win = _win_with_data(qtbot)
    typed = tmp_path / "acq"  # no extension typed

    class _Dialog:
        @staticmethod
        def getSaveFileName(*args, **kwargs):
            return (str(typed), "NumPy array (*.npy)")

    monkeypatch.setattr("app.main_window.QFileDialog", _Dialog)
    win._save_acquired()

    assert (tmp_path / "acq.npy").exists()  # .npy appended from the filter
