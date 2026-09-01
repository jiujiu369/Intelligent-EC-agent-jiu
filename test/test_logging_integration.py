# test_logging_integration.py
# 日志埋点集成测试：登录不记录密码、RBAC 越权、JSON 读取落盘
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import importlib
import os
import tempfile
from datetime import date


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

    import tools.auth_login as auth_login
    from tools.rbac import check_permission
    from tools import data_loader

    original_consumer_file = auth_login.CONSUMER_FILE
    auth_login.CONSUMER_FILE = os.path.join(tmpdir, "consumer_users.json")
    auth_login.init_auth_files()
    auth_login.register_user(
        auth_login.ROLE_CONSUMER,
        "user1",
        "safe_test_password",
        auth_login.SECURITY_QUESTIONS[0],
        "safe_test_answer",
    )
    auth_login.login_user(auth_login.ROLE_CONSUMER, "user1", "safe_test_password")
    auth_login.login_user(auth_login.ROLE_CONSUMER, "user1", "wrong_password")
    check_permission("update_goods", auth_login.ROLE_CONSUMER)
    data_loader.load_json(data_loader.GOODS_PATH)
    auth_login.CONSUMER_FILE = original_consumer_file

    log_path = os.path.join(tmpdir, f"{date.today().isoformat()}.log")
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()

    failures += _assert("登录成功 role=consumer username=user1" in content, "登录成功写入日志")
    failures += _assert("登录失败 reason=user_or_password_error role=consumer username=user1" in content, "登录失败写入日志")
    failures += _assert("safe_test_password" not in content and "wrong_password" not in content, "日志不记录密码明文")
    failures += _assert("越权拦截 role=consumer tool=update_goods" in content, "RBAC 越权写入 WARNING 日志")
    failures += _assert("JSON读取 path=" in content and "货品基础数据.json" in content, "data_loader JSON 读取写入日志")

    logger_module._reset_for_tests()

os.environ.pop("AGENT_LOG_DIR", None)

print("=" * 60)
print(f"  通过: {5 - failures}  失败: {failures}  总计: 5")
raise SystemExit(1 if failures else 0)
