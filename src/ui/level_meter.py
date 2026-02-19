"""音频电平表组件（QPainter 自绘，达芬奇/Adobe 风格）"""

import numpy as np
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen

from config import DARK_THEME


class AudioLevelMeter(QWidget):
    def __init__(self, parent=None, width=200, height=25):
        super().__init__(parent)
        self._bar_width = width
        self._bar_height = height

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._canvas = _MeterCanvas(self, width, height)
        layout.addWidget(self._canvas)

        self._db_label = QLabel("-∞ dB")
        self._db_label.setFixedWidth(70)
        self._db_label.setStyleSheet(f"font-family: Consolas; font-size: 11px; color: {DARK_THEME['text']};")
        layout.addWidget(self._db_label)

        # 状态
        self.level = 0.0
        self.peak_level = 0.0
        self.peak_hold_time = 0
        self.current_db = -60

    def update_level(self, audio_data):
        try:
            if isinstance(audio_data, bytes):
                audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            elif isinstance(audio_data, np.ndarray):
                audio_array = audio_data
            else:
                return
            if len(audio_array) == 0:
                return

            rms = np.sqrt(np.mean(audio_array ** 2))
            db = 20 * np.log10(rms + 1e-10)
            db = np.clip(db, -60, 0)
            normalized = (db + 60) / 60

            self.level = self.level * 0.7 + normalized * 0.3
            self.current_db = db

            if normalized > self.peak_level:
                self.peak_level = normalized
                self.peak_hold_time = 20
            else:
                self.peak_hold_time -= 1
                if self.peak_hold_time <= 0:
                    self.peak_level *= 0.95

            if db <= -59:
                self._db_label.setText("-∞ dB")
            else:
                self._db_label.setText(f"{db:.1f} dB")

            self._canvas.level = self.level
            self._canvas.peak_level = self.peak_level
            self._canvas.update()
        except Exception:
            pass

    def reset(self):
        self.level = 0.0
        self.peak_level = 0.0
        self.peak_hold_time = 0
        self.current_db = -60
        self._db_label.setText("-∞ dB")
        self._canvas.level = 0.0
        self._canvas.peak_level = 0.0
        self._canvas.update()


class _MeterCanvas(QWidget):
    """电平表绘制画布"""
    def __init__(self, parent, width, height):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.level = 0.0
        self.peak_level = 0.0

        self._green_threshold = 0.6
        self._yellow_threshold = 0.85
        self._color_green = QColor(DARK_THEME['green'])
        self._color_yellow = QColor(DARK_THEME['yellow'])
        self._color_red = QColor(DARK_THEME['red'])
        self._color_bg = QColor('#2b2b2b')
        self._color_border = QColor(DARK_THEME['border'])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 背景
        p.fillRect(0, 0, w, h, self._color_bg)
        p.setPen(QPen(self._color_border, 1))
        p.drawRect(0, 0, w - 1, h - 1)

        bar_w = w * self.level
        if bar_w > 0:
            # 绿色段
            green_end = min(bar_w, w * self._green_threshold)
            if green_end > 0:
                p.fillRect(1, 1, int(green_end) - 1, h - 2, self._color_green)
            # 黄色段
            if bar_w > w * self._green_threshold:
                y_start = int(w * self._green_threshold)
                y_end = int(min(bar_w, w * self._yellow_threshold))
                p.fillRect(y_start, 1, y_end - y_start, h - 2, self._color_yellow)
            # 红色段
            if bar_w > w * self._yellow_threshold:
                r_start = int(w * self._yellow_threshold)
                r_end = int(bar_w)
                p.fillRect(r_start, 1, r_end - r_start, h - 2, self._color_red)

        # 峰值指示线
        if self.peak_level > 0:
            peak_x = int(w * self.peak_level)
            p.setPen(QPen(QColor('white'), 2))
            p.drawLine(peak_x, 0, peak_x, h)

        # 刻度线
        p.setPen(QPen(QColor('#555555'), 1, Qt.DashLine))
        for i in range(1, 5):
            x = int(w * i * 0.2)
            p.drawLine(x, 0, x, h)

        p.end()
