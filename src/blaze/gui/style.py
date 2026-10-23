"""Colors and the app-wide stylesheet."""

BG = "#141110"
SURFACE = "#1d1917"
SURFACE_2 = "#26211e"
BORDER = "#342c27"
TEXT = "#f3ece7"
MUTED = "#a3968d"
ACCENT = "#ff8a1f"
ACCENT_DARK = "#e5721a"
DANGER = "#ff5a4f"

STATE_COLORS = {
    "idle": "#ff8a1f",
    "listening": "#3dd6ff",
    "thinking": "#b37bff",
    "speaking": "#ffb347",
    "muted": "#6b625c",
    "starting": "#8a817b",
    "offline": "#4a4440",
}

STATE_LABELS = {
    "idle": "Say “Hey Blaze” or press SUPER+M",
    "listening": "Listening…",
    "thinking": "Thinking…",
    "speaking": "Talking",
    "muted": "Microphone muted",
    "starting": "Waking up…",
    "offline": "Blaze isn't running",
}

STYLESHEET = f"""
* {{ font-size: 14px; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QListWidget#nav {{
    background: {SURFACE}; border: none; border-right: 1px solid {BORDER};
    padding: 12px 8px; outline: none;
}}
QListWidget#nav::item {{ padding: 10px 14px; border-radius: 8px; margin: 2px 0; color: {MUTED}; }}
QListWidget#nav::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#nav::item:hover:!selected {{ background: {SURFACE_2}; }}

QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#subtitle, QLabel.hint {{ color: {MUTED}; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#stateLabel {{ font-size: 18px; font-weight: 600; }}

QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
    margin-top: 28px; padding: 16px 14px 12px 14px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 4px; top: 4px; color: {ACCENT}; }}

QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget#list {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 8px; selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; }}
QTextBrowser#chat {{ background: {SURFACE}; border-radius: 12px; padding: 10px; }}
QListWidget#list::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}
QListWidget#list::item:selected {{ background: {SURFACE}; color: {TEXT}; }}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 16px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: #1a0f08; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#danger {{ color: {DANGER}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QFrame#banner {{ background: #3a2a12; border: 1px solid {ACCENT}; border-radius: 10px; }}
QStatusBar {{ background: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; padding-left: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
