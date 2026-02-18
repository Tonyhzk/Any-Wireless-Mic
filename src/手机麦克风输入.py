import os
import sys

# macOS 兼容性修复：在导入其他库之前强制配置 eventlet 使用 selects hub
if sys.platform == "darwin":
    # 注意：eventlet 的 select hub 模块名为 'selects' (带s)
    os.environ['EVENTLET_HUB'] = 'selects'
    try:
        import eventlet
        eventlet.hubs.use_hub('selects')
    except:
        pass

import json
import time
import socket
import threading
import queue
import wave
import logging
from pathlib import Path
from datetime import datetime

# pip install flask flask-socketio eventlet pyaudio ttkbootstrap qrcode pillow pyopenssl matplotlib numpy send2trash scipy

import tkinter as tk
from tkinter import messagebox, filedialog
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from PIL import Image, ImageTk
import qrcode
import pyaudio
from flask import Flask, render_template, request, jsonify, send_file, abort
from flask_socketio import SocketIO, emit
from OpenSSL import crypto
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.animation import FuncAnimation
import numpy as np
from scipy import signal as scipy_signal  # 替代 audioop 进行重采样 (Python 3.13+ 兼容)
from send2trash import send2trash

# ========== 全局配置 ==========
# 窗口设置
WINDOW_TITLE = "局域网无线麦克风"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 950
WINDOW_MIN_WIDTH = 1000
WINDOW_MIN_HEIGHT = 700

# 功能开关
ENABLE_TRAY = False  # 托盘功能开关
ENABLE_LOG_FILE = False  # 日志文件写入开关
ENABLE_DEBUG = True  # DEBUG模式开关

# 主题设置
DEFAULT_THEME = "darkly"
THEME_LIGHT = "litera"
THEME_DARK = "darkly"

# 文件设置
CONFIG_FILE_NAME = "mobile_mic_config.json"
LOG_FILE_NAME = "mobile_mic.log"
CERT_FILE_NAME = "server.crt"
KEY_FILE_NAME = "server.key"
RECORD_DIR = "records"

# 音频设置
CHUNK = 1024
FORMAT = pyaudio.paInt16
FORMAT_FLOAT32 = pyaudio.paFloat32  # Float32 格式（原生高音质模式）
CHANNELS = 1
RATE = 44100

# 网络设置
DEFAULT_PORT = 5001
MIN_PORT = 1024
MAX_PORT = 65535

# UI组件设置
BUTTON_WIDTH = 12
LOG_DISPLAY_HEIGHT = 6

# 音频播放设置
ENABLE_REALTIME_PLAYBACK = True  # 默认开启实时播放
# ========== 全局配置结束 ==========

class AudioLevelMeter:
    """音频电平表组件（类似达芬奇/Adobe风格）"""
    def __init__(self, parent, width=200, height=30):
        self.parent = parent
        self.width = width
        self.height = height
        
        # 创建容器Frame
        self.container = ttk.Frame(parent)
        
        # 创建Canvas（增加高度避免裁切）
        self.canvas = tk.Canvas(self.container, width=width, height=height, bg='#1a1a1a', highlightthickness=1, highlightbackground='#444444')
        self.canvas.pack(side=LEFT, padx=(0, 5))
        
        # 创建dB标签
        self.db_label = ttk.Label(self.container, text="-∞ dB", font=("Consolas", 10), width=10, anchor=W)
        self.db_label.pack(side=LEFT)
        
        # 电平值（0.0 - 1.0）
        self.level = 0.0
        self.peak_level = 0.0
        self.peak_hold_time = 0
        self.current_db = -60  # 当前dB值
        
        # 颜色阈值（归一化值）
        self.green_threshold = 0.6   # 0-60% 绿色
        self.yellow_threshold = 0.85  # 60-85% 黄色
        # 85-100% 红色
        
        # 颜色定义
        self.color_green = '#28a745'
        self.color_yellow = '#ffc107'
        self.color_red = '#dc3545'
        self.color_bg = '#2b2b2b'
        self.color_border = '#444444'
        
        # 绘制初始状态
        self.draw_meter()
    
    def draw_meter(self):
        """绘制电平表"""
        self.canvas.delete("all")
        
        # 绘制背景边框
        self.canvas.create_rectangle(0, 0, self.width, self.height, 
                                     fill=self.color_bg, outline=self.color_border)
        
        # 计算各段宽度
        bar_width = self.width * self.level
        
        # 绘制电平条
        if bar_width > 0:
            # 绿色段
            green_end = min(bar_width, self.width * self.green_threshold)
            if green_end > 0:
                self.canvas.create_rectangle(1, 1, green_end, self.height-1, 
                                            fill=self.color_green, outline='')
            
            # 黄色段
            if bar_width > self.width * self.green_threshold:
                yellow_start = self.width * self.green_threshold
                yellow_end = min(bar_width, self.width * self.yellow_threshold)
                self.canvas.create_rectangle(yellow_start, 1, yellow_end, self.height-1, 
                                            fill=self.color_yellow, outline='')
            
            # 红色段
            if bar_width > self.width * self.yellow_threshold:
                red_start = self.width * self.yellow_threshold
                red_end = bar_width
                self.canvas.create_rectangle(red_start, 1, red_end, self.height-1, 
                                            fill=self.color_red, outline='')
        
        # 绘制峰值指示线（白色竖线）
        if self.peak_level > 0:
            peak_x = self.width * self.peak_level
            self.canvas.create_line(peak_x, 0, peak_x, self.height, 
                                   fill='white', width=2)
        
        # 绘制刻度线（每20%一条）
        for i in range(1, 5):
            x = self.width * (i * 0.2)
            self.canvas.create_line(x, 0, x, self.height, 
                                   fill='#555555', width=1, dash=(2, 2))
    
    def update_level(self, audio_data):
        """更新电平值"""
        try:
            # 将音频数据转换为numpy数组
            if isinstance(audio_data, bytes):
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                audio_array = audio_array.astype(np.float32) / 32768.0
            elif isinstance(audio_data, np.ndarray):
                audio_array = audio_data
            else:
                return
            
            if len(audio_array) == 0:
                return
            
            # 计算RMS（均方根）电平
            rms = np.sqrt(np.mean(audio_array ** 2))
            
            # 转换为对数刻度（dB）然后归一化到 0-1
            # -60dB 到 0dB 映射到 0-1
            db = 20 * np.log10(rms + 1e-10)  # 避免log(0)
            db = np.clip(db, -60, 0)  # 限制范围
            normalized = (db + 60) / 60  # 归一化到 0-1
            
            # 平滑处理（避免抖动）
            self.level = self.level * 0.7 + normalized * 0.3
            self.current_db = db  # 保存当前dB值
            
            # 更新峰值
            if normalized > self.peak_level:
                self.peak_level = normalized
                self.peak_hold_time = 20  # 持续20帧（约1秒）
            else:
                self.peak_hold_time -= 1
                if self.peak_hold_time <= 0:
                    # 峰值缓慢下降
                    self.peak_level *= 0.95
            
            # 更新dB标签
            if db <= -59:
                self.db_label.config(text="-∞ dB")
            else:
                self.db_label.config(text=f"{db:.1f} dB")
            
            # 重绘
            self.draw_meter()
            
        except Exception:
            pass
    
    def reset(self):
        """重置电平表"""
        self.level = 0.0
        self.peak_level = 0.0
        self.peak_hold_time = 0
        self.current_db = -60
        self.db_label.config(text="-∞ dB")
        self.draw_meter()
    
    def get_widget(self):
        """获取容器组件"""
        return self.container

class AudioPlayer:
    """音频播放引擎"""
    def __init__(self, log_callback):
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.log = log_callback
        self.is_playing = False
        self.is_paused = False
        self.current_file = None
        self.wav_file = None
        self.total_frames = 0
        self.current_frame = 0
        self.play_thread = None
        self.stop_flag = False
        self.seek_request = None  # 用于线程间通信的跳转请求
        
    def load_file(self, filepath):
        """加载音频文件"""
        try:
            self.stop()
            
            # 先尝试标准 wave 模块加载
            try:
                self.wav_file = wave.open(filepath, 'rb')
                self.total_frames = self.wav_file.getnframes()
                self.current_frame = 0
                self.current_file = filepath
                self.log(f"已加载音频文件: {Path(filepath).name}", "INFO")
                return True
            except wave.Error:
                # 可能是 Float32 格式，尝试手动解析头信息
                try:
                    import struct
                    with open(filepath, 'rb') as f:
                        # 简单的头信息检查
                        riff = f.read(4)
                        f.read(4) # size
                        wave_id = f.read(4)
                        if riff != b'RIFF' or wave_id != b'WAVE':
                            raise ValueError("Invalid WAV")
                        
                        # 寻找 fmt 和 data
                        fmt_found = False
                        data_found = False
                        total_frames = 0
                        
                        while True:
                            chunk_id = f.read(4)
                            if len(chunk_id) < 4: break
                            chunk_size = struct.unpack('<I', f.read(4))[0]
                            
                            if chunk_id == b'fmt ':
                                audio_format = struct.unpack('<H', f.read(2))[0]
                                channels = struct.unpack('<H', f.read(2))[0]
                                rate = struct.unpack('<I', f.read(4))[0]
                                f.read(4) # byte rate
                                f.read(2) # align
                                bits = struct.unpack('<H', f.read(2))[0]
                                
                                if audio_format == 3 and bits == 32:
                                    # 是 IEEE Float 32-bit
                                    fmt_found = True
                                
                                # 跳过剩余部分
                                f.read(chunk_size - 16)
                            elif chunk_id == b'data':
                                data_found = True
                                total_frames = chunk_size // 4 # 32-bit = 4 bytes
                                break
                            else:
                                f.read(chunk_size)
                        
                        if fmt_found and data_found:
                            # 是我们自己保存的 Float32 WAV
                            self.wav_file = None # 标记为特殊处理
                            self.float32_file = filepath
                            self.total_frames = total_frames
                            self.current_frame = 0
                            self.current_file = filepath
                            self.log(f"已加载 Float32 音频文件: {Path(filepath).name}", "INFO")
                            return True
                except:
                    pass
                raise # 重新抛出原始错误
                
        except Exception as e:
            self.log(f"加载音频文件失败: {e}", "ERROR")
            return False
    
    def play(self):
        """播放音频"""
        if not self.wav_file and not hasattr(self, 'float32_file'):
            self.log("请先加载音频文件", "WARNING")
            return False
        
        if self.is_paused:
            self.is_paused = False
            self.is_playing = True
            self.log("继续播放", "INFO")
            return True
        
        self.stop_flag = False
        self.is_playing = True
        self.play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self.play_thread.start()
        self.log("开始播放", "INFO")
        return True
    
    def _play_worker(self):
        """播放工作线程"""
        try:
            chunk_size = 1024
            
            # 检查是否是特殊 Float32 文件
            is_float32 = False
            if hasattr(self, 'float32_file') and self.wav_file is None:
                is_float32 = True
                f = open(self.float32_file, 'rb')
                # 跳过头文件到数据区 (简单处理：假设 header 44 bytes)
                f.seek(44) 
            
            if not self.stream:
                if is_float32:
                    self.stream = self.pa.open(
                        format=pyaudio.paFloat32,
                        channels=1,
                        rate=44100,
                        output=True
                    )
                else:
                    self.stream = self.pa.open(
                        format=self.pa.get_format_from_width(self.wav_file.getsampwidth()),
                        channels=self.wav_file.getnchannels(),
                        rate=self.wav_file.getframerate(),
                        output=True
                    )
            
            while self.is_playing and not self.stop_flag:
                # 处理跳转请求
                if self.seek_request is not None:
                    target_frame = self.seek_request
                    self.seek_request = None
                    self.current_frame = target_frame
                    
                    if is_float32:
                        # Float32 (4 bytes) + 44 bytes header
                        offset = 44 + (target_frame * 4)
                        f.seek(offset)
                    elif self.wav_file:
                        self.wav_file.setpos(target_frame)
                
                if self.is_paused:
                    time.sleep(0.1)
                    continue
                
                if is_float32:
                    data = f.read(chunk_size * 4) # 4 bytes per sample
                else:
                    data = self.wav_file.readframes(chunk_size)
                    
                if not data:
                    self.is_playing = False
                    self.current_frame = 0
                    if is_float32:
                        f.seek(44)
                    else:
                        self.wav_file.rewind()
                    self.log("播放完成", "SUCCESS")
                    break
                
                self.stream.write(data)
                self.current_frame += chunk_size
            
            if is_float32:
                f.close()
                
        except Exception as e:
            self.log(f"播放错误: {e}", "ERROR")
            self.is_playing = False
    
    def pause(self):
        """暂停播放"""
        if self.is_playing and not self.is_paused:
            self.is_paused = True
            self.log("已暂停", "INFO")
            return True
        return False
    
    def stop(self):
        """停止播放"""
        self.stop_flag = True
        self.is_playing = False
        self.is_paused = False
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        
        if self.wav_file:
            self.wav_file.rewind()
            self.current_frame = 0
        elif hasattr(self, 'float32_file'):
            self.current_frame = 0
        
        self.log("已停止播放", "INFO")
    
    def seek(self, frame_position):
        """跳转到指定帧位置"""
        # 验证范围
        if not (0 <= frame_position <= self.total_frames):
            return False
            
        if self.is_playing:
            # 如果正在播放，通过 seek_request 通知线程跳转
            self.seek_request = frame_position
            # 同时更新 current_frame 以便 UI 立即响应
            self.current_frame = frame_position
            return True
        else:
            # 如果未播放，直接设置位置
            self.current_frame = frame_position
            if self.wav_file:
                self.wav_file.setpos(frame_position)
            # Float32 文件将在 _play_worker 启动时 seek
            return True
    
    def get_progress(self):
        """获取播放进度 (0.0 - 1.0)"""
        if self.total_frames > 0:
            return min(1.0, self.current_frame / self.total_frames)
        return 0.0
    
    def get_duration(self):
        """获取总时长（秒）"""
        if self.wav_file:
            return self.total_frames / self.wav_file.getframerate()
        elif hasattr(self, 'float32_file'):
            return self.total_frames / 44100
        return 0
    
    def get_current_time(self):
        """获取当前播放时间（秒）"""
        if self.wav_file:
            return self.current_frame / self.wav_file.getframerate()
        elif hasattr(self, 'float32_file'):
            return self.current_frame / 44100
        return 0
    
    def close(self):
        """关闭播放器"""
        self.stop()
        if self.wav_file:
            self.wav_file.close()
        self.pa.terminate()

