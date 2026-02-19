"""实时音频流波形可视化组件（pyqtgraph）"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import QTimer


class RealtimeWaveformVisualizer(QWidget):
    def __init__(self, parent=None, log_callback=None, duration_seconds=10):
        super().__init__(parent)
        self.log = log_callback or (lambda m, l: None)
        self.sample_rate = 44100
        self.duration_seconds = duration_seconds
        self.buffer_size = self.sample_rate * self.duration_seconds
        self.waveform_buffer = np.zeros(self.buffer_size)
        self.display_points = 2000
        self.is_running = False

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # pyqtgraph 绘图
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#34495e')
        self.plot_widget.setYRange(-1.0, 1.0)
        self.plot_widget.setXRange(-self.duration_seconds, 0)
        self.plot_widget.setLabel('bottom', 'Time (s)', color='white', size='8pt')
        self.plot_widget.getAxis('left').setTicks([])
        self.plot_widget.showGrid(x=False, y=False)

        # 零线
        self.plot_widget.addLine(y=0, pen=pg.mkPen('#7f8c8d', width=1, style=pg.QtCore.Qt.DashLine))

        # 波形曲线
        time_axis = np.linspace(-self.duration_seconds, 0, self.display_points)
        self.curve = self.plot_widget.plot(time_axis, np.zeros(self.display_points),
                                           pen=pg.mkPen('#3498db', width=1.5))
        layout.addWidget(self.plot_widget)

        # 刷新定时器 (~30fps)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_plot)

    def _downsample_for_display(self, data):
        if len(data) <= self.display_points:
            return data
        chunk_size = len(data) // self.display_points
        result = np.empty(self.display_points)
        for i in range(self.display_points):
            start = i * chunk_size
            end = start + chunk_size
            chunk = data[start:end]
            if len(chunk) > 0:
                result[i] = np.max(chunk) if i % 2 == 0 else np.min(chunk)
            else:
                result[i] = 0
        return result

    def set_duration(self, duration_seconds):
        if duration_seconds == self.duration_seconds:
            return
        old = self.duration_seconds
        self.duration_seconds = duration_seconds
        self.buffer_size = self.sample_rate * duration_seconds
        self.waveform_buffer = np.zeros(self.buffer_size)
        self.plot_widget.setXRange(-duration_seconds, 0)
        time_axis = np.linspace(-duration_seconds, 0, self.display_points)
        self.curve.setData(time_axis, np.zeros(self.display_points))
        self.log(f"实时波形历史时长已调整: {old}s → {duration_seconds}s", "INFO")

    def update_data(self, audio_data):
        try:
            if isinstance(audio_data, bytes):
                audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            elif isinstance(audio_data, np.ndarray):
                audio_array = audio_data
            else:
                return
            if len(audio_array) > self.buffer_size:
                audio_array = audio_array[-self.buffer_size:]
            shift = len(audio_array)
            self.waveform_buffer = np.roll(self.waveform_buffer, -shift)
            self.waveform_buffer[-shift:] = audio_array
        except Exception:
            pass

    def _update_plot(self):
        if self.is_running:
            display_data = self._downsample_for_display(self.waveform_buffer)
            self.curve.setData(
                np.linspace(-self.duration_seconds, 0, self.display_points),
                display_data
            )

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._timer.start(33)  # ~30fps
            self.log("实时波形显示已启动", "INFO")

    def stop(self):
        self._timer.stop()
        self.is_running = False
        self.waveform_buffer = np.zeros(self.buffer_size)
        self.curve.setData(
            np.linspace(-self.duration_seconds, 0, self.display_points),
            np.zeros(self.display_points)
        )
        self.log("实时波形显示已停止", "INFO")
