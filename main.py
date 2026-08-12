"""usage-widget 悬浮窗监控框架入口。

用法：
    python main.py            # 启动
    python main.py -v         # 调试日志
    python main.py --config path.json   # 指定配置
"""
import argparse
import signal
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="usage-widget", description="悬浮窗监控框架")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    parser.add_argument("-c", "--config", help="配置文件路径")
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication

    from core.app import create_app, setup_logging
    from core.config import Config

    setup_logging(args.verbose)
    config = Config(args.config)
    window = create_app(config)
    window.show()

    app = QApplication.instance()

    def _sigint(*_):
        app.quit()

    try:
        signal.signal(signal.SIGINT, _sigint)
    except (ValueError, OSError):
        pass  # 无控制台环境（pythonw/打包 GUI）不允许注册信号处理
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
