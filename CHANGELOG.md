# Changelog

All notable changes to this project will be documented in this file.

**English** | [中文](CHANGELOG_CN.md)

---

## [1.0.3] - 2026-02-21

### New Features

- **Auto Update Check** - Automatically checks GitHub for new releases on startup, shows non-intrusive notification in footer
- **GitHub Button** - Quick access to project repository from the footer bar
- **Author Attribution** - Footer bar now displays author name and version info

## [1.0.2] - 2026-02-21

### Improvements

- **UI Simplification** - Removed redundant URL edit box above the connection address list

## [1.0.1] - 2026-02-21

### Improvements

- **Cross-platform Config Directory** - Config files now stored in system standard directories (Windows: `%APPDATA%`, macOS: `~/Library/Application Support`, Linux: `~/.config`)
- **Flask 3.1 Compatibility** - Fixed session property compatibility between Flask 3.1 and Flask-SocketIO
- **Version Display** - Show current version number in window title bar
- **Dependency Pinning** - Pinned all dependency versions in requirements.txt, added cryptography dependency
- **Build Configuration** - Added macOS/Windows PyInstaller spec files, Inno Setup installer script and packaging assets

## [1.0.0] - 2026-02-19

### Initial Release

- **Real-time Audio Streaming** - Stream audio from any device to PC via WiFi LAN
- **Virtual Microphone** - Output to VB-Cable / BlackHole for use in any application
- **Auto Resampling** - Automatically adapts to different device sample rates
- **Recording** - Local and remote recording control, WAV format output
- **Waveform Visualization** - Audio waveform display with click-to-seek
- **Real-time Waveform** - Live audio level visualization during streaming
- **Remote File Management** - Browse, play, download, delete recordings from phone
- **QR Code Connection** - Scan to connect with zero configuration
- **Auto HTTPS** - Self-signed certificate for browser microphone access
- **Device Selection** - Audio output device selection with refresh
- **Theme Switching** - Dark and light theme support
- **Keyboard Shortcuts** - Space play/pause, arrow keys seek
- **Safe Delete** - Trash or permanent delete option
- **Modular Architecture** - Clean separation into audio, server, UI, and web modules