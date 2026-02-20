"""主窗口 - PySide6 实现"""

import os
import sys
import json
import time
import socket
import threading
import queue
import webbrowser
import platform
import shutil
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QPushButton, QComboBox, QLineEdit, QLabel,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QSlider, QCheckBox, QFileDialog, QMessageBox, QSplitter
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QPixmap, QImage, QIcon, QTextCursor
import qrcode
from PIL import Image
from flask import Flask
from flask_socketio import SocketIO

from config import (
    WINDOW_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT,
    CONFIG_FILE_NAME, LOG_FILE_NAME, CERT_FILE_NAME, KEY_FILE_NAME, RECORD_DIR,
    DEFAULT_PORT, MIN_PORT, MAX_PORT, FORMAT_FLOAT32,
    ENABLE_LOG_FILE, ENABLE_REALTIME_PLAYBACK, DARK_THEME,
    get_default_record_dir
)
from audio import AudioEngine, AudioPlayer
from server.cert import generate_cert
from server.routes import register_routes
from ui.level_meter import AudioLevelMeter
from ui.waveform import WaveformVisualizer
from ui.realtime_waveform import RealtimeWaveformVisualizer


class _SignalBridge(QObject):
    """线程安全的信号桥，用于从非 UI 线程触发 UI 更新"""
    log_signal = Signal(str, str)
    schedule_signal = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        # 路径设置
        self.base_path = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent))
        if getattr(sys, 'frozen', False):
            if sys.platform == 'win32':
                self.config_dir = Path(os.environ['APPDATA']) / 'Any Wireless Mic'
            elif sys.platform == 'darwin':
                self.config_dir = Path.home() / 'Library' / 'Application Support' / 'Any Wireless Mic'
            else:
                self.config_dir = Path.home() / '.config' / 'Any Wireless Mic'
            self.config_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.config_dir = Path(__file__).parent.parent

        self.config_path = self.config_dir / CONFIG_FILE_NAME
        self.log_path = self.config_dir / LOG_FILE_NAME
        self.cert_path = self.config_dir / CERT_FILE_NAME
        self.key_path = self.config_dir / KEY_FILE_NAME

        # 加载图标
        icon_path = self.base_path / "assets" / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # 信号桥
        self._bridge = _SignalBridge()
        self._bridge.log_signal.connect(self._append_log)
        self._bridge.schedule_signal.connect(lambda fn: fn())

        # 初始化组件
        self.audio_engine = AudioEngine(self.log_message)
        self.audio_player = AudioPlayer(self.log_message)

        # Flask 3.1 兼容性补丁：RequestContext.session 在 3.1 中变为只读，
        # 但 Flask-SocketIO 5.6.0 仍会尝试赋值，需要手动添加 setter
        try:
            from flask.ctx import RequestContext
            if RequestContext.session.fset is None:
                _orig_getter = RequestContext.session.fget
                RequestContext.session = property(
                    _orig_getter,
                    lambda self, value: object.__setattr__(self, '_session', value)
                )
        except Exception:
            pass

        # Flask + SocketIO
        self.flask_app = Flask(__name__)
        try:
            self.socketio = SocketIO(self.flask_app, cors_allowed_origins="*", async_mode='eventlet')
        except ValueError:
            self.log_message("eventlet 不可用，使用 threading 模式", "WARNING")
            self.socketio = SocketIO(self.flask_app, cors_allowed_origins="*", async_mode='threading')

        # 状态
        self.server_thread = None
        self.is_server_running = False
        self.is_recording = False
        self.connected_clients = 0
        self.mic_active_clients = set()
        self.play_update_timer = None
        self.server_sock = None
        self.broadcast_queue = queue.Queue()
        self.recording_start_time = 0

        # 加载配置
        self.config = self._load_config()

        # 录制目录：优先配置，否则用户目录默认值
        saved_dir = self.config.get("record_dir")
        if saved_dir and Path(saved_dir).exists():
            self.record_dir = Path(saved_dir)
        else:
            self.record_dir = get_default_record_dir()
        self.record_dir.mkdir(parents=True, exist_ok=True)

        # 构建 UI
        self._setup_ui()

        # 注册路由
        self._register_routes()

        # 初始化
        self._refresh_devices()
        self._load_existing_records()

        self.log_message("程序初始化完成 (空格: 播放/暂停, 左右键: 快进/快退)", "INFO")

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 5, 10, 5)
        root_layout.setSpacing(5)

        # ===== 顶部工具栏 =====
        toolbar = QHBoxLayout()
        self.btn_driver = QPushButton("📥 安装驱动")
        self.btn_driver.clicked.connect(self._open_driver_website)
        toolbar.addWidget(self.btn_driver)

        self.btn_refresh = QPushButton("刷新设备")
        self.btn_refresh.clicked.connect(self._refresh_devices)
        toolbar.addWidget(self.btn_refresh)

        toolbar.addStretch()
        self.status_label = QLabel("● 服务未运行")
        self.status_label.setStyleSheet(f"color: gray; font-size: 13px;")
        toolbar.addWidget(self.status_label)
        root_layout.addLayout(toolbar)

        # ===== 主内容区（上半部分左右分栏） =====
        main_splitter = QSplitter(Qt.Horizontal)

        # --- 左面板：连接控制 ---
        left_group = QGroupBox("连接控制")
        left_layout = QVBoxLayout(left_group)

        # 监听设备和虚拟麦克风左右布局
        devices_row = QHBoxLayout()

        # 左侧：监听设备
        monitor_group = QVBoxLayout()
        monitor_header = QHBoxLayout()
        monitor_header.addWidget(QLabel("🎧 监听设备:"))
        self.chk_monitor = QCheckBox("启用")
        self.chk_monitor.setChecked(self.config.get("enable_monitor", True))
        self.chk_monitor.stateChanged.connect(self._on_monitor_enabled_changed)
        monitor_header.addStretch()
        monitor_header.addWidget(self.chk_monitor)
        monitor_group.addLayout(monitor_header)

        self.combo_monitor = QComboBox()
        self.combo_monitor.currentIndexChanged.connect(self._on_monitor_device_selected)
        monitor_group.addWidget(self.combo_monitor)
        devices_row.addLayout(monitor_group, stretch=1)

        # 右侧：虚拟麦克风
        vmic_group = QVBoxLayout()
        vmic_header = QHBoxLayout()
        vmic_header.addWidget(QLabel("🎤 虚拟麦克风:"))
        self.chk_vmic = QCheckBox("启用")
        self.chk_vmic.setChecked(self.config.get("enable_virtual_mic", True))
        self.chk_vmic.stateChanged.connect(self._on_vmic_enabled_changed)
        vmic_header.addStretch()
        vmic_header.addWidget(self.chk_vmic)
        vmic_group.addLayout(vmic_header)

        self.combo_vmic = QComboBox()
        self.combo_vmic.currentIndexChanged.connect(self._on_vmic_device_selected)
        vmic_group.addWidget(self.combo_vmic)
        devices_row.addLayout(vmic_group, stretch=1)

        left_layout.addLayout(devices_row)

        # 端口 + 电平表 + 服务按钮
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("服务端口:"))
        self.edit_port = QLineEdit(str(self.config.get("port", DEFAULT_PORT)))
        self.edit_port.setFixedWidth(80)
        port_row.addWidget(self.edit_port)
        port_row.addWidget(QLabel(f"({MIN_PORT}-{MAX_PORT})"))

        # 音频电平表（紧凑型）
        port_row.addWidget(QLabel("  电平:"))
        self.level_meter = AudioLevelMeter(width=150, height=20)
        port_row.addWidget(self.level_meter)

        port_row.addStretch()
        self.btn_server = QPushButton("开启服务")
        self.btn_server.setStyleSheet(f"background-color: {DARK_THEME['success']}; color: #000; font-weight: bold;")
        self.btn_server.clicked.connect(self._toggle_server)
        port_row.addWidget(self.btn_server)
        left_layout.addLayout(port_row)

        # 实时波形（控制和画布在同一行）
        wf_group = QGroupBox("实时音频波形")
        wf_layout = QHBoxLayout(wf_group)

        # 左侧控制
        wf_ctrl = QVBoxLayout()
        ctrl_row1 = QHBoxLayout()
        ctrl_row1.addWidget(QLabel("启用:"))
        self.chk_realtime_wf = QCheckBox()
        self.chk_realtime_wf.setChecked(self.config.get("enable_realtime_waveform", True))
        self.chk_realtime_wf.stateChanged.connect(self._on_realtime_wf_toggle)
        ctrl_row1.addWidget(self.chk_realtime_wf)
        ctrl_row1.addStretch()
        wf_ctrl.addLayout(ctrl_row1)

        ctrl_row2 = QHBoxLayout()
        ctrl_row2.addWidget(QLabel("历史:"))
        self.combo_wf_duration = QComboBox()
        self.combo_wf_duration.addItems(["5", "10", "15", "30"])
        self.combo_wf_duration.setCurrentText(str(self.config.get("waveform_duration", 10)))
        self.combo_wf_duration.currentTextChanged.connect(self._on_wf_duration_changed)
        self.combo_wf_duration.setFixedWidth(60)
        ctrl_row2.addWidget(self.combo_wf_duration)
        ctrl_row2.addWidget(QLabel("秒"))
        ctrl_row2.addStretch()
        wf_ctrl.addLayout(ctrl_row2)
        wf_ctrl.addStretch()
        wf_layout.addLayout(wf_ctrl)

        # 右侧波形画布
        initial_dur = self.config.get("waveform_duration", 10)
        self.realtime_waveform = RealtimeWaveformVisualizer(log_callback=self.log_message, duration_seconds=initial_dur)
        wf_layout.addWidget(self.realtime_waveform, stretch=1)
        self.audio_engine.waveform_callback = self._update_realtime_waveform
        left_layout.addWidget(wf_group)

        # QR 码 + 地址列表
        conn_layout = QHBoxLayout()
        self.qr_label = QLabel("服务开启后\n显示连接二维码")
        self.qr_label.setFixedSize(150, 150)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setStyleSheet(f"border: 1px solid {DARK_THEME['border']}; border-radius: 4px;")
        conn_layout.addWidget(self.qr_label)

        ip_layout = QVBoxLayout()
        self.edit_url = QLineEdit("等待服务启动...")
        self.edit_url.setReadOnly(True)
        ip_layout.addWidget(self.edit_url)
        ip_layout.addWidget(QLabel("可用连接地址 (双击复制):"))
        self.ip_tree = QTreeWidget()
        self.ip_tree.setHeaderLabels(["连接地址"])
        self.ip_tree.itemDoubleClicked.connect(self._on_ip_double_click)
        self.ip_tree.itemSelectionChanged.connect(self._on_ip_selection_changed)
        ip_layout.addWidget(self.ip_tree)
        conn_layout.addLayout(ip_layout)
        left_layout.addLayout(conn_layout)

        main_splitter.addWidget(left_group)

        # --- 右面板：录制管理 ---
        right_group = QGroupBox("录制管理")
        right_layout = QVBoxLayout(right_group)

        rec_ctrl = QHBoxLayout()
        self.btn_rec = QPushButton("开始录制")
        self.btn_rec.setStyleSheet(f"background-color: {DARK_THEME['danger']}; color: #fff; font-weight: bold;")
        self.btn_rec.setEnabled(False)
        self.btn_rec.clicked.connect(self._toggle_recording)
        rec_ctrl.addWidget(self.btn_rec)
        self.lbl_rec_time = QLabel("00:00")
        self.lbl_rec_time.setStyleSheet("font-family: Consolas; font-size: 14px;")
        rec_ctrl.addWidget(self.lbl_rec_time)
        rec_ctrl.addStretch()
        right_layout.addLayout(rec_ctrl)

        right_layout.addWidget(QLabel("录音记录 (双击播放):"))
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["文件名", "录制时间"])
        self.file_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.file_tree.itemDoubleClicked.connect(self._on_file_double_click)
        self.file_tree.itemSelectionChanged.connect(self._on_file_select)
        right_layout.addWidget(self.file_tree)

        file_btn_row = QHBoxLayout()
        btn_open_dir = QPushButton("打开目录")
        btn_open_dir.clicked.connect(self._open_record_dir)
        file_btn_row.addWidget(btn_open_dir)
        btn_change_dir = QPushButton("更改目录")
        btn_change_dir.clicked.connect(self._change_record_dir)
        file_btn_row.addWidget(btn_change_dir)
        btn_save_as = QPushButton("另存为...")
        btn_save_as.clicked.connect(self._save_as_file)
        file_btn_row.addWidget(btn_save_as)
        file_btn_row.addStretch()
        self.chk_trash = QCheckBox("远程删除到回收站")
        self.chk_trash.setChecked(self.config.get("delete_to_trash", True))
        self.chk_trash.stateChanged.connect(self._on_trash_changed)
        file_btn_row.addWidget(self.chk_trash)
        right_layout.addLayout(file_btn_row)

        main_splitter.addWidget(right_group)
        root_layout.addWidget(main_splitter, stretch=1)

        # ===== 底部：音频播放器 =====
        player_group = QGroupBox("音频播放器")
        player_layout = QVBoxLayout(player_group)

        self.waveform_viz = WaveformVisualizer(log_callback=self.log_message, click_callback=self._on_waveform_click)
        player_layout.addWidget(self.waveform_viz)

        ctrl_row = QHBoxLayout()
        self.btn_play_pause = QPushButton("▶ 播放")
        self.btn_play_pause.clicked.connect(self._toggle_play_pause)
        self.btn_play_pause.setFixedWidth(90)
        ctrl_row.addWidget(self.btn_play_pause)
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.clicked.connect(self._stop_audio)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setFixedWidth(90)
        ctrl_row.addWidget(self.btn_stop)

        self.lbl_time = QLabel("00:00")
        self.lbl_time.setStyleSheet("font-family: Consolas; font-size: 11px;")
        ctrl_row.addWidget(self.lbl_time)
        self.slider_progress = QSlider(Qt.Horizontal)
        self.slider_progress.setRange(0, 1000)
        self.slider_progress.sliderMoved.connect(self._on_progress_change)
        ctrl_row.addWidget(self.slider_progress, stretch=1)
        self.lbl_duration = QLabel("00:00")
        self.lbl_duration.setStyleSheet("font-family: Consolas; font-size: 11px;")
        ctrl_row.addWidget(self.lbl_duration)
        self.lbl_current_file = QLabel("未加载文件")
        self.lbl_current_file.setStyleSheet(f"color: {DARK_THEME['text_secondary']}; font-size: 11px;")
        ctrl_row.addWidget(self.lbl_current_file)
        player_layout.addLayout(ctrl_row)
        root_layout.addWidget(player_group)

        # ===== 最底部：日志 =====
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(80)
        self.log_text.setStyleSheet(f"font-family: Consolas, Menlo, monospace; font-size: 11px;")
        root_layout.addWidget(self.log_text)

        # 同步音频引擎状态
        self.audio_engine.enable_monitor_playback = self.chk_monitor.isChecked()
        self.audio_engine.enable_virtual_mic_output = self.chk_vmic.isChecked()

    # ==================== 配置管理 ====================

    def _load_config(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    if 'port' in config:
                        config['port'] = max(MIN_PORT, min(MAX_PORT, config['port']))
                    return config
            except Exception:
                pass
        return {
            "port": DEFAULT_PORT,
            "enable_monitor": True,
            "enable_virtual_mic": True,
            "enable_realtime_waveform": True,
            "waveform_duration": 10,
            "delete_to_trash": True,
            "enable_realtime_playback": ENABLE_REALTIME_PLAYBACK,
        }

    def _save_config(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.log_message(f"保存配置失败: {e}", "ERROR")

    # ==================== 日志 ====================

    def log_message(self, message, level="INFO"):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        full = f"{timestamp} [{level}] {message}"
        self._bridge.log_signal.emit(full, level)

    def _append_log(self, full_message, level):
        color_map = {
            "ERROR": DARK_THEME['danger'],
            "SUCCESS": DARK_THEME['success'],
            "WARNING": DARK_THEME['yellow'],
        }
        color = color_map.get(level, DARK_THEME['text'])
        self.log_text.append(f'<span style="color:{color}">{full_message}</span>')
        self.log_text.moveCursor(QTextCursor.End)

        if ENABLE_LOG_FILE:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(full_message + "\n")
            except:
                pass

    def schedule_ui(self, fn):
        """在 UI 线程中执行回调（线程安全）"""
        self._bridge.schedule_signal.emit(fn)

    # ==================== 设备管理 ====================

    def _refresh_devices(self):
        devices = self.audio_engine.get_output_devices()
        self._device_list = devices
        names = [f"{d['index']}: {d['name']}" for d in devices]

        self.combo_monitor.blockSignals(True)
        self.combo_vmic.blockSignals(True)
        self.combo_monitor.clear()
        self.combo_vmic.clear()
        self.combo_monitor.addItems(names)
        self.combo_vmic.addItems(names)

        # 恢复监听设备
        last_monitor = self.config.get("monitor_device", "")
        idx = self.combo_monitor.findText(last_monitor)
        if idx >= 0:
            self.combo_monitor.setCurrentIndex(idx)
        elif names:
            self.combo_monitor.setCurrentIndex(0)

        # 恢复虚拟麦克风设备（优先选含 cable/virtual 的）
        last_vmic = self.config.get("virtual_mic_device", "")
        idx = self.combo_vmic.findText(last_vmic)
        if idx >= 0:
            self.combo_vmic.setCurrentIndex(idx)
        elif names:
            cable_idx = next((i for i, n in enumerate(names) if "cable" in n.lower() or "virtual" in n.lower()), 0)
            self.combo_vmic.setCurrentIndex(cable_idx)

        self.combo_monitor.blockSignals(False)
        self.combo_vmic.blockSignals(False)
        self._on_monitor_device_selected()
        self._on_vmic_device_selected()
        self.log_message(f"已刷新音频输出设备，发现 {len(devices)} 个可用设备", "INFO")

    def _on_monitor_device_selected(self):
        text = self.combo_monitor.currentText()
        if text:
            idx = int(text.split(":")[0])
            self.audio_engine.start_monitor_stream(idx)
            self.config["monitor_device"] = text
            self._save_config()

    def _on_vmic_device_selected(self):
        text = self.combo_vmic.currentText()
        if text:
            idx = int(text.split(":")[0])
            self.audio_engine.start_virtual_mic_stream(idx)
            self.config["virtual_mic_device"] = text
            self._save_config()

    def _on_monitor_enabled_changed(self):
        enabled = self.chk_monitor.isChecked()
        self.audio_engine.enable_monitor_playback = enabled
        self.config["enable_monitor"] = enabled
        self._save_config()
        self.log_message(f"监听设备{'已启用' if enabled else '已禁用'}", "INFO")

    def _on_vmic_enabled_changed(self):
        enabled = self.chk_vmic.isChecked()
        self.audio_engine.enable_virtual_mic_output = enabled
        self.config["enable_virtual_mic"] = enabled
        self._save_config()
        self.log_message(f"虚拟麦克风{'已启用' if enabled else '已禁用'}", "INFO")

    # ==================== 网络 ====================

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def _get_all_local_ips(self):
        ips = set()
        try:
            hostname = socket.gethostname()
            _, _, ip_list = socket.gethostbyname_ex(hostname)
            for ip in ip_list:
                if not ip.startswith("127.") and ":" not in ip:
                    ips.add(ip)
        except:
            pass
        main_ip = self._get_local_ip()
        if main_ip != "127.0.0.1":
            ips.add(main_ip)
        return sorted(list(ips))

    # ==================== 路由注册 ====================

    def _register_routes(self):
        """构建路由上下文并注册"""
        ctx = _RouteContext(self)
        register_routes(ctx)

    # ==================== 服务器控制 ====================

    def _toggle_server(self):
        if not self.is_server_running:
            self._start_server()
        else:
            self._stop_server()

    def _start_server(self):
        if self.is_server_running:
            return
        try:
            port = int(self.edit_port.text())
            if not (MIN_PORT <= port <= MAX_PORT):
                QMessageBox.critical(self, "错误", f"端口号必须在 {MIN_PORT} 到 {MAX_PORT} 之间")
                return
        except ValueError:
            QMessageBox.critical(self, "错误", "请输入有效的端口号")
            return

        self.config["port"] = port
        self._save_config()

        ip = self._get_local_ip()
        url = f"https://{ip}:{port}"

        try:
            generate_cert(str(self.cert_path), str(self.key_path), ip,
                          self._get_all_local_ips(), self.log_message)

            import eventlet.wsgi
            from eventlet.green import socket as green_socket

            def try_listen(p, retries=5):
                for i in range(retries):
                    try:
                        res_sock = green_socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        res_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
                            time.sleep(0.5)
                            continue
                        raise e

            try:
                self.server_sock = try_listen(port)
            except OSError as e:
                if "address already in use" in str(e).lower() or "[WinError 10048]" in str(e):
                    QMessageBox.critical(self, "错误", f"端口 {port} 已被占用！\n请等待几秒后再试，或更换其他端口。")
                else:
                    QMessageBox.critical(self, "错误", f"启动服务失败: {e}")
                return

            def run_server(sock):
                try:
                    import eventlet
                    import logging
                    import ssl as ssl_module
                    import sys
                    import io

                    # 创建一个过滤 SSL 错误的 stderr 包装器
                    class SSLErrorFilter(io.TextIOBase):
                        def __init__(self, original_stderr):
                            self.original_stderr = original_stderr
                            self.buffer = []

                        def write(self, text):
                            # 过滤掉 SSL 相关的错误信息
                            if any(keyword in text for keyword in [
                                'ssl.SSLError', 'SSLV3_ALERT', 'SSLEOFError',
                                'certificate unknown', 'Removing descriptor',
                                'Traceback (most recent call last):', 'File "/',
                                'eventlet/hubs/selects.py', 'eventlet/wsgi.py',
                                'eventlet/green/ssl.py', '_ssl.c:'
                            ]):
                                # 缓存可能的堆栈跟踪
                                self.buffer.append(text)
                                # 如果缓存超过 20 行，清空（避免内存泄漏）
                                if len(self.buffer) > 20:
                                    self.buffer = []
                                return len(text)
                            else:
                                # 非 SSL 错误，正常输出
                                if self.buffer:
                                    self.buffer = []  # 清空缓存
                                return self.original_stderr.write(text)

                        def flush(self):
                            return self.original_stderr.flush()

                    # 替换 stderr
                    original_stderr = sys.stderr
                    sys.stderr = SSLErrorFilter(original_stderr)

                    try:
                        ssl_sock = eventlet.wrap_ssl(sock,
                                                      certfile=str(self.cert_path),
                                                      keyfile=str(self.key_path),
                                                      server_side=True,
                                                      ssl_version=ssl_module.PROTOCOL_TLS_SERVER)

                        # 禁用 WSGI 日志
                        wsgi_logger = logging.getLogger('eventlet.wsgi')
                        wsgi_logger.setLevel(logging.CRITICAL)

                        self.socketio.start_background_task(self._bg_emit_loop)
                        eventlet.wsgi.server(ssl_sock, self.flask_app, log_output=False)
                    finally:
                        # 恢复原始 stderr
                        sys.stderr = original_stderr

                except Exception as e:
                    error_str = str(e).lower()
                    # 忽略常见的 SSL 握手失败（客户端拒绝证书）
                    if any(x in error_str for x in ['ssl', 'certificate', 'eof occurred']):
                        pass  # 静默处理 SSL 错误
                    elif self.is_server_running:
                        self.log_message(f"服务器异常退出: {e}", "ERROR")
                finally:
                    self.is_server_running = False

            self.server_thread = threading.Thread(target=run_server, args=(self.server_sock,), daemon=True)
            self.server_thread.start()

            self.is_server_running = True
            self.btn_server.setText("停止服务")
            self.btn_server.setStyleSheet(f"background-color: {DARK_THEME['danger']}; color: #fff; font-weight: bold;")
            self.status_label.setText("● 服务运行中")
            self.status_label.setStyleSheet(f"color: {DARK_THEME['success']};")
            self.edit_port.setEnabled(False)
            self._update_qr_code(url)

            self.log_message(f"服务启动成功: {url}", "SUCCESS")
            self.log_message("请使用手机扫描二维码，并确保手机与电脑在同一局域网", "INFO")
            self.log_message("⚠️ 首次访问会提示证书不安全，请点击「继续访问」或「高级 > 继续前往」", "WARNING")

            # 更新地址列表
            self.ip_tree.clear()
            for addr in self._get_all_local_ips():
                full_url = f"https://{addr}:{port}"
                item = QTreeWidgetItem([full_url])
                self.ip_tree.addTopLevelItem(item)
                if full_url == url:
                    self.ip_tree.setCurrentItem(item)

        except Exception as e:
            self.log_message(f"服务启动失败: {e}", "ERROR")
            QMessageBox.critical(self, "服务启动失败", f"无法启动服务器：\n{e}")

    def _stop_server(self):
        self.is_server_running = False
        if self.server_sock:
            try:
                try:
                    self.server_sock.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                self.server_sock.close()
            except:
                pass
            self.server_sock = None
        time.sleep(0.3)

        self.btn_server.setText("开启服务")
        self.btn_server.setStyleSheet(f"background-color: {DARK_THEME['success']}; color: #000; font-weight: bold;")
        self.status_label.setText("● 服务已停止")
        self.status_label.setStyleSheet("color: gray;")
        self.btn_rec.setEnabled(False)
        self.edit_port.setEnabled(True)
        self.qr_label.setPixmap(QPixmap())
        self.qr_label.setText("服务已停止")
        self.log_message("服务停止，端口已释放", "WARNING")

    def _bg_emit_loop(self):
        self.log_message("后台广播服务已启动", "DEBUG")
        while self.is_server_running:
            try:
                try:
                    msg = self.broadcast_queue.get_nowait()
                    if msg['type'] == 'recording_status':
                        self.socketio.emit('recording_status', msg['data'], namespace='/')
                except queue.Empty:
                    pass
                self.socketio.sleep(0.1)
            except Exception as e:
                print(f"Broadcast loop error: {e}")
                self.socketio.sleep(1.0)
        self.log_message("后台广播服务已停止", "DEBUG")

    # ==================== 录制控制 ====================

    def _toggle_recording(self):
        if not self.is_recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        if self.connected_clients <= 0:
            QMessageBox.warning(self, "提示", "未检测到手机连接！\n请先使用手机扫描二维码并连接后再开始录制。")
            return
        if len(self.mic_active_clients) == 0:
            QMessageBox.warning(self, "提示", "无可用麦克风！\n请先在手机端点击「开启麦克风」后再开始录制。")
            return
        self.is_recording = True
        self.audio_engine.start_recording()
        self.btn_rec.setText("停止录制")
        self.btn_rec.setStyleSheet(f"background-color: {DARK_THEME['warning']}; color: #000; font-weight: bold;")
        self.recording_start_time = time.time()
        self._update_rec_timer()
        self._broadcast_recording_status()

    def _stop_recording(self):
        self.is_recording = False
        frames, data_format, sample_rate = self.audio_engine.stop_recording()
        self.btn_rec.setText("开始录制")
        self.btn_rec.setStyleSheet(f"background-color: {DARK_THEME['danger']}; color: #fff; font-weight: bold;")
        self._broadcast_recording_status()
        if frames:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = "_32bit" if data_format == FORMAT_FLOAT32 else ""
            filename = f"REC_{ts}{suffix}.wav"
            filepath = self.record_dir / filename
            if self.audio_engine.save_wav(frames, str(filepath), data_format, sample_rate):
                self._add_file_to_list(filename, ts)
        else:
            self.log_message("录制时间太短或无数据", "WARNING")

    def _remote_toggle_recording(self):
        if not self.is_server_running:
            self.log_message("服务未运行，无法控制录制", "WARNING")
            return
        if not self.is_recording:
            if self.connected_clients <= 0:
                self.log_message("远程录制失败：未检测到手机连接", "WARNING")
                self._broadcast_recording_status()
                return
            self.is_recording = True
            self.audio_engine.start_recording()
            self.btn_rec.setText("停止录制")
            self.btn_rec.setStyleSheet(f"background-color: {DARK_THEME['warning']}; color: #000; font-weight: bold;")
            self.recording_start_time = time.time()
            self._update_rec_timer()
            self.log_message("手机端触发开始录制", "SUCCESS")
        else:
            self.is_recording = False
            frames, data_format, sample_rate = self.audio_engine.stop_recording()
            self.btn_rec.setText("开始录制")
            self.btn_rec.setStyleSheet(f"background-color: {DARK_THEME['danger']}; color: #fff; font-weight: bold;")
            if frames:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                suffix = "_32bit" if data_format == FORMAT_FLOAT32 else ""
                filename = f"REC_{ts}{suffix}.wav"
                filepath = self.record_dir / filename
                if self.audio_engine.save_wav(frames, str(filepath), data_format, sample_rate):
                    self._add_file_to_list(filename, ts)
            self.log_message("手机端触发停止录制", "SUCCESS")
        self._broadcast_recording_status()

    def _broadcast_recording_status(self):
        try:
            self.broadcast_queue.put({'type': 'recording_status', 'data': {'is_recording': self.is_recording}})
        except Exception as e:
            self.log_message(f"广播录制状态失败: {e}", "ERROR")

    def _update_rec_timer(self):
        if self.is_recording:
            elapsed = int(time.time() - self.recording_start_time)
            self.lbl_rec_time.setText(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
            QTimer.singleShot(1000, self._update_rec_timer)
        else:
            self.lbl_rec_time.setText("00:00")

    def _update_rec_button_state(self):
        if self.is_server_running and len(self.mic_active_clients) > 0:
            self.btn_rec.setEnabled(True)
        else:
            self.btn_rec.setEnabled(False)

    # ==================== SocketIO 回调（由 routes.py 调用） ====================

    def on_connect(self, remote_addr, sid):
        self.connected_clients += 1
        self.log_message(f"手机已连接: {remote_addr} (当前连接: {self.connected_clients})", "SUCCESS")
        if self.connected_clients == 1 and self.chk_realtime_wf.isChecked():
            self.schedule_ui(self.realtime_waveform.start)

    def on_disconnect(self, remote_addr, sid):
        self.connected_clients = max(0, self.connected_clients - 1)
        if sid in self.mic_active_clients:
            self.mic_active_clients.discard(sid)
            self.schedule_ui(self._update_rec_button_state)
        self.log_message(f"手机已断开: {remote_addr} (当前连接: {self.connected_clients})", "WARNING")
        if self.connected_clients == 0:
            self.schedule_ui(self.realtime_waveform.stop)

    def on_toggle_recording(self):
        self._remote_toggle_recording()

    def on_mic_status_changed(self, sid, is_open):
        if is_open:
            self.mic_active_clients.add(sid)
            self.log_message(f"客户端 {sid} 麦克风已开启 (活跃: {len(self.mic_active_clients)})", "SUCCESS")
        else:
            self.mic_active_clients.discard(sid)
            self.log_message(f"客户端 {sid} 麦克风已关闭 (活跃: {len(self.mic_active_clients)})", "WARNING")
        self.schedule_ui(self._update_rec_button_state)

    def refresh_file_list(self):
        self.file_tree.clear()
        self._load_existing_records()
        self.log_message("文件列表已刷新", "INFO")

    # ==================== 音频播放 ====================

    def _toggle_play_pause(self):
        """切换播放/暂停"""
        if not self.audio_player.is_playing:
            # 当前未播放，开始播放
            self._play_audio()
        elif self.audio_player.is_paused:
            # 当前暂停中，恢复播放
            self._play_audio()
        else:
            # 当前播放中，暂停
            self._pause_audio()

    def _play_audio(self):
        if self.audio_player.play():
            self.btn_play_pause.setText("⏸ 暂停")
            self.btn_play_pause.setEnabled(True)
            self.btn_stop.setEnabled(True)
            self.waveform_viz.start_animation()
            self._start_play_update()
            self.log_message("开始播放音频", "INFO")

    def _pause_audio(self):
        if self.audio_player.pause():
            self.btn_play_pause.setText("▶ 播放")
            self.btn_play_pause.setEnabled(True)
            self.waveform_viz.stop_animation()

    def _stop_audio(self):
        self.audio_player.stop()
        self.btn_play_pause.setText("▶ 播放")
        self.btn_play_pause.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.slider_progress.setValue(0)
        self.lbl_time.setText("00:00")
        self.waveform_viz.update_play_position(0)
        self.waveform_viz.stop_animation()
        if self.play_update_timer:
            self.play_update_timer.stop()
            self.play_update_timer = None

    def _start_play_update(self):
        self.play_update_timer = QTimer(self)
        self.play_update_timer.timeout.connect(self._update_play_progress)
        self.play_update_timer.start(10)

    def _update_play_progress(self):
        if self.audio_player.is_playing and not self.audio_player.is_paused:
            progress = self.audio_player.get_progress()
            self.slider_progress.blockSignals(True)
            self.slider_progress.setValue(int(progress * 1000))
            self.slider_progress.blockSignals(False)
            ct = self.audio_player.get_current_time()
            self.lbl_time.setText(f"{int(ct // 60):02d}:{int(ct % 60):02d}")
            self.waveform_viz.update_play_position(progress)
        elif not self.audio_player.is_playing and not self.audio_player.is_paused:
            self.btn_play_pause.setText("▶ 播放")
            self.btn_play_pause.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.slider_progress.setValue(0)
            self.lbl_time.setText("00:00")
            self.waveform_viz.update_play_position(0)
            if self.play_update_timer:
                self.play_update_timer.stop()

    def _on_progress_change(self, value):
        if self.audio_player.wav_file or hasattr(self.audio_player, 'float32_file'):
            progress = value / 1000.0
            target = int(progress * self.audio_player.total_frames)
            self.audio_player.seek(target)
            self.waveform_viz.update_play_position(progress)

    def _on_waveform_click(self, progress):
        if self.audio_player.wav_file or hasattr(self.audio_player, 'float32_file'):
            self.slider_progress.blockSignals(True)
            self.slider_progress.setValue(int(progress * 1000))
            self.slider_progress.blockSignals(False)
            target = int(progress * self.audio_player.total_frames)
            self.audio_player.seek(target)
            ct = self.audio_player.get_current_time()
            self.lbl_time.setText(f"{int(ct // 60):02d}:{int(ct % 60):02d}")
            self.waveform_viz.update_play_position(progress)

    # ==================== 文件管理 ====================

    def _load_existing_records(self):
        try:
            wav_files = sorted(self.record_dir.glob("*.wav"), key=lambda x: x.stat().st_mtime, reverse=True)
            for wf in wav_files:
                filename = wf.name
                if filename.startswith("REC_") and len(filename) >= 19:
                    ts_str = filename[4:19]
                    try:
                        ft = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        ft = datetime.fromtimestamp(wf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    ft = datetime.fromtimestamp(wf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                self.file_tree.addTopLevelItem(QTreeWidgetItem([filename, ft]))
            if wav_files:
                self.log_message(f"已加载 {len(wav_files)} 个录音文件", "INFO")
        except Exception as e:
            self.log_message(f"加载录音文件失败: {e}", "ERROR")

    def _add_file_to_list(self, name, timestamp):
        ft = datetime.strptime(timestamp, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        self.file_tree.insertTopLevelItem(0, QTreeWidgetItem([name, ft]))

    def _on_file_select(self):
        items = self.file_tree.selectedItems()
        if items:
            filename = items[0].text(0)
            filepath = self.record_dir / filename
            if filepath.exists():
                self._load_and_play_file(str(filepath), auto_play=False)
                self.waveform_viz.setFocus()

    def _on_file_double_click(self, item):
        filename = item.text(0)
        filepath = self.record_dir / filename
        if filepath.exists():
            self._load_and_play_file(str(filepath), auto_play=True)

    def _load_and_play_file(self, filepath, auto_play=True):
        if self.audio_player.is_playing:
            self.audio_player.stop()
        if self.audio_player.load_file(filepath):
            self.waveform_viz.load_waveform(filepath)
            self.lbl_current_file.setText(Path(filepath).name)
            self.lbl_current_file.setStyleSheet(f"color: {DARK_THEME['text']}; font-size: 11px;")
            dur = self.audio_player.get_duration()
            self.lbl_duration.setText(f"{int(dur // 60):02d}:{int(dur % 60):02d}")
            self.btn_play_pause.setText("▶ 播放")
            self.btn_play_pause.setEnabled(True)
            self.btn_stop.setEnabled(False)
            if auto_play:
                self._play_audio()
                self.waveform_viz.setFocus()
            else:
                self.waveform_viz.update_play_position(0)
                self.slider_progress.setValue(0)
                self.lbl_time.setText("00:00")

    def _open_record_dir(self):
        path = str(self.record_dir)
        if sys.platform == "darwin":
            os.system(f'open "{path}"')
        elif sys.platform == "win32":
            os.startfile(path)
        else:
            os.system(f'xdg-open "{path}"')

    def _change_record_dir(self):
        new_dir = QFileDialog.getExistingDirectory(self, "选择录制目录", str(self.record_dir))
        if not new_dir:
            return
        self.record_dir = Path(new_dir)
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.config["record_dir"] = str(self.record_dir)
        self._save_config()
        self.file_tree.clear()
        self._load_existing_records()
        self.log_message(f"录制目录已更改为: {self.record_dir}", "SUCCESS")

    def _save_as_file(self):
        items = self.file_tree.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请先从列表中选择一个录音文件")
            return
        filename = items[0].text(0)
        src = self.record_dir / filename
        dst, _ = QFileDialog.getSaveFileName(self, "另存为", filename, "WAV files (*.wav)")
        if dst:
            try:
                shutil.copy2(src, dst)
                self.log_message(f"文件已另存为: {dst}", "SUCCESS")
            except Exception as e:
                self.log_message(f"另存为失败: {e}", "ERROR")

    def _on_trash_changed(self):
        self.config["delete_to_trash"] = self.chk_trash.isChecked()
        self._save_config()

    # ==================== 实时波形控制 ====================

    def _on_realtime_wf_toggle(self):
        enabled = self.chk_realtime_wf.isChecked()
        self.config["enable_realtime_waveform"] = enabled
        self._save_config()
        if enabled and self.is_server_running and self.connected_clients > 0:
            self.realtime_waveform.start()
        elif not enabled:
            self.realtime_waveform.stop()
        self.log_message(f"实时波形显示{'已启用' if enabled else '已禁用'}", "INFO")

    def _on_wf_duration_changed(self, text):
        try:
            duration = int(text)
            self.config["waveform_duration"] = duration
            self._save_config()
            self.realtime_waveform.set_duration(duration)
        except ValueError:
            pass

    def _update_realtime_waveform(self, audio_data):
        if self.realtime_waveform and self.chk_realtime_wf.isChecked():
            self.realtime_waveform.update_data(audio_data)
        if hasattr(self, 'level_meter'):
            self.level_meter.update_level(audio_data)

    # ==================== QR 码 ====================

    def _update_qr_code(self, url):
        try:
            self.edit_url.setText(url)
            qr = qrcode.QRCode(version=1, box_size=4, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_img.resize((150, 150))
            # PIL Image -> QPixmap
            data = qr_img.convert("RGBA").tobytes("raw", "RGBA")
            qimage = QImage(data, 150, 150, QImage.Format_RGBA8888)
            self.qr_label.setPixmap(QPixmap.fromImage(qimage))
            self.qr_label.setText("")
        except Exception as e:
            self.log_message(f"二维码生成失败: {e}", "ERROR")

    # ==================== IP 列表 ====================

    def _on_ip_selection_changed(self):
        items = self.ip_tree.selectedItems()
        if items:
            url = items[0].text(0)
            if url != self.edit_url.text():
                self._update_qr_code(url)
                self.log_message(f"切换连接地址: {url}", "INFO")

    def _on_ip_double_click(self, item):
        url = item.text(0)
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(url)
        QMessageBox.information(self, "成功", f"连接地址已复制到剪贴板：\n{url}")
        self.log_message(f"已复制地址: {url}", "SUCCESS")

    # ==================== 驱动下载 ====================

    def _open_driver_website(self):
        system = platform.system()
        if system == "Darwin":
            url = "https://existential.audio/blackhole/"
            name = "BlackHole"
        else:
            url = "https://vb-audio.com/Cable/"
            name = "VB-CABLE Virtual Audio Device"
        try:
            webbrowser.open(url)
            self.log_message(f"正在打开 {name} 下载页面...", "INFO")
        except Exception as e:
            self.log_message(f"打开浏览器失败: {e}", "ERROR")

    # ==================== 快捷键 ====================

    def keyPressEvent(self, event):
        if not self.audio_player.wav_file and not hasattr(self.audio_player, 'float32_file'):
            return super().keyPressEvent(event)

        key = event.key()
        if key == Qt.Key_Space:
            self._toggle_play_pause()
        elif key == Qt.Key_Left:
            progress = max(0.0, self.audio_player.get_progress() - 0.05)
            self._on_waveform_click(progress)
        elif key == Qt.Key_Right:
            progress = min(1.0, self.audio_player.get_progress() + 0.05)
            self._on_waveform_click(progress)
        else:
            super().keyPressEvent(event)

    # ==================== 关闭 ====================

    def closeEvent(self, event):
        if self.is_recording:
            reply = QMessageBox.question(self, "警告", "正在录制中，确定要退出吗？",
                                          QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                event.ignore()
                return

        self.waveform_viz.stop_animation()
        if self.audio_player.is_playing:
            self.audio_player.stop()
        self.audio_engine.close()
        self.audio_player.close()
        self._save_config()
        event.accept()
        os._exit(0)


class _RouteContext:
    """路由上下文，桥接 MainWindow 和 server/routes.py"""
    def __init__(self, win: MainWindow):
        self._win = win
        self.flask_app = win.flask_app
        self.socketio = win.socketio
        self.audio_engine = win.audio_engine
        self.config = win.config
        self.broadcast_queue = win.broadcast_queue

    @property
    def record_dir(self):
        return self._win.record_dir

    @property
    def is_recording(self):
        return self._win.is_recording

    @property
    def connected_clients(self):
        return self._win.connected_clients

    @property
    def mic_active_clients(self):
        return self._win.mic_active_clients

    def log(self, msg, level="INFO"):
        self._win.log_message(msg, level)

    def schedule_ui(self, fn):
        self._win.schedule_ui(fn)

    def on_connect(self, remote_addr, sid):
        self._win.on_connect(remote_addr, sid)

    def on_disconnect(self, remote_addr, sid):
        self._win.on_disconnect(remote_addr, sid)

    def on_toggle_recording(self):
        self._win.on_toggle_recording()

    def on_mic_status_changed(self, sid, is_open):
        self._win.on_mic_status_changed(sid, is_open)

    def refresh_file_list(self):
        self._win.refresh_file_list()

