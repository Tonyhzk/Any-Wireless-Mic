"""录音波形可视化组件（pyqtgraph，支持点击跳转和拖动）"""

import wave
import struct
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QTimer

from config import RATE


class WaveformVisualizer(QWidget):
    def __init__(self, parent=None, log_callback=None, click_callback=None):
        super().__init__(parent)
        self.log = log_callback or (lambda m, l: None)
        self.click_callback = click_callback

        self.waveform_data = None
        self.sample_rate = RATE
        self.total_duration = 0
        self.current_progress = 0.0
        self.is_dragging = False
        self._programmatic_move = False  # 标记程序化移动，避免触发拖动逻辑

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # pyqtgraph 绘图
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#34495e')
        self.plot_widget.setLabel('bottom', 'Time (s)', color='white', size='9pt')
        self.plot_widget.setLabel('left', 'Amplitude', color='white', size='9pt')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

        # 波形曲线
        self.curve = self.plot_widget.plot([], [], pen=pg.mkPen('#3498db', width=0.8))

        # 播放位置指示线
        self.play_line = pg.InfiniteLine(pos=0, angle=90,
                                          pen=pg.mkPen('#e74c3c', width=2),
                                          movable=True)
        self.play_line.setVisible(False)
        self.plot_widget.addItem(self.play_line)

        # 拖动事件
        self.play_line.sigPositionChanged.connect(self._on_line_dragged)
        self.play_line.sigPositionChangeFinished.connect(self._on_line_drag_finished)

        # 点击事件
        self.plot_widget.scene().sigMouseClicked.connect(self._on_click)

        layout.addWidget(self.plot_widget)

        # 动画定时器 (~60fps)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_position)
        self.is_animating = False

    def _on_click(self, event):
        if self.waveform_data is None or self.total_duration == 0:
            return
        # 只处理左键
        if event.button() != Qt.LeftButton:
            return
        # 忽略拖动指示线时的点击
        if self.is_dragging:
            return

        pos = event.scenePos()
        mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
        click_time = mouse_point.x()

        if 0 <= click_time <= self.total_duration and self.click_callback:
            progress = click_time / self.total_duration
            progress = max(0.0, min(1.0, progress))
            self.current_progress = progress
            self._set_line_pos(click_time)
            self.click_callback(progress)

    def _on_line_dragged(self):
        if self.waveform_data is None or self.total_duration == 0:
            return
        # 程序化移动不触发拖动逻辑
        if self._programmatic_move:
            return
        self.is_dragging = True
        new_time = self.play_line.value()
        new_time = max(0, min(self.total_duration, new_time))
        self.current_progress = new_time / self.total_duration
        self.play_line.setPen(pg.mkPen('#f39c12', width=2))
        if self.click_callback:
            self.click_callback(self.current_progress)

    def _on_line_drag_finished(self):
        self.is_dragging = False
        self.play_line.setPen(pg.mkPen('#e74c3c', width=2))

    def load_waveform(self, filepath):
        try:
            try:
                with wave.open(filepath, 'rb') as wf:
                    frames = wf.readframes(wf.getnframes())
                    self.sample_rate = wf.getframerate()
                    sample_width = wf.getsampwidth()
                    if sample_width == 2:
                        data = np.frombuffer(frames, dtype=np.int16)
                        self.waveform_data = data.astype(np.float32) / 32768.0
                    elif sample_width == 4:
                        data = np.frombuffer(frames, dtype=np.float32)
                        if np.max(np.abs(data)) <= 2.0:
                            self.waveform_data = data
                        else:
                            data = np.frombuffer(frames, dtype=np.int32)
                            self.waveform_data = data.astype(np.float32) / 2147483648.0
                    elif sample_width == 1:
                        data = np.frombuffer(frames, dtype=np.uint8)
                        self.waveform_data = (data.astype(np.float32) - 128) / 128.0
                    else:
                        raise ValueError(f"不支持的样本宽度: {sample_width} bytes")
            except wave.Error:
                self.waveform_data, self.sample_rate = self._load_float32_wav(filepath)

            if self.waveform_data is None or len(self.waveform_data) == 0:
                raise ValueError("波形数据为空")

            # 降采样显示
            target_points = 10000
            factor = max(1, len(self.waveform_data) // target_points)
            display_data = self.waveform_data[::factor]
            self.total_duration = len(self.waveform_data) / self.sample_rate
            time_axis = np.arange(len(display_data)) * factor / self.sample_rate

            self.curve.setData(time_axis, display_data)
            self.plot_widget.setXRange(0, self.total_duration)
            self.plot_widget.setYRange(-1.0, 1.0)

            self._set_line_pos(0)
            self.play_line.setVisible(True)
            self.play_line.setBounds([0, self.total_duration])
            self.current_progress = 0.0

            self.log(f"波形加载成功，时长: {self.total_duration:.2f}秒", "INFO")
            return True
        except Exception as e:
            self.log(f"加载波形失败: {e}", "ERROR")
            return False

    def _load_float32_wav(self, filepath):
        with open(filepath, 'rb') as f:
            riff = f.read(4)
            if riff != b'RIFF':
                raise ValueError("不是有效的 WAV 文件")
            f.read(4)
            wave_id = f.read(4)
            if wave_id != b'WAVE':
                raise ValueError("不是有效的 WAVE 文件")
            sample_rate = 44100
            audio_data = None
            while True:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack('<I', f.read(4))[0]
                if chunk_id == b'fmt ':
                    struct.unpack('<H', f.read(2))[0]  # format
                    struct.unpack('<H', f.read(2))[0]  # channels
                    sample_rate = struct.unpack('<I', f.read(4))[0]
                    f.read(4)  # byte_rate
                    f.read(2)  # block_align
                    struct.unpack('<H', f.read(2))[0]  # bits
                    remaining = chunk_size - 16
                    if remaining > 0:
                        f.read(remaining)
                elif chunk_id == b'data':
                    raw_data = f.read(chunk_size)
                    audio_data = np.frombuffer(raw_data, dtype=np.float32)
                    break
                else:
                    f.read(chunk_size)
            if audio_data is None:
                raise ValueError("未找到音频数据")
            return audio_data, sample_rate

    def _set_line_pos(self, pos):
        """程序化设置指示线位置，不触发拖动逻辑"""
        self._programmatic_move = True
        self.play_line.setPos(pos)
        self._programmatic_move = False

    def update_play_position(self, progress):
        if self.waveform_data is None or self.total_duration == 0:
            return
        self.current_progress = progress
        if not self.is_dragging:
            self._set_line_pos(progress * self.total_duration)

    def _animate_position(self):
        if self.waveform_data is not None and self.total_duration > 0 and not self.is_dragging:
            new_pos = self.current_progress * self.total_duration
            self._set_line_pos(new_pos)

    def start_animation(self):
        if not self.is_animating and self.waveform_data is not None:
            self.is_animating = True
            self._anim_timer.start(16)  # ~60fps
            self.log(f"波形动画已启动 (60fps), total_duration={self.total_duration:.2f}s", "INFO")

    def stop_animation(self):
        self._anim_timer.stop()
        self.is_animating = False
        self.log("波形动画已停止", "INFO")

    def clear(self):
        self.stop_animation()
        self.curve.setData([], [])
        self.play_line.setVisible(False)
        self.waveform_data = None
        self.current_progress = 0.0
