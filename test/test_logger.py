# test_logger.py
# 统一日志系统测试：文件名、格式、级别、会话名、保留策略
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import importlib
import os
import tempfile
import io
from contextlib import redirect_stdout
from datetime import date, timedelta


def _assert(condition, label):
    """记录测试断言结果，并输出对应的通过或失败信息。
    :param condition: 断言是否成立的布尔条件。
    :param label: 用于日志或测试输出的说明标签。
    :return: 返回函数处理得到的结果。
    """
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


failures = 0

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ["AGENT_LOG_DIR"] = tmpdir

    import utils.logger as logger_module

    logger_module = importlib.reload(logger_module)
    logger_module._reset_for_tests()

    today = date.today()
    old_log = os.path.join(tmpdir, f"{today - timedelta(days=31)}.log")
    keep_log = os.path.join(tmpdir, f"{today - timedelta(days=29)}.log")
    with open(old_log, "w", encoding="utf-8") as f:
        f.write("old")
    with open(keep_log, "w", encoding="utf-8") as f:
        f.write("keep")

    logger = logger_module.get_logger("test.module")
    logger.debug("debug detail", extra={"session_name": "会话A"})
    logger.info("info detail", extra={"session_name": "会话A"})

    log_path = os.path.join(tmpdir, f"{today}.log")
    failures += _assert(os.path.exists(log_path), "创建当天日志文件 logs/YYYY-MM-DD.log")

    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()

    failures += _assert("[DEBUG] [test.module] [会话A] debug detail" in content, "文件记录 DEBUG 且格式含模块和会话名")
    failures += _assert("[INFO] [test.module] [会话A] info detail" in content, "文件记录 INFO")
    failures += _assert(not os.path.exists(old_log), "清理 30 天前日志")
    failures += _assert(os.path.exists(keep_log), "保留最近 30 天日志")

    no_session_logger = logger_module.get_logger("test.no_session")
    no_session_logger.info("no session")
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    failures += _assert("[INFO] [test.no_session] [-] no session" in content, "未传 session_name 时使用 -")

    stdout_buffer = io.StringIO()
    with redirect_stdout(stdout_buffer):
        logger_module._reset_for_tests()
        hidden_logger = logger_module.get_logger("test.hidden_console")
        logger_module.set_console_logging_enabled(False)
        hidden_logger.info("hidden from cli", extra={"session_name": "对话一"})
    failures += _assert("hidden from cli" not in stdout_buffer.getvalue(), "关闭控制台日志后 stdout 不显示日志")

    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    failures += _assert("[INFO] [test.hidden_console] [对话一] hidden from cli" in content, "关闭控制台日志后文件日志仍写入")

    logger_module._reset_for_tests()

os.environ.pop("AGENT_LOG_DIR", None)

print("=" * 60)
print(f"  通过: {8 - failures}  失败: {failures}  总计: 8")
raise SystemExit(1 if failures else 0)
