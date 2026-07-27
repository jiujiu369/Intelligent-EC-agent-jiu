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
        if not hasattr(record, "session_name"):
            record.session_name = "-"
        return True


class _DailyFileHandler(logging.Handler):
    def __init__(self, log_dir: str, level: int = logging.DEBUG):
        super().__init__(level=level)
        self.log_dir = log_dir
        self._current_date: Optional[date] = None
        self._handler: Optional[logging.FileHandler] = None
        self._lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            try:
                self._ensure_handler()
                if self._handler:
                    self._handler.emit(record)
            except Exception:
                self.handleError(record)

    def setFormatter(self, fmt: logging.Formatter) -> None:
        super().setFormatter(fmt)
        if self._handler:
            self._handler.setFormatter(fmt)

    def close(self) -> None:
        with self._lock:
            if self._handler:
                self._handler.close()
                self._handler = None
        super().close()

    def _ensure_handler(self) -> None:
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
    _configure_logging()
    return logging.getLogger(name)


def _configure_logging() -> None:
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

        file_handler = _DailyFileHandler(_get_log_dir(), level=logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_SessionFilter())
        file_handler._agent_logger_handler = True  # type: ignore[attr-defined]

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        _CONFIGURED = True


def _ensure_stdout_encoding_safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def _get_log_dir() -> str:
    log_dir = config.get("PATHS", "log_dir")
    if os.path.isabs(log_dir):
        return log_dir
    return os.path.join(_PROJECT_ROOT, log_dir)


def _cleanup_old_logs(log_dir: str) -> None:
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
    global _CONFIGURED
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_agent_logger_handler", False):
            root_logger.removeHandler(handler)
            handler.close()
    _CONFIGURED = False
