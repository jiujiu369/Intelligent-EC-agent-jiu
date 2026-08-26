# test_error_handler.py
# 异常处理模块测试：输入过滤、工具参数、RAG 降级、会话恢复、文件锁读写
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import json
import os
import tempfile

from tools.error_handler import (
    CHAT_GUIDE_MESSAGE,
    INPUT_REJECT_MESSAGE,
    INPUT_TOO_LONG_MESSAGE,
    NOT_FOUND_MESSAGE,
    RAG_FALLBACK_MESSAGE,
    InputValidationResult,
    atomic_load_json,
    atomic_save_json,
    filter_rag_results,
    recover_memory_file,
    summarize_memory,
    validate_tool_args,
    validate_user_input,
    wrap_tool_result,
)


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

blank = validate_user_input("   ")
failures += _assert(not blank.ok and "请输入" in blank.message, "空白输入被拒绝并提示重新输入")

long_text = "查订单" + ("商品库存售后政策" * 80)
long_result = validate_user_input(long_text)
failures += _assert(long_result.ok and long_result.text is not None, "超长输入仍可继续处理")
failures += _assert(len(long_result.text) == 500, "超长输入被截断到 500 字符")
failures += _assert(long_result.message == INPUT_TOO_LONG_MESSAGE, "超长输入返回截断提示")

noise = validate_user_input("啊" * 100)
failures += _assert(not noise.ok and noise.message == INPUT_REJECT_MESSAGE, "连续重复字符占比过高被拒绝")

control_noise = validate_user_input("\x00" * 10 + "正常问题")
failures += _assert(not control_noise.ok and control_noise.message == INPUT_REJECT_MESSAGE, "不可打印字符占比过高被拒绝")

chat = validate_user_input("你好")
failures += _assert(not chat.ok and chat.message == CHAT_GUIDE_MESSAGE, "闲聊短语被礼貌引导")

valid = validate_user_input("帮我查询订单 O123")
failures += _assert(valid == InputValidationResult(ok=True, text="帮我查询订单 O123"), "业务输入通过校验")

tool_schema = {
    "type": "function",
    "function": {
        "name": "update_goods",
        "parameters": {"required": ["goods_id", "update_info"]},
    },
}
missing = validate_tool_args("update_goods", {"goods_id": "SP001"}, [tool_schema])
failures += _assert(not missing["ok"] and "update_info" in missing["msg"], "工具参数缺失逐项提示")

present = validate_tool_args("update_goods", {"goods_id": "SP001", "update_info": {"售价": 99}}, [tool_schema])
failures += _assert(present["ok"], "工具参数完整时通过")

failures += _assert(wrap_tool_result("query_goods", []) == {"status": "fail", "msg": NOT_FOUND_MESSAGE}, "查询空列表转为未找到提示")
failures += _assert(wrap_tool_result("query_goods", [{"商品ID": "SP001"}]) == [{"商品ID": "SP001"}], "非空查询结果保持原样")

rag_items = [
    {"text": "高相似度", "meta": {}, "distance": 1.2},
    {"text": "低相似度", "meta": {}, "distance": 1.8},
]
filtered = filter_rag_results(rag_items, 1.5)
failures += _assert(len(filtered) == 1 and filtered[0]["text"] == "高相似度", "RAG 低相似度结果被过滤")
failures += _assert(filter_rag_results([], 1.5) == [{"text": RAG_FALLBACK_MESSAGE, "meta": {"fallback": True}, "distance": None}], "RAG 空结果返回降级话术")

messages = []
for i in range(25):
    messages.append({"role": "user", "content": f"问题{i}"})
    messages.append({"role": "assistant", "content": f"回答{i}"})
summary, kept = summarize_memory(messages, max_rounds=20, summary_keep_rounds=10)
failures += _assert(summary and "历史摘要" in summary["content"], "超过轮次时生成摘要")
failures += _assert(len(kept) == 20, "摘要后保留最近 10 轮消息")

with tempfile.TemporaryDirectory() as tmpdir:
    path = os.path.join(tmpdir, "memory.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{bad json")
    recovered = recover_memory_file(path)
    failures += _assert(recovered == [], "损坏会话文件恢复为空列表")
    with open(path, "r", encoding="utf-8") as f:
        failures += _assert(json.load(f) == [], "损坏会话文件被重建为空文件")

    data_path = os.path.join(tmpdir, "data.json")
    atomic_save_json(data_path, [{"id": 1}])
    failures += _assert(atomic_load_json(data_path) == [{"id": 1}], "JSON 文件锁读写正常")

print("=" * 60)
print(f"  通过: {19 - failures}  失败: {failures}  总计: 19")
raise SystemExit(1 if failures else 0)
