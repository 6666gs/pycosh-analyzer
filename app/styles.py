"""Apple-style light QSS stylesheet for the dual-BPD analyzer."""
from __future__ import annotations

# Apple system palette
COLOR_BG = "#FFFFFF"
COLOR_SIDEBAR = "#F5F5F7"
COLOR_BORDER = "#E5E5EA"
COLOR_INPUT_BORDER = "#D2D2D7"
COLOR_TEXT = "#1D1D1F"
COLOR_TEXT_SECONDARY = "#86868B"
COLOR_ACCENT = "#007AFF"
COLOR_ACCENT_HOVER = "#0066D6"
COLOR_ACCENT_PRESSED = "#0055B3"
COLOR_DISABLED = "#C7C7CC"
COLOR_DANGER = "#FF3B30"

APP_QSS = f"""
* {{
    font-family: -apple-system, "SF Pro Display", "SF Pro Text",
                 "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    font-size: 13px;
    color: {COLOR_TEXT};
}}

QMainWindow, QWidget {{
    background: {COLOR_BG};
}}

#sidebar {{
    background: {COLOR_SIDEBAR};
    border-right: 1px solid {COLOR_BORDER};
}}

#sectionTitle {{
    color: {COLOR_TEXT_SECONDARY};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    padding: 2px 0;
}}

#sectionCard {{
    background: {COLOR_BG};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 12px;
}}

QPushButton {{
    background: {COLOR_ACCENT};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-weight: 500;
}}
QPushButton:hover  {{ background: {COLOR_ACCENT_HOVER}; }}
QPushButton:pressed {{ background: {COLOR_ACCENT_PRESSED}; }}
QPushButton:disabled {{ background: {COLOR_DISABLED}; color: white; }}

QPushButton[variant="secondary"] {{
    background: {COLOR_BG};
    color: {COLOR_ACCENT};
    border: 1px solid {COLOR_INPUT_BORDER};
}}
QPushButton[variant="secondary"]:hover  {{ background: {COLOR_SIDEBAR}; }}
QPushButton[variant="secondary"]:pressed {{ background: {COLOR_BORDER}; }}

QPushButton[variant="ghost"] {{
    background: transparent;
    color: {COLOR_ACCENT};
    border: none;
    padding: 4px 8px;
    font-weight: 500;
}}
QPushButton[variant="ghost"]:hover {{ color: {COLOR_ACCENT_HOVER}; }}

QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    background: {COLOR_BG};
    border: 1px solid {COLOR_INPUT_BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {COLOR_ACCENT};
    selection-color: white;
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1.5px solid {COLOR_ACCENT};
    padding: 5px 9px;
}}
QLineEdit:read-only {{
    background: {COLOR_SIDEBAR};
    color: {COLOR_TEXT_SECONDARY};
}}

QLabel[role="hint"] {{
    color: {COLOR_TEXT_SECONDARY};
    font-size: 11px;
}}

QLabel[role="metric"] {{
    color: {COLOR_TEXT};
    font-size: 12px;
    font-weight: 500;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{
    border: 1.5px solid {COLOR_INPUT_BORDER};
    border-radius: 4px;
    background: {COLOR_BG};
}}
QCheckBox::indicator:checked {{
    border: 1.5px solid {COLOR_ACCENT};
    border-radius: 4px;
    background: {COLOR_ACCENT};
}}

QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 8px; }}
QRadioButton::indicator:unchecked {{
    border: 1.5px solid {COLOR_INPUT_BORDER}; background: {COLOR_BG};
}}
QRadioButton::indicator:checked {{
    border: 1.5px solid {COLOR_ACCENT}; background: {COLOR_BG};
}}
QRadioButton::indicator:checked:hover {{ border-color: {COLOR_ACCENT_HOVER}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {COLOR_DISABLED}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {COLOR_TEXT_SECONDARY}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

QStatusBar {{
    background: {COLOR_SIDEBAR};
    border-top: 1px solid {COLOR_BORDER};
    color: {COLOR_TEXT_SECONDARY};
    font-size: 12px;
}}

QToolTip {{
    background: {COLOR_TEXT};
    color: white;
    border: none;
    padding: 6px 8px;
    border-radius: 4px;
}}
"""
