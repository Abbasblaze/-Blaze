"""Colors and the app-wide stylesheet."""

from ..paths import ASSETS_DIR

BG = "#121010"
SURFACE = "#1b1816"
SURFACE_2 = "#24201d"
SURFACE_3 = "#2e2925"
BORDER = "#332c27"
TEXT = "#f3ece7"
MUTED = "#a3968d"
FAINT = "#6f655e"
ACCENT = "#ff8a1f"
ACCENT_DARK = "#e5721a"
ACCENT_SOFT = "#3a2614"
DANGER = "#ff5a4f"
DANGER_SOFT = "#3a1a17"
OK = "#4cd07d"

STATE_COLORS = {
    "idle": "#ff8a1f",
    "listening": "#3dd6ff",
    "thinking": "#b37bff",
    "speaking": "#ffb347",
    "muted": "#6b625c",
    "starting": "#8a817b",
    "offline": "#4a4440",
    "asleep": "#6a5a8a",
}

STATE_LABELS = {
    "idle": "Say “Hey Blaze” or press SUPER+M",
    "listening": "Listening…",
    "thinking": "Thinking…",
    "speaking": "Talking",
    "muted": "Microphone muted",
    "starting": "Waking up…",
    "offline": "Blaze isn't running",
    "asleep": "Asleep to save RAM · wakes when you're back",
}

STATE_SHORT = {
    "idle": "Ready", "listening": "Listening", "thinking": "Thinking", "speaking": "Talking",
    "muted": "Muted", "starting": "Waking up", "offline": "Offline", "asleep": "Asleep",
}

CHEVRON = (ASSETS_DIR / "chevron-down.svg").as_posix()
TOGGLE_OFF = (ASSETS_DIR / "toggle-off.svg").as_posix()
TOGGLE_ON = (ASSETS_DIR / "toggle-on.svg").as_posix()

STYLESHEET = f"""
* {{ font-size: 14px; color: {TEXT}; }}
QMainWindow, QWidget#root, QWidget#page {{ background: {BG}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

/* ---- sidebar ---- */
QWidget#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#brand {{ font-size: 17px; font-weight: 700; }}
QLabel#brandSub {{ color: {MUTED}; font-size: 12px; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 9px 12px; border-radius: 9px; margin: 2px 0; color: {MUTED}; }}
QListWidget#nav::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QListWidget#nav::item:hover:!selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QFrame#statusCard {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#statusText {{ font-weight: 600; }}
QLabel#statusSub {{ color: {MUTED}; font-size: 12px; }}

/* ---- text ---- */
QLabel#title {{ font-size: 24px; font-weight: 700; }}
QLabel#subtitle {{ color: {MUTED}; }}
QLabel#hint {{ color: {FAINT}; font-size: 12px; }}
QLabel#stateLabel {{ font-size: 20px; font-weight: 650; }}
QLabel#section {{ font-size: 16px; font-weight: 650; }}
QLabel#heard {{ color: {MUTED}; font-size: 12px; font-style: italic; }}
QLabel#chip {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 11px;
    padding: 3px 10px; color: {MUTED}; font-size: 12px;
}}

/* ---- cards ---- */
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px;
    margin-top: 30px; padding: 18px 16px 14px 16px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 6px; top: 6px; color: {TEXT}; font-size: 15px; }}
QGroupBox QLabel {{ font-weight: 400; }}

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 7px 10px; selection-background-color: {ACCENT_DARK};
}}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #4a4039; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled {{ color: {FAINT}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({CHEVRON}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
    selection-background-color: {SURFACE_3}; outline: none;
}}

/* composer: one rounded pill holding the text box and send button */
QFrame#composer {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#composer QLineEdit {{ background: transparent; border: none; padding: 8px 4px; font-size: 15px; }}

/* chat */
QScrollArea#chat {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QWidget#chatBody {{ background: transparent; }}
QLabel#bubbleUser {{
    background: {ACCENT_SOFT}; border: 1px solid #5a3a1c; border-radius: 14px; padding: 9px 13px;
}}
QLabel#bubbleBlaze {{
    background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 14px; padding: 9px 13px;
}}
QLabel#stamp {{ color: {FAINT}; font-size: 11px; }}
QLabel#day {{ color: {FAINT}; font-size: 11px; font-weight: 600; }}

/* lists */
QListWidget#list {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; padding: 6px; outline: none;
}}
QListWidget#list::item {{ padding: 10px 12px; border-radius: 9px; margin: 1px 0; }}
QListWidget#list::item:hover {{ background: {SURFACE_2}; }}
QListWidget#list::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}

/* ---- buttons ---- */
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 16px;
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #4a4039; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: #1a0f08; font-weight: 650; }}
QPushButton#primary:hover {{ background: #ff9a3d; border-color: #ff9a3d; }}
QPushButton#primary:pressed {{ background: {ACCENT_DARK}; }}
QPushButton#primary:disabled {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT}; color: {FAINT}; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#danger:hover {{ background: {DANGER_SOFT}; border-color: #6a2a24; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton#ghost:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#toggle:checked {{ background: {DANGER_SOFT}; border-color: #6a2a24; color: {DANGER}; }}
QPushButton#round {{ background: {ACCENT}; border: none; border-radius: 18px; padding: 0; }}
QPushButton#round:hover {{ background: #ff9a3d; }}
QPushButton#round:disabled {{ background: {SURFACE_3}; }}
QPushButton#big {{
    background: {ACCENT}; border: none; color: #1a0f08; font-weight: 700; font-size: 15px;
    border-radius: 19px; padding: 0 26px; min-height: 40px;
}}
QPushButton#big:hover {{ background: #ff9a3d; }}
QPushButton#big:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton#pill {{ border-radius: 19px; padding: 0 18px; min-height: 38px; }}
QPushButton#pill[active="true"] {{ background: {DANGER_SOFT}; border-color: #6a2a24; color: {DANGER}; }}

QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 36px; height: 20px; image: url({TOGGLE_OFF}); }}
QCheckBox::indicator:checked {{ image: url({TOGGLE_ON}); }}
QCheckBox:disabled {{ color: {FAINT}; }}

QFrame#banner {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; border-radius: 12px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; border-top: 1px solid {BORDER}; padding-left: 10px; font-size: 12px; }}
QStatusBar::item {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a4039; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
"""
