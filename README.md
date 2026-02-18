# Any Wireless Mic

Turn any device with a browser into a wireless microphone for your PC via LAN.

![Banner](assets/banner.svg)

**English** | [中文](README_CN.md) | [Changelog](CHANGELOG.md)

---

## Features

![Features](assets/features_en.svg)

### Core Features
- **Real-time Audio Streaming** - Stream audio from any device to your PC via WiFi with adjustable quality (1-100)
- **Virtual Microphone Support** - Output to virtual audio devices (VB-Cable / BlackHole) for use in any application
- **Auto Resampling** - Automatically adapts to different device sample rates
- **Local & Remote Recording** - Record audio on PC, control from phone, save as WAV
- **Waveform Visualization** - View audio waveforms during playback with click-to-seek

### Additional Features
- **Remote File Management** - Browse, play, download, and delete recordings from your phone
- **QR Code Connection** - Scan to connect, zero configuration needed
- **Auto HTTPS** - Self-signed certificate for browser microphone access
- **Device Selection** - Choose audio output device with refresh support
- **Dark / Light Theme** - Modern GUI with theme switching
- **Keyboard Shortcuts** - Space to play/pause, arrow keys to seek
- **Safe Delete** - Delete to trash (default) or permanent delete

---

## System Requirements

| Platform | Minimum Version |
|----------|-----------------|
| Windows | Windows 10+ |
| macOS | macOS 10.15+ |

- Python 3.7+
- A device with a modern browser on the same WiFi network

---

## Installation

```bash
pip install flask flask-socketio eventlet pyaudio ttkbootstrap qrcode pillow pyopenssl matplotlib numpy send2trash scipy
```

### Virtual Audio Cable (Optional but Recommended)

To use as a microphone in other apps (Zoom, OBS, Discord, etc.):

**Windows:** Install [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)

**macOS:** Install [BlackHole](https://github.com/ExistentialAudio/BlackHole)

---

## Quick Start

1. Run the application:
```bash
python src/手机麦克风输入.py
```

2. Select your audio output device (e.g. "CABLE Input") in the GUI
3. Click "Start Service" - a QR code and URL will appear
4. Scan the QR code with your phone's browser (same WiFi network)
5. Accept the self-signed certificate warning, then tap the microphone button
6. Start speaking - audio streams to your PC in real-time

---

## Audio Quality Settings

| Quality | Mode | Buffer | Latency | Use Case |
|---------|------|--------|---------|----------|
| 1-10 | Low Latency | 256 | ~6ms | Real-time calls |
| 11-30 | Smooth | 512 | ~12ms | Daily use |
| 31-50 | Balanced | 1024 | ~23ms | Recommended default |
| 51-75 | Stable | 2048 | ~46ms | Unstable network |
| 76-100 | High Stability | 4096 | ~93ms | Weak network |

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Space | Play / Pause |
| ← | Rewind 5% |
| → | Forward 5% |

---

## Project Structure

```
Any-Wireless-Mic/
├── src/
│   ├── 手机麦克风输入.py      # Main application
│   ├── templates/
│   │   └── index.html         # Web client interface
│   └── 免费版/                # Free version
├── assets/                    # Visual assets
├── README.md                  # English documentation
├── README_CN.md               # Chinese documentation
├── CHANGELOG.md               # English changelog
├── CHANGELOG_CN.md            # Chinese changelog
├── LICENSE                    # Apache License 2.0
└── VERSION                    # Version file
```

---

## Tech Stack

| Category | Technology |
|----------|-----------|
| Backend | Flask + Flask-SocketIO (WebSocket) |
| Frontend | HTML5 + Web Audio API |
| Audio | PyAudio |
| GUI | ttkbootstrap |
| Visualization | Matplotlib |
| Security | pyOpenSSL (self-signed HTTPS) |

---

## License

[Apache License 2.0](LICENSE)

## Author

**Tonyhzk**

- GitHub: [@Tonyhzk](https://github.com/Tonyhzk)
- Project: [Any-Wireless-Mic](https://github.com/Tonyhzk/Any-Wireless-Mic)

<div align="center">

If this project helps you, please give it a Star!

</div>