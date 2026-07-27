# test_edge_cases.py
# 异常场景专项测试，可独立运行。
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import os
import sys
import tempfile
import threading
import types

fake_rag = types.ModuleType("embedding.rag_pipeline")
fake_rag.rag_search = lambda *args, **kwargs: []
sys.modules["embedding.rag_pipeline"] = fake_rag

from agent.main_agent import clear_memory, get_session_path, load_memory, save_memory
from tools.error_handler import atomic_load_json, atomic_save_json, validate_user_input
from utils.api_monitor import CloudLLMClient
import utils.api_monitor as api_monitor


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


def test_broken_memory_file():
    session_name = "edge_broken_memory"
    session_path = get_session_path(session_name)
    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    with open(session_path, "w", encoding="utf-8") as f:
        f.write("{bad json")
    memory = load_memory(session_name)
    _assert(memory == [], "会话记忆文件损坏时 load_memory 安全返回空列表")
    with open(session_path, "r", encoding="utf-8") as f:
        _assert(f.read().strip() == "[]", "损坏会话文件被重建为空 JSON")
    if os.path.exists(session_path):
        os.remove(session_path)


def test_concurrent_json_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "data.json")

        def worker(idx):
            atomic_save_json(path, [{"idx": idx}])

        threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        data = atomic_load_json(path)
        _assert(isinstance(data, list) and len(data) == 1 and "idx" in data[0], "并发写入 JSON 后文件仍可正常读取")


def test_long_input():
    result = validate_user_input("查订单" + "很长" * 300)
    _assert(result.ok, "超长输入仍允许继续处理")
    _assert(result.text is not None and len(result.text) == 500, "超长输入被截断到 500 字符")
    _assert(bool(result.message), "超长输入返回提示消息")


def test_api_timeout():
    old_post = api_monitor.requests.post

    def fake_post(*args, **kwargs):
        raise api_monitor.requests.exceptions.Timeout()

    try:
        api_monitor.requests.post = fake_post
        client = CloudLLMClient(
            api_key="test",
            base_url="https://example.invalid/v1",
            model_name="test-model",
            timeout=1,
            max_retry=0,
        )
        result = client.chat_completion([{"role": "user", "content": "hello"}])
        content = result["choices"][0]["message"]["content"]
        _assert("繁忙" in content, "API 超时模拟返回系统繁忙降级响应")
    finally:
        api_monitor.requests.post = old_post


def test_role_switch_context_clear():
    session_name = "edge_role_switch"
    save_memory([
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ], session_name)
    _assert(load_memory(session_name) != [], "角色切换前存在会话上下文")
    clear_memory(session_name)
    save_memory([], session_name)
    _assert(load_memory(session_name) == [], "角色切换后上下文被清空")
    session_path = get_session_path(session_name)
    if os.path.exists(session_path):
        os.remove(session_path)


print("=" * 60)
print("  test_edge_cases.py")
print("=" * 60)
for name, case in [
    ("会话记忆文件损坏", test_broken_memory_file),
    ("并发写入 JSON 数据文件", test_concurrent_json_write),
    ("超长输入", test_long_input),
    ("API 超时模拟", test_api_timeout),
    ("角色切换后上下文清空", test_role_switch_context_clear),
]:
    _run_case(name, case)

print("=" * 60)
print(f"  通过: {_pass}  失败: {_fail}  总计: {_pass + _fail}")
raise SystemExit(0 if _fail == 0 else 1)
