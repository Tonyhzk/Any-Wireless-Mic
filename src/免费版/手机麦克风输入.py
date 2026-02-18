import os
import sys
import json
import time
import socket
import threading
import queue
import wave
import logging
from pathlib import Path
from datetime import datetime

# pip install flask flask-socketio eventlet pyaudio ttkbootstrap qrcode pillow pyopenssl matplotlib numpy send2trash

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
from send2trash import send2trash

# ========== 全局配置 ==========
# 窗口设置
WINDOW_TITLE = "局域网无线麦克风"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
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
CHANNELS = 1
RATE = 44100

# 网络设置
DEFAULT_PORT = 5000
MIN_PORT = 1024
MAX_PORT = 65535

# UI组件设置
BUTTON_WIDTH = 12
LOG_DISPLAY_HEIGHT = 6
# ========== 全局配置结束 ==========

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
        
    def load_file(self, filepath):
        """加载音频文件"""
        try:
            self.stop()
            self.wav_file = wave.open(filepath, 'rb')
            self.total_frames = self.wav_file.getnframes()
            self.current_frame = 0
            self.current_file = filepath
            self.log(f"已加载音频文件: {Path(filepath).name}", "INFO")
            return True
        except Exception as e:
            self.log(f"加载音频文件失败: {e}", "ERROR")
            return False
    
    def play(self):
        """播放音频"""
        if not self.wav_file:
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
            if not self.stream:
                self.stream = self.pa.open(
                    format=self.pa.get_format_from_width(self.wav_file.getsampwidth()),
                    channels=self.wav_file.getnchannels(),
                    rate=self.wav_file.getframerate(),
                    output=True
                )
            
            chunk_size = 1024
            while self.is_playing and not self.stop_flag:
                if self.is_paused:
                    time.sleep(0.1)
                    continue
                
                data = self.wav_file.readframes(chunk_size)
                if not data:
                    self.is_playing = False
                    self.current_frame = 0
                    self.wav_file.rewind()
                    self.log("播放完成", "SUCCESS")
                    break
                
                self.stream.write(data)
                self.current_frame += chunk_size
                
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
        
        self.log("已停止播放", "INFO")
    
    def seek(self, frame_position):
        """跳转到指定帧位置"""
        if self.wav_file and 0 <= frame_position <= self.total_frames:
            self.wav_file.setpos(frame_position)
            self.current_frame = frame_position
            return True
        return False
    
    def get_progress(self):
        """获取播放进度 (0.0 - 1.0)"""
        if self.total_frames > 0:
            return min(1.0, self.current_frame / self.total_frames)
        return 0.0
    
    def get_duration(self):
        """获取总时长（秒）"""
        if self.wav_file:
            return self.total_frames / self.wav_file.getframerate()
        return 0
    
    def get_current_time(self):
        """获取当前播放时间（秒）"""
        if self.wav_file:
            return self.current_frame / self.wav_file.getframerate()
        return 0
    
    def close(self):
        """关闭播放器"""
        self.stop()
        if self.wav_file:
            self.wav_file.close()
        self.pa.terminate()

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
        """加载并显示波形"""
        try:
            with wave.open(filepath, 'rb') as wf:
                frames = wf.readframes(wf.getnframes())
                self.sample_rate = wf.getframerate()
                
                # 转换为numpy数组
                if wf.getsampwidth() == 2:
                    data = np.frombuffer(frames, dtype=np.int16)
                else:
                    data = np.frombuffer(frames, dtype=np.uint8)
                
                # 归一化
                self.waveform_data = data / np.max(np.abs(data)) if np.max(np.abs(data)) > 0 else data
                
                # 大幅降采样以提高显示性能（降低到2000个点）
                target_points = 10000
                downsample_factor = max(1, len(self.waveform_data) // target_points)
                display_data = self.waveform_data[::downsample_factor]
                
                # 计算总时长
                self.total_duration = len(self.waveform_data) / self.sample_rate
                
                # 绘制波形
                self.ax.clear()
                time_axis = np.arange(len(display_data)) * downsample_factor / self.sample_rate
                self.ax.plot(time_axis, display_data, color='#3498db', linewidth=0.8)
                self.ax.set_xlabel('Time (s)', color='white', fontsize=9)  # 使用英文避免字体问题
                self.ax.set_ylabel('Amplitude', color='white', fontsize=9)
                self.ax.grid(True, alpha=0.2, color='white')
                self._setup_plot()
                self.canvas.draw()
                
                self.log(f"波形加载成功，时长: {self.total_duration:.2f}秒", "INFO")
                return True
                
        except Exception as e:
            self.log(f"加载波形失败: {e}", "ERROR")
            return False
    
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
    """音频处理引擎"""
    def __init__(self, log_callback):
        self.pa = pyaudio.PyAudio()
        self.output_stream = None
        self.lock = threading.Lock() # 线程锁，保护流操作
        self.log = log_callback
        self.is_recording = False
        self.record_frames = []
        self.current_device_index = None
        
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

    def start_stream(self, device_index):
        """开启输出流"""
        self.stop_stream()
        with self.lock:
            try:
                self.output_stream = self.pa.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    output_device_index=device_index,
                    frames_per_buffer=CHUNK
                )
                self.current_device_index = device_index
                self.log(f"成功开启音频输出流 (设备ID: {device_index})", "INFO")
                return True
            except Exception as e:
                self.log(f"开启音频流失败: {e}", "ERROR")
                return False

    def stop_stream(self):
        """停止输出流"""
        with self.lock:
            if self.output_stream:
                try:
                    self.output_stream.stop_stream()
                    self.output_stream.close()
                except:
                    pass
                self.output_stream = None
                self.log("音频输出流已关闭", "INFO")

    def write_audio(self, data):
        """写入音频数据"""
        with self.lock:
            if self.output_stream:
                try:
                    self.output_stream.write(data)
                except Exception as e:
                    pass # 忽略流中断产生的错误
        
        if self.is_recording:
            self.record_frames.append(data)

    def start_recording(self):
        self.record_frames = []
        self.is_recording = True
        self.log("开始录制音频...", "INFO")

    def stop_recording(self):
        self.is_recording = False
        self.log(f"停止录制，捕获到 {len(self.record_frames)} 个数据块", "INFO")
        return self.record_frames

    def save_wav(self, frames, filepath):
        """保存音频到WAV文件"""
        try:
            wf = wave.open(filepath, 'wb')
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self.pa.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
            wf.close()
            self.log(f"音频已保存至: {filepath}", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"保存WAV失败: {e}", "ERROR")
            return False

    def close(self):
        self.stop_stream()
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
        self.recorded_files = []
        self.play_update_job = None
        self.server_sock = None # 存储监听Socket以便彻底关闭
        
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
        return {"theme": DEFAULT_THEME, "last_device": None, "port": DEFAULT_PORT}

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

    def setup_ui(self):
        # 顶部工具栏
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=X, side=TOP)
        
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
        
        # 设备选择
        ttk.Label(left_panel, text="音频输出设备 (选择虚拟线缆):").pack(anchor=W, pady=(0, 5))
        self.device_combo = ttk.Combobox(left_panel, state="readonly")
        self.device_combo.pack(fill=X, pady=(0, 10))
        self.device_combo.bind("<<ComboboxSelected>>", self.on_device_selected)

        # 端口设置
        port_frame = ttk.Frame(left_panel)
        port_frame.pack(fill=X, pady=(0, 10))
        ttk.Label(port_frame, text="服务端口:").pack(side=LEFT, padx=(0, 5))
        self.port_var = tk.StringVar(value=str(self.config.get("port", DEFAULT_PORT)))
        self.port_entry = ttk.Entry(port_frame, textvariable=self.port_var, width=10)
        self.port_entry.pack(side=LEFT, padx=(0, 5))
        ttk.Label(port_frame, text=f"({MIN_PORT}-{MAX_PORT})", font=("", 8)).pack(side=LEFT)

        # 服务控制
        btn_frame = ttk.Frame(left_panel)
        btn_frame.pack(fill=X, pady=10)
        self.start_btn = ttk.Button(btn_frame, text="开启服务", command=self.toggle_server, bootstyle=SUCCESS, width=15)
        self.start_btn.pack(side=LEFT, padx=5)
        
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
        devices = self.audio_engine.get_output_devices()
        self.device_list = devices
        device_names = [f"{d['index']}: {d['name']}" for d in devices]
        self.device_combo['values'] = device_names
        
        last_device = self.config.get("last_device")
        if last_device and last_device in device_names:
            self.device_combo.set(last_device)
            self.on_device_selected(None)
        elif device_names:
            self.device_combo.current(0)
            self.on_device_selected(None)
            
        self.log_message(f"已刷新音频输出设备，发现 {len(devices)} 个可用设备", "INFO")

    def on_device_selected(self, event):
        selection = self.device_combo.get()
        if selection:
            idx = int(selection.split(":")[0])
            self.audio_engine.start_stream(idx)
            self.config["last_device"] = selection
            self.save_config()

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
        """为 HTTPS 生成自签名证书"""
        if self.cert_path.exists() and self.key_path.exists():
            return
            
        self.log_message("正在生成自签名证书...", "INFO")
        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 2048)
        
        cert = crypto.X509()
        cert.get_subject().C = "CN"
        cert.get_subject().ST = "State"
        cert.get_subject().L = "City"
        cert.get_subject().O = "Organization"
        cert.get_subject().OU = "Unit"
        cert.get_subject().CN = self.get_local_ip()
        cert.set_serial_number(1000)
        cert.set_notBefore(b"20230101000000Z")
        cert.set_notAfter(b"20330101000000Z")
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.sign(k, 'sha256')
        
        with open(self.cert_path, "wb") as f:
            f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        with open(self.key_path, "wb") as f:
            f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k))

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
                    except:
                        pass
                    
                    audio_files.append({
                        'filename': filename,
                        'size': file_size,
                        'size_str': self._format_file_size(file_size),
                        'mtime': mtime.strftime("%Y-%m-%d %H:%M:%S"),
                        'duration': duration,
                        'duration_str': self._format_duration(duration)
                    })
                
                self.log_message(f"手机端请求音频列表，返回 {len(audio_files)} 个文件", "INFO")
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

        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.connected_clients = max(0, self.connected_clients - 1)
            self.log_message(f"手机已断开: {request.remote_addr} (当前连接: {self.connected_clients})", "WARNING")

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
            
            import eventlet
            import eventlet.wsgi
            
            # 尝试监听端口逻辑 (带重试和强制重用选项)
            def try_listen(p, retries=5):
                for i in range(retries):
                    try:
                        # 手动创建 green socket 以确保设置 SO_REUSEADDR
                        from eventlet.green import socket as green_socket
                        res_sock = green_socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        res_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
            self.rec_btn.config(state=NORMAL)
            self.port_entry.config(state=DISABLED)  # 禁用端口输入
            self.url_var.set(url)
            
            # 生成二维码
            qr = qrcode.QRCode(version=1, box_size=5, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            
            # 转换为 Tkinter 可用图片
            qr_img = qr_img.resize((200, 200))
            self.tk_qr = ImageTk.PhotoImage(qr_img)
            self.qr_label.config(image=self.tk_qr, text="")
            
            self.log_message(f"服务启动成功: {url}", "SUCCESS")
            self.log_message("请使用手机扫描二维码，并确保手机与电脑在同一局域网", "INFO")

            # 更新可用连接地址列表
            self.ip_tree.delete(*self.ip_tree.get_children())
            all_ips = self.get_all_local_ips()
            for ip in all_ips:
                full_url = f"https://{ip}:{port}"
                self.ip_tree.insert("", END, values=(full_url,))
            
        except Exception as e:
            self.log_message(f"服务启动失败: {e}", "ERROR")

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
            frames = self.audio_engine.stop_recording()
            self.rec_btn.config(text="开始录制", bootstyle=DANGER)
            
            if frames:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"REC_{timestamp}.wav"
                filepath = self.record_dir / filename
                if self.audio_engine.save_wav(frames, str(filepath)):
                    self.add_file_to_list(filename, timestamp)
            else:
                self.log_message("录制时间太短或无数据", "WARNING")
            
            self.log_message("手机端触发停止录制", "SUCCESS")
        
        # 广播录制状态给所有客户端
        self._broadcast_recording_status()

    def _broadcast_recording_status(self):
        """向所有连接的客户端广播当前录制状态"""
        try:
            # 保存当前录制状态，因为后台任务可能延迟执行
            current_status = self.is_recording
            
            def do_emit():
                try:
                    self.socketio.emit('recording_status', {'is_recording': current_status})
                except Exception as e:
                    pass  # 忽略广播错误
            
            # 使用 Flask-SocketIO 的 start_background_task 确保在正确的上下文中执行
            self.socketio.start_background_task(do_emit)
            self.log_message(f"广播录制状态: {'录制中' if current_status else '未录制'}", "DEBUG")
        except Exception as e:
            self.log_message(f"广播录制状态失败: {e}", "ERROR")

    def start_recording(self):
        # 检查是否有设备连接
        if self.connected_clients <= 0:
            messagebox.showwarning("提示", "未检测到手机连接！\n请先使用手机扫描二维码并连接后再开始录制。")
            self.log_message("录制失败：未检测到手机连接", "WARNING")
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
        frames = self.audio_engine.stop_recording()
        self.rec_btn.config(text="开始录制", bootstyle=DANGER)
        # 广播录制状态给所有客户端
        self._broadcast_recording_status()
        
        if frames:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"REC_{timestamp}.wav"
            filepath = self.record_dir / filename
            if self.audio_engine.save_wav(frames, str(filepath)):
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
        if not self.audio_player.wav_file:
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
        if self.audio_player.wav_file:
            # 计算目标帧位置
            progress = float(value) / 100
            target_frame = int(progress * self.audio_player.total_frames)
            
            # 跳转播放位置
            self.audio_player.seek(target_frame)
            
            # 更新波形位置
            self.waveform_viz.update_play_position(progress)
    
    def on_waveform_click(self, progress):
        """波形图点击跳转回调"""
        if self.audio_player.wav_file:
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

    def on_delete_to_trash_changed(self):
        """删除到回收站勾选框状态变化回调"""
        self.config["delete_to_trash"] = self.delete_to_trash_var.get()
        self.save_config()
        status = "远程删除到回收站" if self.delete_to_trash_var.get() else "永久删除"
        self.log_message(f"删除模式已切换为: {status}", "INFO")

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
