# test_context_window.py
# 上下文窗口与多用户会话隔离测试，可独立运行。
import os
import sys
import tempfile
import types

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

fake_rag = types.ModuleType("embedding.rag_pipeline")
fake_rag.rag_search = lambda *args, **kwargs: []
sys.modules["embedding.rag_pipeline"] = fake_rag

import agent.main_agent as main_agent
from tools.rbac import ROLE_CONSUMER, ROLE_MERCHANT


_pass, _fail = 0, 0


def _assert(condition, label):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  [PASS] {label}")
    else:
        _fail += 1
        print(f"  [FAIL] {label}")


def _run_case(label, fn):
    try:
        fn()
    except Exception as exc:
        _assert(False, f"{label} -> {type(exc).__name__}: {exc}")


def test_user_scoped_session_memory_isolated():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_memory_dir = main_agent.MEMORY_DIR
        main_agent.MEMORY_DIR = tmpdir
        try:
            main_agent.save_memory(
                [{"role": "user", "content": "user1 的问题"}],
                "对话一",
                username="user1",
                role=ROLE_CONSUMER,
            )
            main_agent.save_memory(
                [{"role": "user", "content": "admin 的问题"}],
                "对话一",
                username="admin",
                role=ROLE_MERCHANT,
            )

            user_memory = main_agent.load_memory("对话一", username="user1", role=ROLE_CONSUMER)
            admin_memory = main_agent.load_memory("对话一", username="admin", role=ROLE_MERCHANT)
            user_path = main_agent.get_session_path("对话一", username="user1", role=ROLE_CONSUMER)
            admin_path = main_agent.get_session_path("对话一", username="admin", role=ROLE_MERCHANT)

            _assert(user_memory[0]["content"] == "user1 的问题", "消费者读取自己的同名会话")
            _assert(admin_memory[0]["content"] == "admin 的问题", "商家读取自己的同名会话")
            _assert(user_path != admin_path, "不同用户同名会话保存到不同文件")
        finally:
            main_agent.MEMORY_DIR = old_memory_dir


def test_recent_context_window_shows_last_five_current_user_messages():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_memory_dir = main_agent.MEMORY_DIR
        main_agent.MEMORY_DIR = tmpdir
        try:
            messages = [{"role": "system", "content": "旧摘要"}]
            for idx in range(1, 8):
                messages.append({"role": "user", "content": f"问题{idx}"})
            messages.append({"role": "assistant", "content": None})
            main_agent.save_memory(messages, "售后咨询", username="user1", role=ROLE_CONSUMER)

            recent = main_agent.get_recent_chat_records(
                "售后咨询",
                username="user1",
                role=ROLE_CONSUMER,
                limit=5,
            )
            formatted = main_agent.format_recent_chat_records(recent)

            _assert([item["content"] for item in recent] == ["问题3", "问题4", "问题5", "问题6", "问题7"], "最近上下文窗口只返回最后五条有效聊天记录")
            _assert("问题2" not in formatted and "问题7" in formatted, "格式化输出只展示窗口内记录")
        finally:
            main_agent.MEMORY_DIR = old_memory_dir


print("=" * 60)
print("  test_context_window.py")
print("=" * 60)
for name, case in [
    ("多用户同名会话隔离", test_user_scoped_session_memory_isolated),
    ("最近五条上下文窗口", test_recent_context_window_shows_last_five_current_user_messages),
]:
    _run_case(name, case)

print("=" * 60)
print(f"  通过: {_pass}  失败: {_fail}  总计: {_pass + _fail}")
raise SystemExit(0 if _fail == 0 else 1)