class RealtimeWaveformVisualizer:
    """实时音频流波形可视化组件"""
    def __init__(self, parent, log_callback, duration_seconds=10):
        self.parent = parent
        self.log = log_callback
        
        # 创建图形（紧凑型）
        self.fig = Figure(figsize=(4, 1.5), dpi=80, facecolor='#2b3e50')
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas_widget = self.canvas.get_tk_widget()
        
        # 波形数据缓冲区配置
        self.sample_rate = 44100
        self.duration_seconds = duration_seconds
        self.buffer_size = self.sample_rate * self.duration_seconds
        self.waveform_buffer = np.zeros(self.buffer_size)
        
        # 显示优化：降采样到固定点数
        self.display_points = 2000
        
        # 绘图元素
        self.line = None
        self.animation = None
        self.is_running = False
        
        self._setup_plot()
    
    def _setup_plot(self):
        """设置绘图样式"""
        self.ax.set_facecolor('#34495e')
        self.ax.set_ylim(-1.0, 1.0)
        
        # X轴显示时间（从-duration到0）
        self.ax.set_xlim(-self.duration_seconds, 0)
        self.ax.set_xlabel('Time (s)', color='white', fontsize=8)
        
        # 设置时间刻度
        if self.duration_seconds <= 10:
            tick_interval = 2
        elif self.duration_seconds <= 20:
            tick_interval = 5
        else:
            tick_interval = 10
        
        time_ticks = np.arange(-self.duration_seconds, 1, tick_interval)
        self.ax.set_xticks(time_ticks)
        self.ax.set_xticklabels([f'{int(t)}' for t in time_ticks], fontsize=7, color='white')
        
        self.ax.set_yticks([])
        self.ax.axhline(y=0, color='#7f8c8d', linewidth=1, alpha=0.5)
        
        # 创建时间轴用于绘图
        time_axis = np.linspace(-self.duration_seconds, 0, self.display_points)
        display_data = self._downsample_for_display(self.waveform_buffer)
        self.line, = self.ax.plot(time_axis, display_data, color='#3498db', linewidth=1.5)
        
        self.ax.spines['bottom'].set_color('#7f8c8d')
        self.ax.spines['top'].set_color('#7f8c8d')
        self.ax.spines['left'].set_color('#7f8c8d')
        self.ax.spines['right'].set_color('#7f8c8d')
        self.fig.tight_layout(pad=0.3)
    
    def _downsample_for_display(self, data):
        """降采样数据用于显示"""
        if len(data) <= self.display_points:
            return data
        
        # 使用分块最大值/最小值降采样（保留波形特征）
        chunk_size = len(data) // self.display_points
        downsampled = []
        for i in range(self.display_points):
            start_idx = i * chunk_size
            end_idx = start_idx + chunk_size
            if end_idx > len(data):
                end_idx = len(data)
            chunk = data[start_idx:end_idx]
            if len(chunk) > 0:
                # 交替使用最大值和最小值，保留波形包络
                if i % 2 == 0:
                    downsampled.append(np.max(chunk))
                else:
                    downsampled.append(np.min(chunk))
            else:
                downsampled.append(0)
        
        return np.array(downsampled)
    
    def set_duration(self, duration_seconds):
        """动态调整历史显示时长"""
        if duration_seconds == self.duration_seconds:
            return
        
        old_duration = self.duration_seconds
        self.duration_seconds = duration_seconds
        self.buffer_size = self.sample_rate * self.duration_seconds
        
        # 重新初始化缓冲区
        self.waveform_buffer = np.zeros(self.buffer_size)
        
        # 重新设置绘图
        self._setup_plot()
        self.canvas.draw()
        
        self.log(f"实时波形历史时长已调整: {old_duration}s → {duration_seconds}s", "INFO")
    
    def update_data(self, audio_data):
        """更新音频数据"""
        try:
            if isinstance(audio_data, bytes):
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                audio_array = audio_array.astype(np.float32) / 32768.0
            elif isinstance(audio_data, np.ndarray):
                audio_array = audio_data
            else:
                return
            
            if len(audio_array) > self.buffer_size:
                audio_array = audio_array[-self.buffer_size:]
            
            shift_size = len(audio_array)
            self.waveform_buffer = np.roll(self.waveform_buffer, -shift_size)
            self.waveform_buffer[-shift_size:] = audio_array
        except Exception:
            pass
    
    def _animate(self, frame):
        """动画更新函数"""
        if self.is_running:
            # 降采样后更新显示
            display_data = self._downsample_for_display(self.waveform_buffer)
            self.line.set_ydata(display_data)
        return [self.line]
    
    def start(self):
        """启动实时更新动画"""
        if not self.is_running:
            self.is_running = True
            self.animation = FuncAnimation(
                self.fig, 
                self._animate, 
                interval=33,
                blit=True,
                cache_frame_data=False
            )
            self.canvas.draw()
            self.log("实时波形显示已启动", "INFO")
    
    def stop(self):
        """停止实时更新"""
        if self.animation:
            self.animation.event_source.stop()
            self.animation = None
        self.is_running = False
        self.waveform_buffer = np.zeros(self.buffer_size)
        if self.line:
            self.line.set_ydata(self.waveform_buffer)
            self.canvas.draw()
        self.log("实时波形显示已停止", "INFO")
    
    def get_widget(self):
        """获取画布组件"""
        return self.canvas_widget

