import logging
import os
import sys
import threading
from datetime import date, datetime, timedelta
from typing import Optional

import config


_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LOGGER_LOCK = threading.Lock()
_CONFIGURED = False
_RETENTION_DAYS = 30


class _SessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """过滤不需要写入日志的记录。
        :param record: 传入 ``record`` 的业务数据。
        :return: 返回函数处理得到的结果。
        """
        if not hasattr(record, "session_name"):
            record.session_name = "-"
        return True


class _DailyFileHandler(logging.Handler):
    def __init__(self, log_dir: str, level: int = logging.DEBUG):
        """初始化对象所需的状态和依赖。
        :param log_dir: 传入 ``log_dir`` 的业务数据。
        :param level: 传入 ``level`` 的业务数据。
        """
        super().__init__(level=level)
        self.log_dir = log_dir
        self._current_date: Optional[date] = None
        self._handler: Optional[logging.FileHandler] = None
        self._lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        """执行 ``emit`` 对应的项目处理逻辑。
        :param record: 传入 ``record`` 的业务数据。
        """
        with self._lock:
            try:
                self._ensure_handler()
                if self._handler:
                    self._handler.emit(record)
            except Exception:
                self.handleError(record)

    def setFormatter(self, fmt: logging.Formatter) -> None:
        """执行 ``setFormatter`` 对应的项目处理逻辑。
        :param fmt: 传入 ``fmt`` 的业务数据。
        """
        super().setFormatter(fmt)
        if self._handler:
            self._handler.setFormatter(fmt)

    def close(self) -> None:
        """执行 ``close`` 对应的项目处理逻辑。"""
        with self._lock:
            if self._handler:
                self._handler.close()
                self._handler = None
        super().close()

    def _ensure_handler(self) -> None:
        """执行 ``_ensure_handler`` 对应的项目处理逻辑。"""
        today = date.today()
        if self._handler and self._current_date == today:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        if self._handler:
            self._handler.close()
        log_path = os.path.join(self.log_dir, f"{today.isoformat()}.log")
        self._handler = logging.FileHandler(log_path, encoding="utf-8")
        self._handler.setLevel(self.level)
        self._handler.addFilter(_SessionFilter())
        if self.formatter:
            self._handler.setFormatter(self.formatter)
        self._current_date = today
        _cleanup_old_logs(self.log_dir)


def get_logger(name: str) -> logging.Logger:
    """获取已应用项目统一配置的日志记录器。
    :param name: 目标对象的名称。
    :return: 返回完成读取、构建或转换后的结果。
    """
    _configure_logging()
    return logging.getLogger(name)


def set_console_logging_enabled(enabled: bool) -> None:
    """启用或关闭日志的终端输出。
    :param enabled: 传入 ``enabled`` 的业务数据。
    """
    _configure_logging()
    level = logging.INFO if enabled else logging.CRITICAL + 1
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if getattr(handler, "_agent_console_handler", False):
            handler.setLevel(level)


def _configure_logging() -> None:
    """按照项目配置初始化日志级别、格式和输出处理器。"""
    global _CONFIGURED
    with _LOGGER_LOCK:
        if _CONFIGURED:
            return

        _ensure_stdout_encoding_safe()

        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] [%(session_name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(_SessionFilter())
        console_handler._agent_logger_handler = True  # type: ignore[attr-defined]
        console_handler._agent_console_handler = True  # type: ignore[attr-defined]

        file_handler = _DailyFileHandler(_get_log_dir(), level=logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_SessionFilter())
        file_handler._agent_logger_handler = True  # type: ignore[attr-defined]

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        _CONFIGURED = True


def _ensure_stdout_encoding_safe() -> None:
    """执行 ``_ensure_stdout_encoding_safe`` 对应的项目处理逻辑。"""
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def _get_log_dir() -> str:
    """执行 ``_get_log_dir`` 对应的项目处理逻辑。
    :return: 返回完成读取、构建或转换后的结果。
    """
    log_dir = config.get("PATHS", "log_dir")
    if os.path.isabs(log_dir):
        return log_dir
    return os.path.join(_PROJECT_ROOT, log_dir)


def _cleanup_old_logs(log_dir: str) -> None:
    """执行 ``_cleanup_old_logs`` 对应的项目处理逻辑。
    :param log_dir: 传入 ``log_dir`` 的业务数据。
    """
    cutoff = date.today() - timedelta(days=_RETENTION_DAYS)
    if not os.path.exists(log_dir):
        return
    for filename in os.listdir(log_dir):
        if not filename.endswith(".log"):
            continue
        try:
            file_date = datetime.strptime(filename[:-4], "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                os.remove(os.path.join(log_dir, filename))
            except OSError:
                pass


def _reset_for_tests() -> None:
    """重置日志模块状态，供自动化测试重新初始化。"""
    global _CONFIGURED
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_agent_logger_handler", False):
            root_logger.removeHandler(handler)
            handler.close()
    _CONFIGURED = False
