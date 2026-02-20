"""全局配置常量"""

import platform
from pathlib import Path

import pyaudio

# ========== 版本 ==========
APP_VERSION = "1.0.1"

# ========== 窗口设置 ==========
WINDOW_TITLE = f"局域网无线麦克风 v{APP_VERSION}"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 950
WINDOW_MIN_WIDTH = 1000
WINDOW_MIN_HEIGHT = 700

# ========== 功能开关 ==========
ENABLE_TRAY = False
ENABLE_LOG_FILE = False
ENABLE_DEBUG = True

# ========== 文件设置 ==========
CONFIG_FILE_NAME = "mobile_mic_config.json"
LOG_FILE_NAME = "mobile_mic.log"
CERT_FILE_NAME = "server.crt"
KEY_FILE_NAME = "server.key"
RECORD_DIR = "records"


def get_default_record_dir():
    """获取默认录制目录（用户目录下）"""
    home = Path.home()
    if platform.system() == "Linux":
        return home / "Any-Wireless-Mic"
    return home / "Documents" / "Any-Wireless-Mic"

# ========== 音频设置 ==========
CHUNK = 1024
FORMAT = pyaudio.paInt16
FORMAT_FLOAT32 = pyaudio.paFloat32
CHANNELS = 1
RATE = 44100

# ========== 网络设置 ==========
DEFAULT_PORT = 5001
MIN_PORT = 1024
MAX_PORT = 65535

# ========== UI 设置 ==========
LOG_DISPLAY_HEIGHT = 6
ENABLE_REALTIME_PLAYBACK = True

# ========== 深色主题色值 ==========
DARK_THEME = {
    "bg": "#1e1e2e",
    "bg_secondary": "#2b2b3d",
    "bg_panel": "#252537",
    "text": "#cdd6f4",
    "text_secondary": "#a6adc8",
    "accent": "#89b4fa",
    "success": "#a6e3a1",
    "warning": "#f9e2af",
    "danger": "#f38ba8",
    "border": "#45475a",
    "input_bg": "#313244",
    "green": "#28a745",
    "yellow": "#ffc107",
    "red": "#dc3545",
}

# ========== QSS 深色主题样式表 ==========
DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: %(bg)s;
    color: %(text)s;
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid %(border)s;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 16px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QPushButton {
    background-color: %(bg_secondary)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
    padding: 6px 16px;
    min-height: 24px;
}
QPushButton:hover {
    background-color: %(border)s;
}
QPushButton:pressed {
    background-color: %(accent)s;
    color: #000;
}
QPushButton:disabled {
    background-color: %(bg_panel)s;
    color: %(border)s;
}
QComboBox {
    background-color: %(input_bg)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 24px;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: %(input_bg)s;
    border: 1px solid %(border)s;
    selection-background-color: %(accent)s;
    selection-color: #000;
}
QLineEdit {
    background-color: %(input_bg)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 24px;
}
QTextEdit {
    background-color: %(input_bg)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
    font-family: "Consolas", "Menlo", monospace;
    font-size: 12px;
}
QTreeWidget, QTableWidget {
    background-color: %(input_bg)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
    alternate-background-color: %(bg_secondary)s;
}
QTreeWidget::item:selected, QTableWidget::item:selected {
    background-color: %(accent)s;
    color: #000;
}
QHeaderView::section {
    background-color: %(bg_secondary)s;
    border: 1px solid %(border)s;
    padding: 4px 8px;
    font-weight: bold;
}
QSlider::groove:horizontal {
    height: 6px;
    background: %(border)s;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: %(accent)s;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: %(accent)s;
    border-radius: 3px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 3px;
    border: 1px solid %(border)s;
    background-color: %(input_bg)s;
}
QCheckBox::indicator:checked {
    background-color: %(accent)s;
    border-color: %(accent)s;
}
QLabel {
    background: transparent;
}
QScrollBar:vertical {
    background: %(bg)s;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: %(border)s;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
""" % DARK_THEME
