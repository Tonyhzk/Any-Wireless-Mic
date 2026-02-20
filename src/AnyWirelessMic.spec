# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# 项目根目录
src_dir = os.path.abspath('.')

# 强制收集 engineio/socketio 的所有子模块（动态导入 PyInstaller 无法自动检测）
engineio_imports = collect_submodules('engineio')
socketio_imports = collect_submodules('socketio')
eventlet_imports = collect_submodules('eventlet')
dns_imports = collect_submodules('dns')  # eventlet 依赖 dnspython

a = Analysis(
    ['main.py'],
    pathex=[src_dir],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('web', 'web'),
    ],
    hiddenimports=engineio_imports + socketio_imports + eventlet_imports + dns_imports + [
        'flask',
        'flask_socketio',
        'simple_websocket',
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'pyaudio',
        'qrcode',
        'PIL',
        'OpenSSL',
        'matplotlib',
        'numpy',
        'send2trash',
        'scipy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'llvmlite', 'numba',
        'pyarrow',
        'cv2', 'opencv-python',
        'transformers', 'huggingface_hub', 'tokenizers', 'safetensors',
        'datasets',
        'pandas',
        'botocore', 'boto3', 'awscli', 's3transfer',
        'psycopg2',
        'lxml',
        'IPython', 'jedi', 'parso',
        'fastapi', 'uvicorn', 'uvloop', 'starlette',
        'pydantic', 'pydantic_core',
        'tiktoken',
        'primp',
        'beautifulsoup4', 'bs4',
        'aiohttp',
        'pytest',
        'tkinter', '_tkinter',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtTest',
        'PySide6.QtVirtualKeyboard', 'PySide6.QtPdf',
        'PySide6.QtDBus',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Any Wireless Mic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='assets/icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Any Wireless Mic',
)

app = BUNDLE(
    coll,
    name='Any Wireless Mic.app',
    icon='assets/icon.icns',
    bundle_identifier='com.anywirelessmic.app',
    info_plist={
        'CFBundleName': 'Any Wireless Mic',
        'CFBundleDisplayName': 'Any Wireless Mic',
        'CFBundleShortVersionString': '1.0.3',
        'CFBundleVersion': '1.0.3',
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription': 'This app needs microphone access to function as a wireless microphone.',
        'NSLocalNetworkUsageDescription': 'This app needs local network access to connect devices.',
    },
)