"""局域网无线麦克风 - 入口文件"""

import os
import sys
import warnings

# 抑制所有 DeprecationWarning（包括 Eventlet）
warnings.filterwarnings('ignore', category=DeprecationWarning)

# macOS 兼容性修复：在导入其他库之前强制配置 eventlet
if sys.platform == "darwin":
    os.environ['EVENTLET_HUB'] = 'selects'
    try:
        import eventlet
        eventlet.hubs.use_hub('selects')
    except:
        pass

# 确保 src/ 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 修复打包后的 stdout/stderr
class _DummyStream:
    def write(self, data): pass
    def flush(self): pass
    def isatty(self): return False

if sys.stdout is None:
    sys.stdout = _DummyStream()
if sys.stderr is None:
    sys.stderr = _DummyStream()

from PySide6.QtWidgets import QApplication
from config import DARK_STYLESHEET
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