class WaveformVisualizer:
    """波形可视化组件"""
    def __init__(self, parent, log_callback, click_callback=None):
        self.parent = parent
        self.log = log_callback
        self.click_callback = click_callback
        
        # 设置中文字体
        import matplotlib.font_manager as fm
        # 尝试使用系统中文字体
        try:
            plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
        except:
            pass
        
        self.fig = Figure(figsize=(8, 2), dpi=100, facecolor='#2b3e50')
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas_widget = self.canvas.get_tk_widget()
        
        self.waveform_data = None
        self.play_line = None
        self.sample_rate = RATE
        self.total_duration = 0
        self.current_progress = 0.0
        
        # 动画相关
        self.animation = None
        self.is_animating = False
        
        # 拖动相关
        self.is_dragging = False
        self.drag_tolerance = 0.5  # 竖线拖动容差（秒）
        self.original_line_color = '#e74c3c'
        self.drag_line_color = '#f39c12'  # 拖动时的高亮色（橙色）
        
        # 绑定事件
        self.canvas.mpl_connect('button_press_event', self._on_click)
        self.canvas.mpl_connect('motion_notify_event', self._on_motion)
        self.canvas.mpl_connect('button_release_event', self._on_release)
        
        self._setup_plot()
    
    def _setup_plot(self):
        """设置绘图样式"""
        self.ax.set_facecolor('#34495e')
        self.ax.tick_params(colors='white', labelsize=8)
        self.ax.spines['bottom'].set_color('white')
        self.ax.spines['top'].set_color('white')
        self.ax.spines['left'].set_color('white')
        self.ax.spines['right'].set_color('white')
        self.fig.tight_layout()
    
    def _safe_remove_play_line(self):
        """安全移除播放指示线"""
        if self.play_line:
            try:
                # 检查指示线是否在 axes 中
                if self.play_line in self.ax.lines:
                    self.play_line.remove()
            except Exception:
                pass
            self.play_line = None

    def _on_click(self, event):
        """波形图点击事件"""
        if event.inaxes == self.ax and self.waveform_data is not None:
            if self.total_duration > 0:
                current_time = self.current_progress * self.total_duration
                click_time = event.xdata
                
                # 检测是否点击在竖线附近（容差范围内）
                if abs(click_time - current_time) <= self.drag_tolerance:
                    # 开始拖动竖线
                    self.is_dragging = True
                    self.canvas_widget.config(cursor="sb_h_double_arrow")  # 改变光标为左右箭头
                    self.log(f"开始拖动竖线", "INFO")
                else:
                    # 点击波形，执行跳转
                    if self.click_callback:
                        progress = click_time / self.total_duration
                        progress = max(0.0, min(1.0, progress))
                        # 更新进度
                        self.current_progress = progress
                        # 调用回调函数
                        self.click_callback(progress)
                        # 如果动画未运行，手动更新竖线显示
                        if not self.is_animating:
                            self._safe_remove_play_line()
                            new_time = progress * self.total_duration
                            self.play_line = self.ax.axvline(x=new_time, color=self.original_line_color, linewidth=2, alpha=0.8)
                            self.canvas.draw_idle()
    
    def _on_motion(self, event):
        """鼠标移动事件"""
        if self.is_dragging and event.inaxes == self.ax and self.waveform_data is not None:
            if self.total_duration > 0:
                # 拖动竖线到鼠标位置
                new_time = event.xdata
                # 限制在有效范围内
                new_time = max(0, min(self.total_duration, new_time))
                new_progress = new_time / self.total_duration
                
                # 更新播放位置
                self.current_progress = new_progress
                
                # 手动更新竖线显示（不依赖动画）
                self._safe_remove_play_line()
                self.play_line = self.ax.axvline(x=new_time, color=self.drag_line_color, linewidth=2, alpha=0.9)
                self.canvas.draw_idle()  # 使用draw_idle提高性能
                
                # 如果有回调函数，通知外部更新播放位置
                if self.click_callback:
                    self.click_callback(new_progress)
    
    def _on_release(self, event):
        """鼠标释放事件"""
        if self.is_dragging:
            self.is_dragging = False
            self.canvas_widget.config(cursor="")  # 恢复默认光标
            
            # 拖动结束后恢复正常颜色的竖线
            self._safe_remove_play_line()
            current_time = self.current_progress * self.total_duration
            self.play_line = self.ax.axvline(x=current_time, color=self.original_line_color, linewidth=2, alpha=0.8)
            self.canvas.draw_idle()
            
            self.log(f"拖动结束", "INFO")
    
    def load_waveform(self, filepath):
        """加载并显示波形 - 支持 16-bit 和 32-bit Float WAV"""
        try:
            # 首先尝试标准 wave 模块读取
            try:
                with wave.open(filepath, 'rb') as wf:
                    frames = wf.readframes(wf.getnframes())
                    self.sample_rate = wf.getframerate()
                    sample_width = wf.getsampwidth()
                    
                    # 根据样本宽度选择数据类型
                    if sample_width == 2:
                        # 16-bit Int
                        data = np.frombuffer(frames, dtype=np.int16)
                        # 归一化到 -1.0 ~ 1.0
                        self.waveform_data = data.astype(np.float32) / 32768.0
                    elif sample_width == 4:
                        # 32-bit (可能是 Float 或 Int32)
                        # 先尝试作为 Float32 读取
                        data = np.frombuffer(frames, dtype=np.float32)
                        max_val = np.max(np.abs(data))
                        if max_val <= 2.0:
                            # 是 Float32 格式
                            self.waveform_data = data
                        else:
                            # 可能是 Int32 格式，归一化
                            data = np.frombuffer(frames, dtype=np.int32)
                            self.waveform_data = data.astype(np.float32) / 2147483648.0
                    elif sample_width == 1:
                        # 8-bit
                        data = np.frombuffer(frames, dtype=np.uint8)
                        self.waveform_data = (data.astype(np.float32) - 128) / 128.0
                    else:
                        raise ValueError(f"不支持的样本宽度: {sample_width} bytes")
                        
            except wave.Error:
                # wave 模块无法读取 (可能是 IEEE Float WAV)
                # 手动解析 WAV 文件
                self.waveform_data, self.sample_rate = self._load_float32_wav(filepath)
            
            # 确保波形数据有效
            if self.waveform_data is None or len(self.waveform_data) == 0:
                raise ValueError("波形数据为空")
            
            # 大幅降采样以提高显示性能
            target_points = 10000
            downsample_factor = max(1, len(self.waveform_data) // target_points)
            display_data = self.waveform_data[::downsample_factor]
            
            # 计算总时长
            self.total_duration = len(self.waveform_data) / self.sample_rate
            
            # 绘制波形
            self.ax.clear()
            time_axis = np.arange(len(display_data)) * downsample_factor / self.sample_rate
            self.ax.plot(time_axis, display_data, color='#3498db', linewidth=0.8)
            self.ax.set_xlabel('Time (s)', color='white', fontsize=9)
            self.ax.set_ylabel('Amplitude', color='white', fontsize=9)
            self.ax.grid(True, alpha=0.2, color='white')
            self._setup_plot()
            self.canvas.draw()
            
            self.log(f"波形加载成功，时长: {self.total_duration:.2f}秒", "INFO")
            return True
            
        except Exception as e:
            self.log(f"加载波形失败: {e}", "ERROR")
            return False
    
    def _load_float32_wav(self, filepath):
        """手动解析 32-bit Float WAV 文件"""
        import struct
        
        with open(filepath, 'rb') as f:
            # 读取 RIFF 头
            riff = f.read(4)
            if riff != b'RIFF':
                raise ValueError("不是有效的 WAV 文件")
            
            f.read(4)  # 文件大小
            wave_id = f.read(4)
            if wave_id != b'WAVE':
                raise ValueError("不是有效的 WAVE 文件")
            
            sample_rate = 44100
            audio_data = None
            
            # 读取子块
            while True:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                
                chunk_size = struct.unpack('<I', f.read(4))[0]
                
                if chunk_id == b'fmt ':
                    audio_format = struct.unpack('<H', f.read(2))[0]
                    num_channels = struct.unpack('<H', f.read(2))[0]
                    sample_rate = struct.unpack('<I', f.read(4))[0]
                    f.read(4)  # byte_rate
                    f.read(2)  # block_align
                    bits_per_sample = struct.unpack('<H', f.read(2))[0]
                    
                    # 跳过剩余的 fmt 数据
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
    
    def update_play_position(self, progress):
        """更新播放位置指示线（存储进度，由动画更新）"""
        if self.waveform_data is None or self.total_duration == 0:
            return
        self.current_progress = progress
        
        # 如果动画未运行，手动更新竖线显示
        if not self.is_animating:
            self._safe_remove_play_line()
            current_time = self.current_progress * self.total_duration
            self.play_line = self.ax.axvline(x=current_time, color=self.original_line_color, linewidth=2, alpha=0.8)
            self.canvas.draw_idle()
    
    def _animate_position(self, frame):
        """动画更新函数（高频率调用）"""
        if self.waveform_data is None or self.total_duration == 0:
            return []
        
        try:
            # 移除旧的指示线
            self._safe_remove_play_line()
            
            # 绘制新的指示线
            current_time = self.current_progress * self.total_duration
            self.play_line = self.ax.axvline(x=current_time, color='#e74c3c', linewidth=2, alpha=0.8)
            
            return [self.play_line]
        except Exception as e:
            return []
    
    def start_animation(self):
        """启动动画（60fps刷新）"""
        if not self.is_animating and self.waveform_data is not None:
            self.is_animating = True
            # 使用blit=True启用blitting优化，interval=16约等于60fps
            self.animation = FuncAnimation(
                self.fig, 
                self._animate_position,
                interval=16,  # 约60fps
                blit=True,
                cache_frame_data=False
            )
            self.canvas.draw()
            self.log("波形动画已启动 (60fps)", "INFO")
    
    def stop_animation(self):
        """停止动画"""
        if self.animation:
            self.animation.event_source.stop()
            self.animation = None
            self.is_animating = False
            self.log("波形动画已停止", "INFO")
    
    def clear(self):
        """清空波形"""
        self.stop_animation()
        self.ax.clear()
        self._setup_plot()
        self.canvas.draw()
        self.waveform_data = None
        self.play_line = None
        self.current_progress = 0.0
    
    def get_widget(self):
        """获取画布组件"""
        return self.canvas_widget

class AudioEngine:
    """音频处理引擎 - 支持 Int16 和 Float32 双格式，双输出设备（监听+虚拟麦克风）"""
    def __init__(self, log_callback):
        self.pa = pyaudio.PyAudio()
        # 双输出流：监听设备 + 虚拟麦克风设备
        self.monitor_stream = None      # 监听流（输出到耳机/扬声器）
        self.virtual_mic_stream = None  # 虚拟麦克风流（输出到 Virtual Cable）
        self.lock = threading.Lock()    # 线程锁，保护流操作
        self.log = log_callback
        self.is_recording = False
        self.record_frames = []
        
        # 双设备索引
        self.monitor_device_index = None      # 监听设备索引
        self.virtual_mic_device_index = None  # 虚拟麦克风设备索引
        
        # 播放控制
        self.enable_monitor_playback = True      # 是否启用监听播放
        self.enable_virtual_mic_output = True    # 是否启用虚拟麦克风输出
        
        # 音频格式状态（手动切换模式）
        self.current_format = FORMAT  # 当前流的格式 (paInt16 或 paFloat32)
        self.recording_format = FORMAT  # 录制时的格式
        self.recording_sample_rate = RATE # 录制时的采样率
        self.is_float32_mode = False  # 是否处于 Float32 模式（由手机端控制）
        
        # 采样率控制
        self.target_sample_rate = RATE  # 目标采样率 (44100)
        self.input_sample_rate = RATE   # 输入采样率 (默认为44100，可变)
        self.resample_state = None      # audioop 重采样状态
        
        # 实时波形更新回调
        self.waveform_callback = None
    
    def set_input_sample_rate(self, rate):
        """设置输入采样率"""
        if rate != self.input_sample_rate:
            self.input_sample_rate = rate
            self.resample_state = None # 重置状态
            self.log(f"输入采样率调整为: {rate} Hz", "INFO")

    def set_float32_mode(self, enabled):
        """
        手动设置 Float32 模式（由手机端触发）
        
        注意：输出流始终使用 Int16 格式，Float32 数据在播放时会被转换
        这样可以保证最大兼容性，同时录制时保留高音质原始数据
        """
        if self.is_float32_mode == enabled:
            return  # 状态未变化
        
        self.is_float32_mode = enabled
        
        if enabled:
            self.recording_format = FORMAT_FLOAT32
            self.log("已切换到 Float32 高音质模式 (录制: Float32, 播放: Int16)", "SUCCESS")
        else:
            self.recording_format = FORMAT
            self.log("已切换到 Int16 标准模式", "INFO")
        
    def get_output_devices(self):
        """获取所有音频输出设备"""
        devices = []
        try:
            info = self.pa.get_host_api_info_by_index(0)
            num_devices = info.get('deviceCount')
            for i in range(num_devices):
                device_info = self.pa.get_device_info_by_host_api_device_index(0, i)
                if device_info.get('maxOutputChannels') > 0:
                    devices.append({
                        'index': i,
                        'name': device_info.get('name')
                    })
        except Exception as e:
            self.log(f"获取设备列表失败: {e}", "ERROR")
        return devices

    def start_monitor_stream(self, device_index):
        """开启监听流（输出到耳机/扬声器）"""
        self.stop_monitor_stream()
        with self.lock:
            try:
                self.monitor_stream = self.pa.open(
                    format=FORMAT,  # 始终使用 Int16 格式
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    output_device_index=device_index,
                    frames_per_buffer=CHUNK
                )
                self.monitor_device_index = device_index
                self.log(f"成功开启监听流 (设备ID: {device_index})", "INFO")
                return True
            except Exception as e:
                self.log(f"开启监听流失败: {e}", "ERROR")
                return False

    def stop_monitor_stream(self):
        """停止监听流"""
        with self.lock:
            if self.monitor_stream:
                try:
                    self.monitor_stream.stop_stream()
                    self.monitor_stream.close()
                except:
                    pass
                self.monitor_stream = None
                self.log("监听流已关闭", "INFO")

    def start_virtual_mic_stream(self, device_index):
        """开启虚拟麦克风流（输出到 Virtual Cable）"""
        self.stop_virtual_mic_stream()
        with self.lock:
            try:
                self.virtual_mic_stream = self.pa.open(
                    format=FORMAT,  # 始终使用 Int16 格式
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    output_device_index=device_index,
                    frames_per_buffer=CHUNK
                )
                self.virtual_mic_device_index = device_index
                self.log(f"成功开启虚拟麦克风流 (设备ID: {device_index})", "INFO")
                return True
            except Exception as e:
                self.log(f"开启虚拟麦克风流失败: {e}", "ERROR")
                return False

    def stop_virtual_mic_stream(self):
        """停止虚拟麦克风流"""
        with self.lock:
            if self.virtual_mic_stream:
                try:
                    self.virtual_mic_stream.stop_stream()
                    self.virtual_mic_stream.close()
                except:
                    pass
                self.virtual_mic_stream = None
                self.log("虚拟麦克风流已关闭", "INFO")

    def stop_all_streams(self):
        """停止所有输出流"""
        self.stop_monitor_stream()
        self.stop_virtual_mic_stream()

    def write_audio(self, data):
        """
        写入音频数据 - 三条链路并行处理
        
        链路1: 录制到文件 - 保存原始数据（Float32 模式下保存 Float32）
        链路2: 监听流（耳机/扬声器） - 始终输出 Int16
        链路3: 虚拟麦克风流（Virtual Cable） - 始终输出 Int16
        
        原生 Float32 模式：
        - 手机发送 Float32 数据
        - 录制：保存原始 Float32 数据（高音质）
        - 播放：强制转换为 Int16（避免雪花音）
        
        标准 Int16 模式：
        - 手机发送 Int16 数据
        - 录制：Int16
        - 播放：Int16（如果是低采样率，自动重采样到 44100）
        """
        # 确保数据是 bytes 类型
        if not isinstance(data, (bytes, bytearray)):
            try:
                data = bytes(data)
            except Exception:
                return  # 无法转换的数据直接跳过
        
        if len(data) == 0:
            return
        
        # ==================== 链路1: 录制（保存原始数据）====================
        if self.is_recording:
            self.record_frames.append(data)
        
        # ==================== 链路2 & 3: 播放（始终转为 Int16）====================
        playback_data = None  # 初始化为 None，确保转换失败时不输出垃圾
        
        if self.is_float32_mode:
            # ========== Float32 模式：强制转换为 Int16 ==========
            # 原生模式下，手机发送的是 Float32 数据，必须转换为 Int16 播放
            if len(data) % 4 == 0 and len(data) >= 4:
                try:
                    float_array = np.frombuffer(data, dtype=np.float32)
                    # 裁剪到 [-1.0, 1.0] 范围，然后转换为 Int16
                    int16_array = (np.clip(float_array, -1.0, 1.0) * 32767).astype(np.int16)
                    playback_data = int16_array.tobytes()
                except Exception as e:
                    # 转换失败，生成静音数据（避免雪花音）
                    sample_count = len(data) // 4
                    playback_data = np.zeros(sample_count, dtype=np.int16).tobytes()
                    self.log(f"Float32→Int16 转换失败，输出静音: {e}", "WARNING")
            else:
                # 数据长度不符合 Float32 格式，生成静音
                sample_count = max(1, len(data) // 4)
                playback_data = np.zeros(sample_count, dtype=np.int16).tobytes()
        else:
            # ========== Int16 模式 ==========
            playback_data = data
            
            # 检查是否需要重采样（低采样率 → 44100）
            if self.input_sample_rate != self.target_sample_rate:
                try:
                    int16_array = np.frombuffer(data, dtype=np.int16)
                    num_samples = len(int16_array)
                    
                    if num_samples > 0:
                        # 计算重采样后的样本数
                        new_num_samples = int(num_samples * self.target_sample_rate / self.input_sample_rate)
                        
                        # 使用线性插值重采样（避免边界伪影/电流声）
                        x_old = np.linspace(0, 1, num_samples)
                        x_new = np.linspace(0, 1, new_num_samples)
                        resampled = np.interp(x_new, x_old, int16_array)
                        
                        # 防溢出裁剪并转换回 Int16
                        playback_data = np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
                except Exception as e:
                    # 重采样失败，使用原始数据
                    pass
        
        # 确保 playback_data 有效
        if playback_data is None or len(playback_data) == 0:
            return

        # ========== 写入监听流（链路2：耳机/扬声器）==========
        if self.enable_monitor_playback:
            with self.lock:
                if self.monitor_stream:
                    try:
                        self.monitor_stream.write(playback_data)
                    except Exception:
                        pass  # 忽略流中断错误

        # ========== 写入虚拟麦克风流（链路3：Virtual Cable）==========
        if self.enable_virtual_mic_output:
            with self.lock:
                if self.virtual_mic_stream:
                    try:
                        self.virtual_mic_stream.write(playback_data)
                    except Exception:
                        pass  # 忽略流中断错误
        
        # ========== 链路4: 更新实时波形显示 ==========
        if self.waveform_callback and playback_data:
            try:
                self.waveform_callback(playback_data)
            except Exception:
                pass  # 波形更新失败不影响主流程

    def start_recording(self):
        self.record_frames = []
        self.is_recording = True
        # 记录开始录制时的采样率，防止中途切换导致加速/减速
        self.recording_sample_rate = self.input_sample_rate
        self.log(f"开始录制音频 (采样率: {self.recording_sample_rate} Hz)...", "INFO")

    def stop_recording(self):
        self.is_recording = False
        format_name = "Float32 (32-bit)" if self.recording_format == FORMAT_FLOAT32 else "Int16 (16-bit)"
        self.log(f"停止录制，捕获到 {len(self.record_frames)} 个数据块 (格式: {format_name}, 采样率: {self.recording_sample_rate} Hz)", "INFO")
        return self.record_frames, self.recording_format, self.recording_sample_rate

    def save_wav(self, frames, filepath, data_format=None, sample_rate=None):
        """保存音频到WAV文件 - 自动根据录制格式保存"""
        try:
            if len(frames) == 0:
                self.log("没有音频数据可保存", "WARNING")
                return False
            
            # 如果未指定格式，默认使用当前录制格式
            if data_format is None:
                data_format = self.recording_format
            
            # 如果未指定采样率，使用录制时的采样率
            if sample_rate is None:
                sample_rate = self.recording_sample_rate
            
            # 合并所有帧
            audio_data = b''.join(frames)
            
            # 检测录制的数据格式
            if data_format == FORMAT_FLOAT32:
                # Float32 格式：保存为 32-bit Float WAV (高音质)
                # wave 模块不直接支持 float，需要手动写入
                return self._save_float32_wav(audio_data, filepath, sample_rate)
            else:
                # Int16 格式：标准 16-bit WAV
                wf = wave.open(filepath, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.pa.get_sample_size(FORMAT))  # 2 bytes for Int16
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data)
                wf.close()
                self.log(f"音频已保存至: {filepath} (16-bit, {sample_rate}Hz)", "SUCCESS")
                return True
                
        except Exception as e:
            self.log(f"保存WAV失败: {e}", "ERROR")
            return False
    
    def _save_float32_wav(self, audio_data, filepath, sample_rate=None):
        """保存 Float32 数据为 32-bit Float WAV 文件"""
        import struct
        
        try:
            # 如果未指定采样率，使用全局默认
            if sample_rate is None:
                sample_rate = RATE

            # 解析 Float32 数据
            float_array = np.frombuffer(audio_data, dtype=np.float32)
            num_samples = len(float_array)
            
            # WAV 文件参数
            num_channels = CHANNELS
            bits_per_sample = 32
            byte_rate = sample_rate * num_channels * bits_per_sample // 8
            block_align = num_channels * bits_per_sample // 8
            data_size = num_samples * block_align
            
            with open(filepath, 'wb') as f:
                # RIFF 头
                f.write(b'RIFF')
                f.write(struct.pack('<I', 36 + data_size))  # 文件大小 - 8
                f.write(b'WAVE')
                
                # fmt 子块 (IEEE Float 格式)
                f.write(b'fmt ')
                f.write(struct.pack('<I', 16))  # 子块大小
                f.write(struct.pack('<H', 3))   # 音频格式: 3 = IEEE Float
                f.write(struct.pack('<H', num_channels))
                f.write(struct.pack('<I', sample_rate))
                f.write(struct.pack('<I', byte_rate))
                f.write(struct.pack('<H', block_align))
                f.write(struct.pack('<H', bits_per_sample))
                
                # data 子块
                f.write(b'data')
                f.write(struct.pack('<I', data_size))
                f.write(float_array.tobytes())
            
            self.log(f"音频已保存至: {filepath} (32-bit Float 高音质)", "SUCCESS")
            return True
            
        except Exception as e:
            self.log(f"保存 Float32 WAV 失败: {e}", "ERROR")
            # 降级：转换为 Int16 保存
            self.log("尝试降级保存为 16-bit WAV...", "WARNING")
            try:
                float_array = np.frombuffer(audio_data, dtype=np.float32)
                # 转换为 Int16
                int16_array = (np.clip(float_array, -1.0, 1.0) * 32767).astype(np.int16)
                
                wf = wave.open(filepath, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # 2 bytes for Int16
                wf.setframerate(sample_rate)
                wf.writeframes(int16_array.tobytes())
                wf.close()
                self.log(f"音频已降级保存至: {filepath} (16-bit, {sample_rate}Hz)", "SUCCESS")
                return True
            except Exception as e2:
                self.log(f"降级保存也失败: {e2}", "ERROR")
                return False

    def close(self):
        """关闭音频引擎，停止所有输出流"""
        self.stop_all_streams()
        self.pa.terminate()

class MobileMicApp:
    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        
        # 居中显示
        self.center_window()
        
        # 路径设置
        # 资源路径（打包后指向临时解压目录，用于读取静态资源如templates、证书等）
        self.base_path = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
        
        # 工作路径（始终指向exe所在目录或脚本所在目录，用于保存配置、日志、录音）
        if getattr(sys, 'frozen', False):
            # 打包后：使用exe所在目录作为工作目录
            self.config_dir = Path(sys.executable).parent
        else:
            # 开发环境：使用脚本所在目录
            self.config_dir = Path(__file__).parent
        
        self.record_dir = self.config_dir / RECORD_DIR
        self.record_dir.mkdir(exist_ok=True)
        
        self.config_path = self.config_dir / CONFIG_FILE_NAME
        self.log_path = self.config_dir / LOG_FILE_NAME
        self.cert_path = self.config_dir / CERT_FILE_NAME
        self.key_path = self.config_dir / KEY_FILE_NAME
        
        # 加载并设置窗口图标
        self.icon_path = self.load_icon()
        if self.icon_path:
            try:
                self.root.iconbitmap(self.icon_path)
            except Exception as e:
                print(f"设置窗口图标失败: {e}")
        
        # 初始化组件
        self.audio_engine = AudioEngine(self.log_message)
        self.audio_player = AudioPlayer(self.log_message)
        
        # 波形回调将在UI创建后绑定
        self.realtime_waveform = None
        
        self.flask_app = Flask(__name__)
        
        # Flask-SocketIO 初始化：不指定 async_mode，让其自动检测可用模式
        # 这样可以兼容开发环境和打包环境
        try:
            self.socketio = SocketIO(self.flask_app, cors_allowed_origins="*", async_mode='eventlet')
        except ValueError:
            # 如果 eventlet 不可用，使用默认模式（threading）
            self.log_message("eventlet 模式不可用，使用 threading 模式", "WARNING")
            self.socketio = SocketIO(self.flask_app, cors_allowed_origins="*", async_mode='threading')
        
        self.server_thread = None
        self.is_server_running = False
        self.is_recording = False
        self.connected_clients = 0  # 连接的客户端数量
        self.mic_active_clients = set()  # 追踪开启麦克风的客户端（存储session id）
        self.recorded_files = []
        self.play_update_job = None
        self.server_sock = None # 存储监听Socket以便彻底关闭
        self.broadcast_queue = queue.Queue() # 线程安全的消息队列，用于UI线程向SocketIO线程传递消息
        
        # 加载配置
        self.config = self.load_config()
        
        # 应用主题
        self.style = ttk.Style(theme=self.config.get("theme", DEFAULT_THEME))
        
        # 初始化UI
        self.setup_ui()
        
        # 注册 Flask 路由和 Socket 事件
        self.setup_flask_routes()
        
        # 自动加载音频设备列表
        self.refresh_devices()
        
        # 加载已有录音文件
        self.load_existing_records()
        
        # 绑定快捷键
        self.root.bind("<space>", self.handle_keypress)
        self.root.bind("<Left>", self.handle_keypress)
        self.root.bind("<Right>", self.handle_keypress)
        
        # 设置关闭协议
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.log_message("程序初始化完成 (空格: 播放/暂停, 左右键: 快进/快退)", "INFO")

    def load_icon(self):
        """
        多策略加载应用图标
        策略1: 优先加载icon.ico（打包后在_MEIPASS，开发时在脚本目录）
        策略2: 如果找不到ico，尝试转换icon.png为ico
        策略3: 动态创建默认图标
        返回: 图标文件路径（字符串）或None
        """
        # 策略1: 尝试从打包资源目录加载icon.ico
        ico_in_bundle = self.base_path / "icon.ico"
        if ico_in_bundle.exists():
            print(f"[图标] 找到打包资源图标: {ico_in_bundle}")
            return str(ico_in_bundle)
        
        # 策略2: 尝试从脚本目录加载icon.ico（开发环境）
        ico_in_script_dir = self.config_dir / "icon.ico"
        if ico_in_script_dir.exists():
            print(f"[图标] 找到脚本目录图标: {ico_in_script_dir}")
            return str(ico_in_script_dir)
        
        # 策略3: 尝试从icon.png转换（开发环境）
        png_path = self.config_dir / "icon.png"
        if png_path.exists():
            try:
                print(f"[图标] 尝试将 {png_path} 转换为 ICO 格式...")
                temp_ico = self.config_dir / "temp_icon.ico"
                img = Image.open(png_path)
                img.save(temp_ico, format='ICO', sizes=[(256, 256)])
                print(f"[图标] 转换成功: {temp_ico}")
                return str(temp_ico)
            except Exception as e:
                print(f"[图标] PNG转ICO失败: {e}")
        
        # 策略4: 动态创建默认图标
        try:
            print("[图标] 动态创建默认图标...")
            default_ico = self.config_dir / "default_icon.ico"
            
            # 创建一个简单的彩色图标 (蓝色背景 + 白色 M 字母)
            img = Image.new('RGB', (256, 256), color='#3498db')
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)
            
            # 绘制白色 "M" 字母
            try:
                # 尝试使用系统字体
                font = ImageFont.truetype("arial.ttf", 180)
            except:
                # 如果找不到字体，使用默认字体
                font = ImageFont.load_default()
            
            # 计算文本位置使其居中
            text = "M"
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (256 - text_width) / 2
            y = (256 - text_height) / 2 - 20  # 稍微上移
            
            draw.text((x, y), text, fill='white', font=font)
            
            img.save(default_ico, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
            print(f"[图标] 默认图标创建成功: {default_ico}")
            return str(default_ico)
        except Exception as e:
            print(f"[图标] 创建默认图标失败: {e}")
        
        print("[图标] 所有图标加载策略均失败，窗口将使用系统默认图标")
        return None

    def center_window(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'+{x}+{y}')

    def load_config(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 确保端口在有效范围内
                    if 'port' in config:
                        config['port'] = max(MIN_PORT, min(MAX_PORT, config['port']))
                    return config
            except Exception:
                pass
        return {
            "theme": DEFAULT_THEME, 
            "last_device": None, 
            "port": DEFAULT_PORT,
            "enable_realtime_playback": ENABLE_REALTIME_PLAYBACK,
            "delete_to_trash": True
        }

    def save_config(self):
        self.config["theme"] = self.style.theme_use()
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.log_message(f"保存配置失败: {e}", "ERROR")

    def toggle_theme(self):
        new_theme = THEME_LIGHT if self.style.theme_use() == THEME_DARK else THEME_DARK
        self.style.theme_use(new_theme)
        self.save_config()
        self.log_message(f"已切换主题为: {new_theme}", "INFO")

    def open_virtual_mic_driver_website(self):
        """打开虚拟麦克风驱动下载页面（自动识别系统）"""
        import webbrowser
        import platform
        
        # 检测操作系统
        system = platform.system()
        
        if system == "Windows":
            # Windows 系统 -> VB-CABLE
            url = "https://vb-audio.com/Cable/"
            driver_name = "VB-CABLE Virtual Audio Device"
        elif system == "Darwin":
            # macOS 系统 -> BlackHole
            url = "https://existential.audio/blackhole/"
            driver_name = "BlackHole"
        else:
            # Linux 或其他系统
            url = "https://vb-audio.com/Cable/"
            driver_name = "VB-CABLE Virtual Audio Device"
        
        try:
            webbrowser.open(url)
            self.log_message(f"正在打开 {driver_name} 下载页面...", "INFO")
            self.log_message(f"系统: {system} | 链接: {url}", "INFO")
        except Exception as e:
            self.log_message(f"打开浏览器失败: {e}", "ERROR")
            # 降级方案：复制到剪贴板
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(url)
                messagebox.showinfo(
                    "提示", 
                    f"无法自动打开浏览器，链接已复制到剪贴板：\n\n{url}\n\n推荐驱动：{driver_name}\n\n请手动粘贴到浏览器访问"
                )
                self.log_message("链接已复制到剪贴板", "WARNING")
            except:
                messagebox.showerror("错误", f"请手动访问：\n{url}\n\n推荐驱动：{driver_name}")

    def setup_ui(self):
        # 顶部工具栏
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=X, side=TOP)
        
        ttk.Button(toolbar, text="📥 安装驱动", command=self.open_virtual_mic_driver_website, width=18, bootstyle=PRIMARY).pack(side=LEFT, padx=5)
        ttk.Button(toolbar, text="切换主题", command=self.toggle_theme, width=BUTTON_WIDTH, bootstyle=OUTLINE).pack(side=LEFT, padx=5)
        ttk.Button(toolbar, text="刷新设备", command=self.refresh_devices, width=BUTTON_WIDTH, bootstyle=OUTLINE).pack(side=LEFT, padx=5)
        
        self.status_indicator = ttk.Label(toolbar, text="● 服务未运行", foreground="gray")
        self.status_indicator.pack(side=RIGHT, padx=10)

        # 主内容区域
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=BOTH, expand=YES)
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)

        # 左上：连接与控制
        left_panel = ttk.LabelFrame(main_frame, text="连接控制", padding=10)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=(0, 5))
        
        # ==================== 双设备选择 ====================
        # 监听设备选择（耳机/扬声器）
        monitor_frame = ttk.Frame(left_panel)
        monitor_frame.pack(fill=X, pady=(0, 5))
        
        ttk.Label(monitor_frame, text="🎧 监听设备 (耳机/扬声器):").pack(side=LEFT, padx=(0, 5))
        self.monitor_playback_var = tk.BooleanVar(value=self.config.get("enable_monitor", True))
        self.monitor_cb = ttk.Checkbutton(
            monitor_frame,
            text="启用",
            variable=self.monitor_playback_var,
            command=self.on_monitor_enabled_changed,
            bootstyle="round-toggle-success"
        )
        self.monitor_cb.pack(side=RIGHT)
        
        self.monitor_combo = ttk.Combobox(left_panel, state="readonly")
        self.monitor_combo.pack(fill=X, pady=(0, 10))
        self.monitor_combo.bind("<<ComboboxSelected>>", self.on_monitor_device_selected)
        
        # 虚拟麦克风设备选择（Virtual Cable）
        virtual_mic_frame = ttk.Frame(left_panel)
        virtual_mic_frame.pack(fill=X, pady=(0, 5))
        
        ttk.Label(virtual_mic_frame, text="🎤 虚拟麦克风 (Virtual Cable):").pack(side=LEFT, padx=(0, 5))
        self.virtual_mic_var = tk.BooleanVar(value=self.config.get("enable_virtual_mic", True))
        self.virtual_mic_cb = ttk.Checkbutton(
            virtual_mic_frame,
            text="启用",
            variable=self.virtual_mic_var,
            command=self.on_virtual_mic_enabled_changed,
            bootstyle="round-toggle-success"
        )
        self.virtual_mic_cb.pack(side=RIGHT)
        
        self.virtual_mic_combo = ttk.Combobox(left_panel, state="readonly")
        self.virtual_mic_combo.pack(fill=X, pady=(0, 10))
        self.virtual_mic_combo.bind("<<ComboboxSelected>>", self.on_virtual_mic_device_selected)
        
        # 同步到音频引擎
        self.audio_engine.enable_monitor_playback = self.monitor_playback_var.get()
        self.audio_engine.enable_virtual_mic_output = self.virtual_mic_var.get()
        # ==================== 双设备选择结束 ====================

        # 端口设置和实时波形并排显示
        port_waveform_container = ttk.Frame(left_panel)
        port_waveform_container.pack(fill=BOTH, expand=YES, pady=(0, 10))
        
        # 左侧：端口和服务控制
        port_control_frame = ttk.Frame(port_waveform_container)
        port_control_frame.pack(side=LEFT, fill=Y, padx=(0, 10))
        
        # 端口设置
        port_frame = ttk.Frame(port_control_frame)
        port_frame.pack(fill=X, pady=(0, 5))
        ttk.Label(port_frame, text="服务端口:").pack(side=LEFT, padx=(0, 5))
        self.port_var = tk.StringVar(value=str(self.config.get("port", DEFAULT_PORT)))
        self.port_entry = ttk.Entry(port_frame, textvariable=self.port_var, width=10)
        self.port_entry.pack(side=LEFT, padx=(0, 5))
        ttk.Label(port_frame, text=f"({MIN_PORT}-{MAX_PORT})", font=("", 8)).pack(side=LEFT)
        
        # 服务控制
        btn_frame = ttk.Frame(port_control_frame)
        btn_frame.pack(fill=X, pady=0)
        self.start_btn = ttk.Button(btn_frame, text="开启服务", command=self.toggle_server, bootstyle=SUCCESS, width=15)
        self.start_btn.pack(side=LEFT, padx=5)
        
        # 音频电平表
        level_frame = ttk.LabelFrame(port_control_frame, text="音频电平", padding=5)
        level_frame.pack(fill=X, pady=(10, 0))
        self.audio_level_meter = AudioLevelMeter(level_frame, width=180, height=25)
        self.audio_level_meter.get_widget().pack(fill=X)
        
        # 右侧：实时波形显示
        waveform_container = ttk.LabelFrame(port_waveform_container, text="实时音频波形", padding=5)
        waveform_container.pack(side=LEFT, fill=BOTH, expand=YES)
        
        # 波形控制勾选框和时长选择
        waveform_ctrl_frame = ttk.Frame(waveform_container)
        waveform_ctrl_frame.pack(fill=X, pady=(0, 2))
        self.realtime_waveform_var = tk.BooleanVar(value=self.config.get("enable_realtime_waveform", True))
        ttk.Checkbutton(
            waveform_ctrl_frame,
            text="启用",
            variable=self.realtime_waveform_var,
            command=self.on_realtime_waveform_toggle,
            bootstyle="round-toggle-success"
        ).pack(side=LEFT, padx=(0, 5))
        
        # 历史时长下拉菜单
        ttk.Label(waveform_ctrl_frame, text="历史:", font=("", 8)).pack(side=LEFT, padx=(5, 2))
        self.waveform_duration_var = tk.StringVar(value=str(self.config.get("waveform_duration", 10)))
        duration_combo = ttk.Combobox(
            waveform_ctrl_frame,
            textvariable=self.waveform_duration_var,
            values=["5", "10", "15", "30"],
            state="readonly",
            width=5
        )
        duration_combo.pack(side=LEFT, padx=(0, 2))
        duration_combo.bind("<<ComboboxSelected>>", self.on_waveform_duration_changed)
        ttk.Label(waveform_ctrl_frame, text="秒", font=("", 8)).pack(side=LEFT)
        
        # 实时波形组件（使用配置的历史时长初始化）
        initial_duration = self.config.get("waveform_duration", 10)
        self.realtime_waveform = RealtimeWaveformVisualizer(waveform_container, self.log_message, initial_duration)
        realtime_wf_widget = self.realtime_waveform.get_widget()
        realtime_wf_widget.pack(fill=BOTH, expand=YES)
        
        # 绑定音频引擎回调
        self.audio_engine.waveform_callback = self.update_realtime_waveform
        
        # 连接信息显示区 (左右布局)
        conn_info_frame = ttk.Frame(left_panel)
        conn_info_frame.pack(fill=BOTH, expand=YES, pady=10)

        # 左侧：二维码容器 (固定大小)
        qr_container = ttk.Frame(conn_info_frame, width=220, height=220)
        qr_container.pack_propagate(False) # 禁止子组件撑开容器
        qr_container.pack(side=LEFT, padx=(0, 10))
        
        self.qr_label = ttk.Label(qr_container, text="服务开启后\n显示连接二维码", 
                                relief=SUNKEN, anchor=CENTER, justify=CENTER)
        self.qr_label.pack(fill=BOTH, expand=YES)

        # 右侧：地址列表容器
        ip_list_container = ttk.Frame(conn_info_frame)
        ip_list_container.pack(side=LEFT, fill=BOTH, expand=YES)

        self.url_var = tk.StringVar(value="等待服务启动...")
        ttk.Entry(ip_list_container, textvariable=self.url_var, state="readonly").pack(fill=X, pady=(0, 5))

        ttk.Label(ip_list_container, text="可用连接地址 (双击复制):").pack(anchor=W)
        self.ip_tree = ttk.Treeview(ip_list_container, columns=("url"), show="headings", height=5)
        self.ip_tree.heading("url", text="连接地址")
        self.ip_tree.pack(fill=BOTH, expand=YES, pady=(2, 0))
        self.ip_tree.bind("<Double-1>", self.on_ip_double_click)
        self.ip_tree.bind("<<TreeviewSelect>>", self.on_ip_selection_changed)

        # 右上：录制管理
        right_panel = ttk.LabelFrame(main_frame, text="录制管理", padding=10)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=(0, 5))
        
        rec_ctrl_frame = ttk.Frame(right_panel)
        rec_ctrl_frame.pack(fill=X, pady=(0, 10))
        
        self.rec_btn = ttk.Button(rec_ctrl_frame, text="开始录制", command=self.toggle_recording, bootstyle=DANGER, width=15, state=DISABLED)
        self.rec_btn.pack(side=LEFT, padx=5)
        
        self.rec_time_label = ttk.Label(rec_ctrl_frame, text="00:00", font=("Consolas", 12))
        self.rec_time_label.pack(side=LEFT, padx=10)

        # 文件列表
        ttk.Label(right_panel, text="录音记录 (双击播放):").pack(anchor=W)
        self.file_list = ttk.Treeview(right_panel, columns=("name", "time"), show="headings", height=8)
        self.file_list.heading("name", text="文件名")
        self.file_list.heading("time", text="录制时间")
        self.file_list.column("name", width=150)
        self.file_list.column("time", width=120)
        self.file_list.pack(fill=BOTH, expand=YES, pady=5)
        self.file_list.bind("<Double-1>", self.on_file_double_click)
        self.file_list.bind("<<TreeviewSelect>>", self.on_file_select)
        
        file_btn_frame = ttk.Frame(right_panel)
        file_btn_frame.pack(fill=X)
        ttk.Button(file_btn_frame, text="打开目录", command=self.open_record_dir, width=12).pack(side=LEFT, padx=5)
        ttk.Button(file_btn_frame, text="另存为...", command=self.save_as_file, width=12).pack(side=LEFT, padx=5)
        
        # 删除到回收站勾选框
        self.delete_to_trash_var = tk.BooleanVar(value=self.config.get("delete_to_trash", True))
        self.delete_to_trash_cb = ttk.Checkbutton(
            file_btn_frame, 
            text="远程删除到回收站", 
            variable=self.delete_to_trash_var,
            command=self.on_delete_to_trash_changed,
            bootstyle="round-toggle"
        )
        self.delete_to_trash_cb.pack(side=RIGHT, padx=5)

        # 底部：播放器面板
        player_panel = ttk.LabelFrame(main_frame, text="音频播放器", padding=10)
        player_panel.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(5, 0))
        player_panel.columnconfigure(0, weight=1)
        player_panel.rowconfigure(0, weight=1)
        
        # 波形显示区（传入点击回调函数）
        self.waveform_viz = WaveformVisualizer(player_panel, self.log_message, self.on_waveform_click)
        waveform_widget = self.waveform_viz.get_widget()
        waveform_widget.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        
        # 播放控制区
        control_frame = ttk.Frame(player_panel)
        control_frame.grid(row=1, column=0, sticky="ew")
        control_frame.columnconfigure(1, weight=1)
        
        # 播放按钮
        btn_control = ttk.Frame(control_frame)
        btn_control.grid(row=0, column=0, padx=(0, 10))
        
        self.play_btn = ttk.Button(btn_control, text="▶ 播放", command=self.play_audio, width=10, bootstyle=SUCCESS)
        self.play_btn.pack(side=LEFT, padx=2)
        
        self.pause_btn = ttk.Button(btn_control, text="⏸ 暂停", command=self.pause_audio, width=10, bootstyle=WARNING, state=DISABLED)
        self.pause_btn.pack(side=LEFT, padx=2)
        
        self.stop_btn = ttk.Button(btn_control, text="⏹ 停止", command=self.stop_audio, width=10, bootstyle=DANGER, state=DISABLED)
        self.stop_btn.pack(side=LEFT, padx=2)
        
        # 进度条
        progress_frame = ttk.Frame(control_frame)
        progress_frame.grid(row=0, column=1, sticky="ew", padx=10)
        
        self.time_label = ttk.Label(progress_frame, text="00:00", font=("Consolas", 10))
        self.time_label.pack(side=LEFT, padx=5)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_scale = ttk.Scale(progress_frame, from_=0, to=100, variable=self.progress_var, 
                                        orient=HORIZONTAL, command=self.on_progress_change)
        self.progress_scale.pack(side=LEFT, fill=X, expand=YES, padx=5)
        
        self.duration_label = ttk.Label(progress_frame, text="00:00", font=("Consolas", 10))
        self.duration_label.pack(side=LEFT, padx=5)
        
        # 当前文件名
        self.current_file_label = ttk.Label(control_frame, text="未加载文件", font=("", 9), foreground="gray")
        self.current_file_label.grid(row=0, column=2, padx=10)

        # 最底部日志框
        log_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        log_frame.pack(fill=X, side=BOTTOM)
        self.log_text = ttk.ScrolledText(log_frame, height=LOG_DISPLAY_HEIGHT, state=DISABLED, font=("Consolas", 9))
        self.log_text.pack(fill=X)

    def log_message(self, message, level="INFO"):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        full_message = f"{timestamp} [{level}] {message}\n"
        
        def _update():
            self.log_text.config(state=NORMAL)
            self.log_text.insert(END, full_message)
            
            # 颜色标识
            last_index = self.log_text.index("end-1c linestart")
            if level == "ERROR":
                self.log_text.tag_add("error", last_index, f"{last_index} lineend")
                self.log_text.tag_config("error", foreground="red")
            elif level == "SUCCESS":
                self.log_text.tag_add("success", last_index, f"{last_index} lineend")
                self.log_text.tag_config("success", foreground="#28a745")
            elif level == "WARNING":
                self.log_text.tag_add("warning", last_index, f"{last_index} lineend")
                self.log_text.tag_config("warning", foreground="yellow")
            
            self.log_text.see(END)
            self.log_text.config(state=DISABLED)
            
            if ENABLE_LOG_FILE:
                try:
                    with open(self.log_path, "a", encoding="utf-8") as f:
                        f.write(full_message)
                except:
                    pass
        
        self.root.after(0, _update)

    def refresh_devices(self):
        """刷新音频设备列表，填充双设备选择下拉框"""
        devices = self.audio_engine.get_output_devices()
        self.device_list = devices
        device_names = [f"{d['index']}: {d['name']}" for d in devices]
        
        # 填充监听设备下拉框
        self.monitor_combo['values'] = device_names
        # 填充虚拟麦克风设备下拉框
        self.virtual_mic_combo['values'] = device_names
        
        # 恢复监听设备选择
        last_monitor = self.config.get("monitor_device")
        if last_monitor and last_monitor in device_names:
            self.monitor_combo.set(last_monitor)
            self.on_monitor_device_selected(None)
        elif device_names:
            self.monitor_combo.current(0)
            self.on_monitor_device_selected(None)
        
        # 恢复虚拟麦克风设备选择
        last_virtual_mic = self.config.get("virtual_mic_device")
        if last_virtual_mic and last_virtual_mic in device_names:
            self.virtual_mic_combo.set(last_virtual_mic)
            self.on_virtual_mic_device_selected(None)
        elif device_names:
            # 尝试自动选择包含 "Cable" 或 "Virtual" 的设备
            cable_idx = None
            for i, name in enumerate(device_names):
                if "cable" in name.lower() or "virtual" in name.lower():
                    cable_idx = i
                    break
            if cable_idx is not None:
                self.virtual_mic_combo.current(cable_idx)
            else:
                self.virtual_mic_combo.current(0)
            self.on_virtual_mic_device_selected(None)
            
        self.log_message(f"已刷新音频输出设备，发现 {len(devices)} 个可用设备", "INFO")

    def on_monitor_device_selected(self, event):
        """监听设备选择回调"""
        selection = self.monitor_combo.get()
        if selection:
            idx = int(selection.split(":")[0])
            self.audio_engine.start_monitor_stream(idx)
            self.config["monitor_device"] = selection
            self.save_config()

    def on_virtual_mic_device_selected(self, event):
        """虚拟麦克风设备选择回调"""
        selection = self.virtual_mic_combo.get()
        if selection:
            idx = int(selection.split(":")[0])
            self.audio_engine.start_virtual_mic_stream(idx)
            self.config["virtual_mic_device"] = selection
            self.save_config()

    def on_monitor_enabled_changed(self):
        """监听设备启用/禁用回调"""
        enabled = self.monitor_playback_var.get()
        self.audio_engine.enable_monitor_playback = enabled
        self.config["enable_monitor"] = enabled
        self.save_config()
        status = "已启用" if enabled else "已禁用"
        self.log_message(f"监听设备{status}", "INFO")

    def on_virtual_mic_enabled_changed(self):
        """虚拟麦克风启用/禁用回调"""
        enabled = self.virtual_mic_var.get()
        self.audio_engine.enable_virtual_mic_output = enabled
        self.config["enable_virtual_mic"] = enabled
        self.save_config()
        status = "已启用" if enabled else "已禁用"
        self.log_message(f"虚拟麦克风{status}", "INFO")

    def get_local_ip(self):
        """获取主IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def get_all_local_ips(self):
        """获取所有可用的内网IP地址"""
        ips = set()
        # 方法1: 通过socket.gethostbyname_ex获取
        try:
            hostname = socket.gethostname()
            _, _, ip_list = socket.gethostbyname_ex(hostname)
            for ip in ip_list:
                if not ip.startswith("127.") and ":" not in ip: # 排除回环和部分IPv6
                    ips.add(ip)
        except:
            pass
        
        # 方法2: 尝试连接获取主IP (最可靠)
        main_ip = self.get_local_ip()
        if main_ip != "127.0.0.1":
            ips.add(main_ip)
            
        return sorted(list(ips))

    def generate_cert(self):
        """使用 cryptography 库生成现代化自签名证书 (含 SAN，解决 macOS 兼容性问题)"""
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            import ipaddress
            import datetime as dt_module # 避免与 from datetime import datetime 冲突
            
            self.log_message("正在生成自签名证书 (cryptography)...", "INFO")
            
            # 生成私钥
            key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
            
            # 构建 SAN 列表
            local_ip = self.get_local_ip()
            alt_names = []
            
            # 添加当前IP
            try:
                alt_names.append(x509.IPAddress(ipaddress.ip_address(local_ip)))
            except:
                pass
                
            # 尝试添加所有本地IP
            try:
                all_ips = self.get_all_local_ips()
                for ip in all_ips:
                    try:
                        ip_obj = ipaddress.ip_address(ip)
                        # 避免重复 (简单的 list check)
                        if ip_obj not in [x.value for x in alt_names if isinstance(x, x509.IPAddress)]:
                            alt_names.append(x509.IPAddress(ip_obj))
                    except:
                        pass
            except:
                pass
            
            # 添加 localhost
            alt_names.append(x509.DNSName("localhost"))
            
            # 构建证书主题
            subject = x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
                x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "State"),
                x509.NameAttribute(NameOID.LOCALITY_NAME, "City"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MobileMic"),
                x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
            ])
            
            # 构建证书
            # 有效期 10 年
            now = dt_module.datetime.utcnow()
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + dt_module.timedelta(days=3650))
                .add_extension(
                    x509.SubjectAlternativeName(alt_names),
                    critical=False,
                )
                .sign(key, hashes.SHA256())
            )
            
            # 保存证书
            with open(self.cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
                
            # 保存私钥
            with open(self.key_path, "wb") as f:
                f.write(key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                ))
                
            self.log_message("证书生成成功", "SUCCESS")
            
        except Exception as e:
            self.log_message(f"证书生成失败: {e}", "ERROR")
            messagebox.showerror("证书错误", f"无法生成HTTPS证书:\n{e}\n\n请尝试重新安装: pip install cryptography")

    def setup_flask_routes(self):
        @self.flask_app.route('/')
        def index():
            return render_template('index.html')

        # ==================== 音频文件管理API ====================
        @self.flask_app.route('/api/audio/list')
        def get_audio_list():
            """获取音频文件列表"""
            try:
                audio_files = []
                wav_files = sorted(self.record_dir.glob("*.wav"), key=lambda x: x.stat().st_mtime, reverse=True)
                for wav_file in wav_files:
                    filename = wav_file.name
                    file_stat = wav_file.stat()
                    file_size = file_stat.st_size
                    mtime = datetime.fromtimestamp(file_stat.st_mtime)
                    
                    # 获取音频时长
                    duration = 0
                    try:
                        with wave.open(str(wav_file), 'rb') as wf:
                            frames = wf.getnframes()
                            rate = wf.getframerate()
                            duration = frames / rate if rate > 0 else 0
                    except Exception as e:
                        # wave模块失败，尝试手动解析Float32 WAV
                        try:
                            import struct
                            with open(str(wav_file), 'rb') as f:
                                # 读取RIFF头
                                riff = f.read(4)
                                if riff == b'RIFF':
                                    f.read(4)  # 文件大小
                                    wave_id = f.read(4)
                                    if wave_id == b'WAVE':
                                        sample_rate = 44100
                                        total_samples = 0
                                        
                                        while True:
                                            chunk_id = f.read(4)
                                            if len(chunk_id) < 4:
                                                break
                                            chunk_size = struct.unpack('<I', f.read(4))[0]
                                            
                                            if chunk_id == b'fmt ':
                                                # fmt块结构: audio_format(2) + channels(2) + sample_rate(4) + byte_rate(4) + block_align(2) + bits(2) = 16字节
                                                f.read(2)  # audio_format
                                                f.read(2)  # num_channels
                                                sample_rate = struct.unpack('<I', f.read(4))[0]
                                                f.read(4)  # byte_rate
                                                f.read(2)  # block_align
                                                f.read(2)  # bits_per_sample
                                                # 跳过剩余字节(如果有扩展)
                                                remaining = chunk_size - 16
                                                if remaining > 0:
                                                    f.read(remaining)
                                            elif chunk_id == b'data':
                                                # 32-bit Float = 4 bytes per sample
                                                total_samples = chunk_size // 4
                                                break
                                            else:
                                                f.read(chunk_size)
                                        
                                        if sample_rate > 0 and total_samples > 0:
                                            duration = total_samples / sample_rate
                        except Exception as parse_err:
                            pass  # 解析失败，保持duration=0
                    
                    audio_files.append({
                        'filename': filename,
                        'size': file_size,
                        'size_str': self._format_file_size(file_size),
                        'mtime': mtime.strftime("%Y-%m-%d %H:%M:%S"),
                        'duration': duration,
                        'duration_str': self._format_duration(duration)
                    })
                
                return jsonify({'success': True, 'files': audio_files})
            except Exception as e:
                self.log_message(f"获取音频列表失败: {e}", "ERROR")
                return jsonify({'success': False, 'error': str(e)}), 500

        @self.flask_app.route('/api/audio/play/<filename>')
        def play_audio(filename):
            """播放指定音频文件（返回文件流）"""
            try:
                # 安全检查：防止路径遍历攻击
                if '..' in filename or '/' in filename or '\\' in filename:
                    abort(400, description="Invalid filename")
                
                filepath = self.record_dir / filename
                if not filepath.exists():
                    abort(404, description="File not found")
                
                self.log_message(f"手机端播放音频: {filename}", "INFO")
                return send_file(
                    str(filepath),
                    mimetype='audio/wav',
                    as_attachment=False,
                    download_name=filename
                )
            except Exception as e:
                self.log_message(f"播放音频失败: {e}", "ERROR")
                abort(500, description=str(e))

        @self.flask_app.route('/api/audio/download/<filename>')
        def download_audio(filename):
            """下载指定音频文件"""
            try:
                # 安全检查：防止路径遍历攻击
                if '..' in filename or '/' in filename or '\\' in filename:
                    abort(400, description="Invalid filename")
                
                filepath = self.record_dir / filename
                if not filepath.exists():
                    abort(404, description="File not found")
                
                self.log_message(f"手机端下载音频: {filename}", "INFO")
                return send_file(
                    str(filepath),
                    mimetype='audio/wav',
                    as_attachment=True,
                    download_name=filename
                )
            except Exception as e:
                self.log_message(f"下载音频失败: {e}", "ERROR")
                abort(500, description=str(e))

        @self.flask_app.route('/api/audio/delete/<filename>', methods=['DELETE'])
        def delete_audio(filename):
            """删除指定音频文件"""
            try:
                # 安全检查：防止路径遍历攻击
                if '..' in filename or '/' in filename or '\\' in filename:
                    return jsonify({'success': False, 'error': 'Invalid filename'}), 400
                
                filepath = self.record_dir / filename
                if not filepath.exists():
                    return jsonify({'success': False, 'error': 'File not found'}), 404
                
                # 根据GUI设置决定删除方式
                delete_to_trash = self.config.get("delete_to_trash", True)
                
                if delete_to_trash:
                    # 删除到回收站
                    send2trash(str(filepath))
                    self.log_message(f"手机端删除音频到回收站: {filename}", "WARNING")
                else:
                    # 永久删除
                    filepath.unlink()
                    self.log_message(f"手机端永久删除音频: {filename}", "WARNING")
                
                # 同步更新桌面端文件列表
                self.root.after(0, self._refresh_file_list)
                
                return jsonify({'success': True, 'message': f'已删除 {filename}'})
            except Exception as e:
                self.log_message(f"删除音频失败: {e}", "ERROR")
                return jsonify({'success': False, 'error': str(e)}), 500

        @self.flask_app.route('/api/audio/info/<filename>')
        def get_audio_info(filename):
            """获取音频文件详细信息"""
            try:
                # 安全检查
                if '..' in filename or '/' in filename or '\\' in filename:
                    return jsonify({'success': False, 'error': 'Invalid filename'}), 400
                
                filepath = self.record_dir / filename
                if not filepath.exists():
                    return jsonify({'success': False, 'error': 'File not found'}), 404
                
                file_stat = filepath.stat()
                
                # 获取音频详细信息
                with wave.open(str(filepath), 'rb') as wf:
                    channels = wf.getnchannels()
                    sample_width = wf.getsampwidth()
                    frame_rate = wf.getframerate()
                    n_frames = wf.getnframes()
                    duration = n_frames / frame_rate if frame_rate > 0 else 0
                
                info = {
                    'filename': filename,
                    'size': file_stat.st_size,
                    'size_str': self._format_file_size(file_stat.st_size),
                    'mtime': datetime.fromtimestamp(file_stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    'duration': duration,
                    'duration_str': self._format_duration(duration),
                    'channels': channels,
                    'sample_rate': frame_rate,
                    'bit_depth': sample_width * 8
                }
                
                return jsonify({'success': True, 'info': info})
            except Exception as e:
                self.log_message(f"获取音频信息失败: {e}", "ERROR")
                return jsonify({'success': False, 'error': str(e)}), 500
        # ==================== 音频文件管理API结束 ====================

        @self.socketio.on('audio_data')
        def handle_audio(data):
            self.audio_engine.write_audio(data)

        @self.socketio.on('connect')
        def handle_connect():
            self.connected_clients += 1
            self.log_message(f"手机已连接: {request.remote_addr} (当前连接: {self.connected_clients})", "SUCCESS")
            # 向新连接的客户端发送当前录制状态
            emit('recording_status', {'is_recording': self.is_recording})
            # ✅ 广播当前原生模式状态，强制手机端同步
            emit('native_mode_status', {'enabled': self.audio_engine.is_float32_mode})
            # 如果实时波形已启用，自动启动显示
            if self.connected_clients == 1 and self.realtime_waveform_var.get():
                self.root.after(0, lambda: self.realtime_waveform.start())

        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.connected_clients = max(0, self.connected_clients - 1)
            # 从麦克风活跃列表移除断开的客户端
            if request.sid in self.mic_active_clients:
                self.mic_active_clients.discard(request.sid)
                self.log_message(f"客户端 {request.sid} 麦克风已标记为关闭（断线）", "WARNING")
                # 更新录制按钮状态
                self.root.after(0, self.update_rec_button_state)
            
            self.log_message(f"手机已断开: {request.remote_addr} (当前连接: {self.connected_clients})", "WARNING")
            # 如果没有客户端连接了，停止实时波形显示
            if self.connected_clients == 0:
                self.root.after(0, lambda: self.realtime_waveform.stop())

        @self.socketio.on('toggle_recording')
        def handle_toggle_recording():
            """处理手机端的录制控制请求"""
            self.log_message(f"收到手机端录制控制请求", "INFO")
            # 先计算切换后的目标状态
            target_status = not self.is_recording
            # 在主线程中执行UI操作
            self.root.after(0, self._remote_toggle_recording)
            # 短暂延迟后广播目标状态（确保UI操作已开始）
            # 使用标准的 time.sleep 代替 eventlet.sleep，兼容所有模式
            time.sleep(0.2)
            # 直接在SocketIO上下文中广播实际状态
            self.socketio.emit('recording_status', {'is_recording': self.is_recording})
            self.log_message(f"直接广播录制状态: {'录制中' if self.is_recording else '未录制'}", "DEBUG")

        @self.socketio.on('request_recording_status')
        def handle_request_status():
            """处理手机端请求录制状态"""
            emit('recording_status', {'is_recording': self.is_recording})

        @self.socketio.on('set_native_mode')
        def handle_set_native_mode(data):
            """处理手机端的原生模式切换请求"""
            enabled = data.get('enabled', False)
            # ✅ 立即同步切换，消除时序竞态，避免雪花噪音
            self.audio_engine.set_float32_mode(enabled)
            self.log_message(f"手机端切换原生模式: {'开启' if enabled else '关闭'}", "INFO")

        @self.socketio.on('update_config')
        def handle_update_config(data):
            """处理手机端的配置更新 (如采样率)"""
            sample_rate = data.get('sampleRate')
            if sample_rate:
                # 在主线程中更新
                self.root.after(0, lambda: self.audio_engine.set_input_sample_rate(int(sample_rate)))

        @self.socketio.on('mic_status')
        def handle_mic_status(data):
            """处理手机端麦克风状态变化"""
            is_open = data.get('is_open', False)
            client_sid = request.sid
            
            if is_open:
                # 麦克风开启
                self.mic_active_clients.add(client_sid)
                self.log_message(f"客户端 {client_sid} 麦克风已开启 (活跃麦克风: {len(self.mic_active_clients)})", "SUCCESS")
            else:
                # 麦克风关闭
                self.mic_active_clients.discard(client_sid)
                self.log_message(f"客户端 {client_sid} 麦克风已关闭 (活跃麦克风: {len(self.mic_active_clients)})", "WARNING")
            
            # 在主线程更新UI
            self.root.after(0, self.update_rec_button_state)

    def toggle_server(self):
        if not self.is_server_running:
            self.start_server()
        else:
            self.stop_server()

    def start_server(self):
        if self.is_server_running:
            return

        # 验证端口号
        try:
            port = int(self.port_var.get())
            if not (MIN_PORT <= port <= MAX_PORT):
                messagebox.showerror("错误", f"端口号必须在 {MIN_PORT} 到 {MAX_PORT} 之间")
                return
        except ValueError:
            messagebox.showerror("错误", "请输入有效的端口号")
            return
        
        # 保存端口配置
        self.config["port"] = port
        self.save_config()
        
        ip = self.get_local_ip()
        url = f"https://{ip}:{port}"
        
        try:
            self.generate_cert()
            
            import eventlet.wsgi
            
            # 尝试监听端口逻辑 (带重试和强制重用选项)
            def try_listen(p, retries=5):
                for i in range(retries):
                    try:
                        # 手动创建 green socket 以确保设置 SO_REUSEADDR
                        from eventlet.green import socket as green_socket
                        res_sock = green_socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        res_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        # macOS/Linux: 尝试使用 SO_REUSEPORT 以避免端口占用错误
                        if hasattr(socket, 'SO_REUSEPORT'):
                            try:
                                res_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                            except:
                                pass
                        res_sock.bind(('0.0.0.0', p))
                        res_sock.listen(128)
                        return res_sock
                    except OSError as e:
                        if i < retries - 1:
                            self.log_message(f"端口 {p} 正在释放中，等待重试 ({i+1}/{retries})...", "WARNING")
                            time.sleep(0.5)  # 增加等待时间到 500ms
                            continue
                        raise e
            
            try:
                self.server_sock = try_listen(port)
            except OSError as e:
                if "[WinError 10048]" in str(e) or "address already in use" in str(e).lower():
                    messagebox.showerror("错误", f"端口 {port} 已被占用！\n请等待几秒后再试，或更换其他端口。")
                else:
                    messagebox.showerror("错误", f"启动服务失败: {e}")
                return

            def run_server(sock):
                try:
                    ssl_sock = eventlet.wrap_ssl(
                        sock,
                        certfile=str(self.cert_path),
                        keyfile=str(self.key_path),
                        server_side=True
                    )
                    # 使用自定义日志处理器抑制 SSL 握手错误
                    import logging
                    wsgi_logger = logging.getLogger('eventlet.wsgi')
                    wsgi_logger.setLevel(logging.ERROR)
                    
                    # 启动后台广播任务 (在 Server 线程上下文中运行)
                    self.socketio.start_background_task(self._bg_emit_loop)
                    
                    eventlet.wsgi.server(ssl_sock, self.flask_app, log_output=False)
                except Exception as e:
                    # 忽略 SSL 证书相关错误和手动关闭导致的异常
                    error_str = str(e).lower()
                    if "ssl" in error_str or "certificate" in error_str:
                        pass  # SSL 证书错误是正常的（用户未信任自签名证书）
                    elif self.is_server_running:
                        self.log_message(f"服务器异常退出: {e}", "ERROR")
                finally:
                    self.is_server_running = False

            self.server_thread = threading.Thread(target=run_server, args=(self.server_sock,), daemon=True)
            self.server_thread.start()
            
            self.is_server_running = True
            self.start_btn.config(text="停止服务", bootstyle=DANGER)
            self.status_indicator.config(text="● 服务运行中", foreground="#28a745")
            # 不自动启用录制按钮，需要等待麦克风开启
            self.port_entry.config(state=DISABLED)  # 禁用端口输入
            self.update_qr_code(url)
            
            self.log_message(f"服务启动成功: {url}", "SUCCESS")
            self.log_message("请使用手机扫描二维码，并确保手机与电脑在同一局域网", "INFO")

            # 更新可用连接地址列表
            self.ip_tree.delete(*self.ip_tree.get_children())
            all_ips = self.get_all_local_ips()
            for ip in all_ips:
                full_url = f"https://{ip}:{port}"
                item_id = self.ip_tree.insert("", END, values=(full_url,))
                # 自动选中当前主IP
                if full_url == url:
                    self.ip_tree.selection_set(item_id)
                    self.ip_tree.see(item_id)
            
        except Exception as e:
            self.log_message(f"服务启动失败: {e}", "ERROR")
            messagebox.showerror("服务启动失败", f"无法启动服务器：\n{e}")

    def stop_server(self):
        """物理关闭服务器以释放端口"""
        self.is_server_running = False
        
        # 强制关闭监听Socket，这将打断 eventlet 的阻塞等待
        if self.server_sock:
            try:
                # 先尝试 shutdown 再 close，确保彻底关闭
                try:
                    self.server_sock.shutdown(socket.SHUT_RDWR)
                except:
                    pass  # shutdown 可能失败（如果 socket 已经关闭）
                self.server_sock.close()
                self.log_message("服务器监听 Socket 已物理关闭", "DEBUG")
            except Exception as e:
                self.log_message(f"关闭 Socket 时发生异常: {e}", "DEBUG")
            self.server_sock = None
        
        # 等待一小段时间让操作系统释放端口
        time.sleep(0.3)

        self.start_btn.config(text="开启服务", bootstyle=SUCCESS)
        self.status_indicator.config(text="● 服务已停止", foreground="gray")
        self.rec_btn.config(state=DISABLED)
        self.port_entry.config(state=NORMAL)  # 恢复端口输入
        self.qr_label.config(image="", text="服务已停止")
        self.log_message("服务停止，端口已释放", "WARNING")

    def toggle_recording(self):
        if not self.is_recording:
            self.start_recording()
        else:
            self.stop_recording()

    def _remote_toggle_recording(self):
        """远程录制控制（由手机端触发）"""
        if not self.is_server_running:
            self.log_message("服务未运行，无法控制录制", "WARNING")
            return
        
        if not self.is_recording:
            # 开始录制
            if self.connected_clients <= 0:
                self.log_message("远程录制失败：未检测到手机连接", "WARNING")
                self._broadcast_recording_status()
                return
            
            self.is_recording = True
            self.audio_engine.start_recording()
            self.rec_btn.config(text="停止录制", bootstyle=WARNING)
            self.recording_start_time = time.time()
            self.update_rec_timer()
            self.log_message("手机端触发开始录制", "SUCCESS")
        else:
            # 停止录制
            self.is_recording = False
            frames, data_format, sample_rate = self.audio_engine.stop_recording()
            self.rec_btn.config(text="开始录制", bootstyle=DANGER)
            
            if frames:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                # 根据格式添加后缀
                suffix = "_32bit" if data_format == FORMAT_FLOAT32 else ""
                filename = f"REC_{timestamp}{suffix}.wav"
                
                filepath = self.record_dir / filename
                if self.audio_engine.save_wav(frames, str(filepath), data_format, sample_rate):
                    self.add_file_to_list(filename, timestamp)
            else:
                self.log_message("录制时间太短或无数据", "WARNING")
            
            self.log_message("手机端触发停止录制", "SUCCESS")
        
        # 广播录制状态给所有客户端
        self._broadcast_recording_status()

    def _broadcast_recording_status(self):
        """向所有连接的客户端广播当前录制状态"""
        try:
            # 将消息放入队列，由 Server 线程的后台任务处理
            # 这样避免了在 UI 线程直接调用 socketio.emit 可能导致的上下文冲突
            data = {'is_recording': self.is_recording}
            self.broadcast_queue.put({'type': 'recording_status', 'data': data})
            self.log_message(f"广播录制状态(已入列): {'录制中' if self.is_recording else '未录制'}", "DEBUG")
        except Exception as e:
            self.log_message(f"广播录制状态失败: {e}", "ERROR")

    def _bg_emit_loop(self):
        """后台广播循环 (运行在 Server 线程/Eventlet 上下文中)"""
        self.log_message("后台广播服务已启动", "DEBUG")
        while self.is_server_running:
            try:
                # 尝试从队列获取消息 (非阻塞)
                try:
                    msg = self.broadcast_queue.get_nowait()
                    if msg['type'] == 'recording_status':
                        self.socketio.emit('recording_status', msg['data'], namespace='/')
                except queue.Empty:
                    pass
                
                # 让出控制权给 Eventlet Hub
                self.socketio.sleep(0.1)
            except Exception as e:
                # 避免错误导致循环退出
                print(f"Broadcast loop error: {e}")
                self.socketio.sleep(1.0)
        self.log_message("后台广播服务已停止", "DEBUG")

    def start_recording(self):
        # 检查是否有设备连接
        if self.connected_clients <= 0:
            messagebox.showwarning("提示", "未检测到手机连接！\n请先使用手机扫描二维码并连接后再开始录制。")
            self.log_message("录制失败：未检测到手机连接", "WARNING")
            return
        
        # 检查是否有麦克风开启
        if len(self.mic_active_clients) == 0:
            messagebox.showwarning("提示", "无可用麦克风！\n请先在手机端点击“开启麦克风”后再开始录制。")
            self.log_message("录制失败：无可用麦克风", "WARNING")
            return

        self.is_recording = True
        self.audio_engine.start_recording()
        self.rec_btn.config(text="停止录制", bootstyle=WARNING)
        self.recording_start_time = time.time()
        self.update_rec_timer()
        # 广播录制状态给所有客户端
        self._broadcast_recording_status()

    def stop_recording(self):
        self.is_recording = False
        frames, data_format, sample_rate = self.audio_engine.stop_recording()
        self.rec_btn.config(text="开始录制", bootstyle=DANGER)
        # 广播录制状态给所有客户端
        self._broadcast_recording_status()
        
        if frames:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # 根据格式添加后缀
            suffix = "_32bit" if data_format == FORMAT_FLOAT32 else ""
            filename = f"REC_{timestamp}{suffix}.wav"
            
            filepath = self.record_dir / filename
            if self.audio_engine.save_wav(frames, str(filepath), data_format, sample_rate):
                self.add_file_to_list(filename, timestamp)
        else:
            self.log_message("录制时间太短或无数据", "WARNING")

    def update_rec_timer(self):
        if self.is_recording:
            elapsed = int(time.time() - self.recording_start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            self.rec_time_label.config(text=f"{mins:02d}:{secs:02d}")
            self.root.after(1000, self.update_rec_timer)
        else:
            self.rec_time_label.config(text="00:00")

    def add_file_to_list(self, name, timestamp):
        # 格式化时间
        formatted_time = datetime.strptime(timestamp, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        self.file_list.insert("", 0, values=(name, formatted_time))

    def _get_float32_duration(self, filepath):
        """手动解析 32-bit Float WAV 文件获取时长"""
        import struct
        
        try:
            with open(filepath, 'rb') as f:
                # 读取 RIFF 头
                riff = f.read(4)
                if riff != b'RIFF':
                    return 0
                
                f.read(4)  # 文件大小
                wave_id = f.read(4)
                if wave_id != b'WAVE':
                    return 0
                
                sample_rate = 44100
                total_samples = 0
                
                # 读取子块
                while True:
                    chunk_id = f.read(4)
                    if len(chunk_id) < 4:
                        break
                    
                    chunk_size = struct.unpack('<I', f.read(4))[0]
                    
                    if chunk_id == b'fmt ':
                        f.read(2)  # audio_format
                        f.read(2)  # num_channels
                        sample_rate = struct.unpack('<I', f.read(4))[0]
                        # 跳过剩余的 fmt 数据
                        remaining = chunk_size - 10
                        if remaining > 0:
                            f.read(remaining)
                            
                    elif chunk_id == b'data':
                        # 32-bit = 4 bytes per sample
                        total_samples = chunk_size // 4
                        break
                    else:
                        f.read(chunk_size)
                
                if sample_rate > 0 and total_samples > 0:
                    return total_samples / sample_rate
                
                return 0
        except:
            return 0
    
    def _format_file_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _format_duration(self, duration_seconds):
        """格式化音频时长"""
        if duration_seconds < 60:
            return f"{int(duration_seconds)}秒"
        elif duration_seconds < 3600:
            mins = int(duration_seconds // 60)
            secs = int(duration_seconds % 60)
            return f"{mins}分{secs}秒"
        else:
            hours = int(duration_seconds // 3600)
            mins = int((duration_seconds % 3600) // 60)
            secs = int(duration_seconds % 60)
            return f"{hours}时{mins}分{secs}秒"

    def _refresh_file_list(self):
        """刷新桌面端文件列表（由手机端删除文件后触发）"""
        try:
            # 清空列表
            self.file_list.delete(*self.file_list.get_children())
            # 重新加载
            self.load_existing_records()
            self.log_message("文件列表已刷新", "INFO")
        except Exception as e:
            self.log_message(f"刷新文件列表失败: {e}", "ERROR")

    def load_existing_records(self):
        """加载已有的录音文件到列表"""
        try:
            wav_files = sorted(self.record_dir.glob("*.wav"), key=lambda x: x.stat().st_mtime, reverse=True)
            for wav_file in wav_files:
                # 尝试从文件名解析时间戳
                filename = wav_file.name
                if filename.startswith("REC_") and len(filename) >= 19:
                    timestamp_str = filename[4:19]  # REC_20231225_143022.wav
                    try:
                        formatted_time = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
                        self.file_list.insert("", END, values=(filename, formatted_time))
                    except:
                        # 如果解析失败，使用文件修改时间
                        mtime = datetime.fromtimestamp(wav_file.stat().st_mtime)
                        formatted_time = mtime.strftime("%Y-%m-%d %H:%M:%S")
                        self.file_list.insert("", END, values=(filename, formatted_time))
                else:
                    # 使用文件修改时间
                    mtime = datetime.fromtimestamp(wav_file.stat().st_mtime)
                    formatted_time = mtime.strftime("%Y-%m-%d %H:%M:%S")
                    self.file_list.insert("", END, values=(filename, formatted_time))
            
            if wav_files:
                self.log_message(f"已加载 {len(wav_files)} 个录音文件", "INFO")
        except Exception as e:
            self.log_message(f"加载录音文件失败: {e}", "ERROR")

    def on_file_select(self, event):
        """单击列表项加载波形图 (不自动播放)"""
        selected = self.file_list.selection()
        if selected:
            filename = self.file_list.item(selected[0])['values'][0]
            filepath = self.record_dir / filename
            if filepath.exists():
                self.load_and_play_file(str(filepath), auto_play=False)

    def on_file_double_click(self, event):
        """双击文件列表项时加载并播放"""
        selected = self.file_list.selection()
        if selected:
            filename = self.file_list.item(selected[0])['values'][0]
            filepath = self.record_dir / filename
            if filepath.exists():
                self.load_and_play_file(str(filepath), auto_play=True)

    def load_and_play_file(self, filepath, auto_play=True):
        """加载音频文件到播放器并显示波形"""
        # 停止当前播放
        if self.audio_player.is_playing:
            self.audio_player.stop()
        
        # 加载音频文件
        if self.audio_player.load_file(filepath):
            # 加载波形
            self.waveform_viz.load_waveform(filepath)
            
            # 更新UI
            filename = Path(filepath).name
            self.current_file_label.config(text=filename, foreground="white")
            
            # 更新时长显示
            duration = self.audio_player.get_duration()
            mins = int(duration // 60)
            secs = int(duration % 60)
            self.duration_label.config(text=f"{mins:02d}:{secs:02d}")
            
            # 启用播放按钮
            self.play_btn.config(state=NORMAL)
            self.stop_btn.config(state=NORMAL)
            
            # 根据参数决定是否自动开始播放
            if auto_play:
                self.play_audio()
            else:
                self.waveform_viz.update_play_position(0)
                self.progress_var.set(0)
                self.time_label.config(text="00:00")

    def play_audio(self):
        """播放音频"""
        if self.audio_player.play():
            self.play_btn.config(state=DISABLED)
            self.pause_btn.config(state=NORMAL)
            self.stop_btn.config(state=NORMAL)
            # 启动波形动画
            self.waveform_viz.start_animation()
            self.start_play_update()

    def pause_audio(self):
        """暂停播放"""
        if self.audio_player.pause():
            self.play_btn.config(state=NORMAL)
            self.pause_btn.config(state=DISABLED)
            # 暂停时停止动画
            self.waveform_viz.stop_animation()

    def stop_audio(self):
        """停止播放"""
        self.audio_player.stop()
        self.play_btn.config(state=NORMAL)
        self.pause_btn.config(state=DISABLED)
        self.progress_var.set(0)
        self.time_label.config(text="00:00")
        self.waveform_viz.update_play_position(0)
        # 停止波形动画
        self.waveform_viz.stop_animation()
        if self.play_update_job:
            self.root.after_cancel(self.play_update_job)
            self.play_update_job = None

    def start_play_update(self):
        """开始更新播放进度"""
        def update():
            if self.audio_player.is_playing and not self.audio_player.is_paused:
                # 更新进度
                progress = self.audio_player.get_progress()
                self.progress_var.set(progress * 100)
                
                # 更新时间显示
                current_time = self.audio_player.get_current_time()
                mins = int(current_time // 60)
                secs = int(current_time % 60)
                self.time_label.config(text=f"{mins:02d}:{secs:02d}")
                
                # 更新波形位置（高频率更新以匹配60fps动画）
                self.waveform_viz.update_play_position(progress)
                
                # 继续更新（从100ms降低到20ms，匹配动画刷新率）
                self.play_update_job = self.root.after(10, update)
            else:
                # 播放结束，重置UI
                if not self.audio_player.is_playing and not self.audio_player.is_paused:
                    self.play_btn.config(state=NORMAL)
                    self.pause_btn.config(state=DISABLED)
                    self.progress_var.set(0)
                    self.time_label.config(text="00:00")
                    self.waveform_viz.update_play_position(0)
        
        update()

    def handle_keypress(self, event):
        """处理快捷键事件"""
        if not self.audio_player.wav_file and not hasattr(self.audio_player, 'float32_file'):
            return
            
        if event.keysym == "space":
            # 空格：播放/暂停
            if self.audio_player.is_playing:
                if self.audio_player.is_paused:
                    self.play_audio()
                else:
                    self.pause_audio()
            else:
                self.play_audio()
                
        elif event.keysym == "Left":
            # 左键：后退 5%
            current_progress = self.audio_player.get_progress()
            new_progress = max(0.0, current_progress - 0.05)
            self.on_waveform_click(new_progress)
            self.log_message(f"快退 5% -> {int(new_progress*100)}%", "INFO")
            
        elif event.keysym == "Right":
            # 右键：前进 5%
            current_progress = self.audio_player.get_progress()
            new_progress = min(1.0, current_progress + 0.05)
            self.on_waveform_click(new_progress)
            self.log_message(f"快进 5% -> {int(new_progress*100)}%", "INFO")

    def on_progress_change(self, value):
        """进度条拖动事件"""
        if self.audio_player.wav_file or hasattr(self.audio_player, 'float32_file'):
            # 计算目标帧位置
            progress = float(value) / 100
            target_frame = int(progress * self.audio_player.total_frames)
            
            # 跳转播放位置
            self.audio_player.seek(target_frame)
            
            # 更新波形位置
            self.waveform_viz.update_play_position(progress)
    
    def on_waveform_click(self, progress):
        """波形图点击跳转回调"""
        if self.audio_player.wav_file or hasattr(self.audio_player, 'float32_file'):
            # 更新进度条
            self.progress_var.set(progress * 100)
            
            # 跳转播放位置
            target_frame = int(progress * self.audio_player.total_frames)
            self.audio_player.seek(target_frame)
            
            # 更新时间显示
            current_time = self.audio_player.get_current_time()
            mins = int(current_time // 60)
            secs = int(current_time % 60)
            self.time_label.config(text=f"{mins:02d}:{secs:02d}")
            
            # 更新波形位置
            self.waveform_viz.update_play_position(progress)
            
            self.log_message(f"跳转到 {mins:02d}:{secs:02d}", "INFO")

    def on_realtime_playback_changed(self):
        """实时播放勾选框状态变化回调"""
        # 注意：勾选框是"停止实时播放"，所以要取反
        enable_playback = not self.realtime_playback_var.get()
        self.audio_engine.enable_realtime_playback = enable_playback
        self.config["enable_realtime_playback"] = enable_playback
        self.save_config()
        status = "已启用" if enable_playback else "已停止"
        self.log_message(f"实时播放{status} (仅影响监听，不影响录制)", "INFO")
    
    def on_delete_to_trash_changed(self):
        """删除到回收站勾选框状态变化回调"""
        self.config["delete_to_trash"] = self.delete_to_trash_var.get()
        self.save_config()
        status = "远程删除到回收站" if self.delete_to_trash_var.get() else "永久删除"
        self.log_message(f"删除模式已切换为: {status}", "INFO")
    
    def on_realtime_waveform_toggle(self):
        """实时波形勾选框变化回调"""
        enabled = self.realtime_waveform_var.get()
        self.config["enable_realtime_waveform"] = enabled
        self.save_config()
        
        if enabled:
            # 如果服务正在运行且有客户端连接，启动波形显示
            if self.is_server_running and self.connected_clients > 0:
                self.realtime_waveform.start()
        else:
            # 停止波形显示
            self.realtime_waveform.stop()
        
        status = "已启用" if enabled else "已禁用"
        self.log_message(f"实时波形显示{status}", "INFO")
    
    def on_waveform_duration_changed(self, event):
        """波形历史时长选择回调"""
        try:
            duration = int(self.waveform_duration_var.get())
            self.config["waveform_duration"] = duration
            self.save_config()
            
            # 动态调整波形时长
            if self.realtime_waveform:
                self.realtime_waveform.set_duration(duration)
        except ValueError:
            pass
    
    def update_realtime_waveform(self, audio_data):
        """更新实时波形和电平表显示（由 AudioEngine 回调）"""
        if self.realtime_waveform and self.realtime_waveform_var.get():
            self.realtime_waveform.update_data(audio_data)
        
        # 同时更新音频电平表
        if hasattr(self, 'audio_level_meter'):
            self.audio_level_meter.update_level(audio_data)

    def update_qr_code(self, url):
        """更新二维码显示"""
        try:
            self.url_var.set(url)
            qr = qrcode.QRCode(version=1, box_size=5, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_img.resize((200, 200))
            self.tk_qr = ImageTk.PhotoImage(qr_img)
            self.qr_label.config(image=self.tk_qr, text="")
        except Exception as e:
            self.log_message(f"二维码生成失败: {e}", "ERROR")

    def on_ip_selection_changed(self, event):
        """IP列表选择变更回调"""
        selected = self.ip_tree.selection()
        if selected:
            url = self.ip_tree.item(selected[0])['values'][0]
            # 避免重复更新（如点击已选中的项）
            if url != self.url_var.get():
                self.update_qr_code(url)
                self.log_message(f"切换连接地址: {url}", "INFO")

    def on_ip_double_click(self, event):
        """连接地址列表双击复制"""
        selected = self.ip_tree.selection()
        if selected:
            url = self.ip_tree.item(selected[0])['values'][0]
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            messagebox.showinfo("成功", f"连接地址已复制到剪贴板：\n{url}")
            self.log_message(f"已复制地址: {url}", "SUCCESS")

    def open_record_dir(self):
        os.startfile(self.record_dir)

    def open_selected_file(self, event):
        selected = self.file_list.selection()
        if selected:
            filename = self.file_list.item(selected[0])['values'][0]
            filepath = self.record_dir / filename
            if filepath.exists():
                os.startfile(filepath)

    def save_as_file(self):
        selected = self.file_list.selection()
        if not selected:
            messagebox.showinfo("提示", "请先从列表中选择一个录音文件")
            return
        
        filename = self.file_list.item(selected[0])['values'][0]
        src_path = self.record_dir / filename
        
        dst_path = filedialog.asksaveasfilename(
            defaultextension=".wav",
            initialfile=filename,
            filetypes=[("WAV files", "*.wav")]
        )
        
        if dst_path:
            import shutil
            try:
                shutil.copy2(src_path, dst_path)
                self.log_message(f"文件已另存为: {dst_path}", "SUCCESS")
            except Exception as e:
                self.log_message(f"另存为失败: {e}", "ERROR")

    def update_rec_button_state(self):
        """根据连接和麦克风状态更新录制按钮"""
        if self.is_server_running and len(self.mic_active_clients) > 0:
            self.rec_btn.config(state=NORMAL)
            self.log_message(f"录制按钮已启用 (活跃麦克风: {len(self.mic_active_clients)})", "INFO")
        else:
            self.rec_btn.config(state=DISABLED)
            reason = "服务未运行" if not self.is_server_running else "无活跃麦克风"
            self.log_message(f"录制按钮已禁用 ({reason})", "INFO")
    
    def on_closing(self):
        if self.is_recording:
            if not messagebox.askyesno("警告", "正在录制中，确定要退出吗？"):
                return
        
        # 停止波形动画
        self.waveform_viz.stop_animation()
        
        # 停止播放
        if self.audio_player.is_playing:
            self.audio_player.stop()
        
        # 关闭音频引擎
        self.audio_engine.close()
        self.audio_player.close()
        
        # 保存配置
        self.save_config()
        
        self.root.destroy()
        os._exit(0) # 确保线程能强制关闭

if __name__ == "__main__":
    # ==================== 修复打包后的 stdout/stderr 问题 ====================
    # 在打包环境中，sys.stdout 和 sys.stderr 可能为 None
    # 导致 eventlet.wsgi.server 尝试写入日志时出错
    # 创建一个虚拟的输出流来避免 'NoneType' object has no attribute 'write' 错误
    class DummyStream:
        """虚拟输出流，用于替代 None 的 stdout/stderr"""
        def write(self, data):
            pass  # 忽略所有输出
        
        def flush(self):
            pass  # 忽略刷新操作
        
        def isatty(self):
            return False
    
    # 检查并修复 stdout/stderr
    if sys.stdout is None:
        sys.stdout = DummyStream()
        print("[INIT] 已修复 sys.stdout (打包环境)")
    
    if sys.stderr is None:
        sys.stderr = DummyStream()
        print("[INIT] 已修复 sys.stderr (打包环境)")
    # ==================== 修复结束 ====================
    
    root = ttk.Window()
    app = MobileMicApp(root)
    root.mainloop()
