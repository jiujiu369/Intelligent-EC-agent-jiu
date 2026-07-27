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

    from tools.auth_login import ROLE_CONSUMER, login_user
    from tools.rbac import check_permission
    from tools import data_loader

    login_user(ROLE_CONSUMER, "user1", "123456")
    login_user(ROLE_CONSUMER, "user1", "wrong_password")
    check_permission("update_goods", ROLE_CONSUMER)
    data_loader.load_json(data_loader.GOODS_PATH)

    log_path = os.path.join(tmpdir, f"{date.today().isoformat()}.log")
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()

    failures += _assert("登录成功 role=consumer username=user1" in content, "登录成功写入日志")
    failures += _assert("登录失败 reason=user_or_password_error role=consumer username=user1" in content, "登录失败写入日志")
    failures += _assert("123456" not in content and "wrong_password" not in content, "日志不记录密码明文")
    failures += _assert("越权拦截 role=consumer tool=update_goods" in content, "RBAC 越权写入 WARNING 日志")
    failures += _assert("JSON读取 path=" in content and "货品基础数据.json" in content, "data_loader JSON 读取写入日志")

    logger_module._reset_for_tests()

os.environ.pop("AGENT_LOG_DIR", None)

print("=" * 60)
print(f"  通过: {5 - failures}  失败: {failures}  总计: 5")
raise SystemExit(1 if failures else 0)
