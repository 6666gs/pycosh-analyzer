"""Entry point for the dual-BPD noise analyzer."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QStyleFactory

from app.main_window import MainWindow
from app.styles import APP_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Dual-BPD Noise Analyzer")
    app.setOrganizationName("LaserPhaseNoise")
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    # Fusion baseline so our QSS fully overrides native widget styling.
    # Without this, macOS/Windows native styles win for default QPushButton
    # and Apple-style buttons render incorrectly.
    app.setStyle(QStyleFactory.create("Fusion"))

    # Prefer system UI font; falls back automatically.
    base_font = QFont()
    base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(base_font)
    app.setStyleSheet(APP_QSS)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
