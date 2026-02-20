# 当前项目简介

```markdown
Any Wireless Mic - 局域网无线麦克风工具
将任意支持浏览器的设备变成电脑的无线麦克风
技术栈: Flask + SocketIO + PyAudio + PySide6
```

# 版本号管理

更新版本号时需同步修改以下三处：

- `VERSION` — 项目根目录版本文件
- `src/config.py` — `APP_VERSION` 常量
- `src/AnyWirelessMic.spec` — `CFBundleShortVersionString` 和 `CFBundleVersion`

# 必读文档

- **当前项目简介**：

  ```bash
  README.md / README_CN.md
  ```
- **身份与角色**：

  ```bash
  Python 桌面应用 + Web 前端项目
  ```
- **编码规范**：

  ```bash
  PEP 8, Python 3.7+
  ```