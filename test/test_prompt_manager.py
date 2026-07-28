# test_prompt_manager.py
# 提示词管理测试：模板集中维护、动态拼接、角色片段、异常话术、Agent 集成引用
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import os
import sys
import types

from tools.rbac import ROLE_CONSUMER, ROLE_MERCHANT
from tools.prompt_manager import (
    PROMPT_TEMPLATES,
    build_system_prompt,
    get_error_message,
    get_role_description,
)


def _assert(condition, label):
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


failures = 0

required_keys = {
    "BASE_SYSTEM",
    "ROLE_CONSUMER",
    "ROLE_MERCHANT",
    "TOOL_CONSTRAINTS",
    "ANTI_HALLUCINATION",
    "ERROR_FALLBACK",
}
failures += _assert(required_keys.issubset(PROMPT_TEMPLATES.keys()), "提示词模板集中存储为字典常量")

consumer_prompt = build_system_prompt(ROLE_CONSUMER)
failures += _assert("电商客服智能助手" in consumer_prompt, "BASE_SYSTEM 被拼接")
failures += _assert("买家" in consumer_prompt and "无权修改商品" in consumer_prompt and "查看库存" in consumer_prompt and "查看销售报表" in consumer_prompt, "买家角色约束被拼接")
failures += _assert("不得连续调用同一工具超过 2 次" in consumer_prompt, "工具调用约束被拼接")
failures += _assert("不得自行编造商品名" in consumer_prompt, "反幻觉约束被拼接")
failures += _assert("API 失败" in consumer_prompt and "RAG 无结果" in consumer_prompt and "工具异常" in consumer_prompt, "异常兜底话术被拼接")

merchant_prompt = build_system_prompt(ROLE_MERCHANT, session_context="历史摘要：用户关注 SP001")
failures += _assert("拥有全部管理权限" in merchant_prompt, "商家角色描述被拼接")
failures += _assert(merchant_prompt.rstrip().endswith("历史摘要：用户关注 SP001"), "session_context 追加到末尾")

failures += _assert("买家" in get_role_description(ROLE_CONSUMER), "可单独获取买家角色描述")
failures += _assert("店铺管理员" in get_role_description(ROLE_MERCHANT), "可单独获取商家角色描述")
failures += _assert(get_error_message("api_failure") == "API 失败：系统繁忙，请稍后再试", "可按类型获取 API 兜底话术")
failures += _assert(get_error_message("unknown") == "系统走丢了，请重试", "未知异常类型返回通用兜底")

main_agent_path = os.path.join(os.getcwd(), "agent", "main_agent.py")
with open(main_agent_path, "r", encoding="utf-8") as f:
    source = f.read()
failures += _assert("SYSTEM_PROMPT =" not in source, "main_agent.py 删除硬编码 SYSTEM_PROMPT")
failures += _assert("get_role_prompt_suffix" not in source, "main_agent.py 不再依赖 rbac 角色提示词")
failures += _assert("build_system_prompt" in source, "main_agent.py 使用 prompt_manager 构建系统提示词")

fake_rag = types.ModuleType("embedding.rag_pipeline")
fake_rag.rag_search = lambda *args, **kwargs: []
sys.modules["embedding.rag_pipeline"] = fake_rag

import agent.main_agent as main_agent


class FakeLLM:
    def __init__(self):
        self.messages = None

    def chat_completion(self, messages, tools=None, temperature=0.1, session_name="-"):
        self.messages = messages
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


old_llm = main_agent.llm_client
try:
    fake_llm = FakeLLM()
    main_agent.llm_client = fake_llm
    answer = main_agent.run_agent("查询订单 O001", session_name="prompt_manager_test", use_memory=False)
    failures += _assert(answer == "ok", "Agent 正常返回 LLM 答案")
    failures += _assert("电商客服智能助手" in fake_llm.messages[0]["content"], "Agent 注入 prompt_manager 生成的系统提示词")
finally:
    main_agent.llm_client = old_llm
    session_path = main_agent.get_session_path("prompt_manager_test")
    if os.path.exists(session_path):
        os.remove(session_path)

print("=" * 60)
print(f"  通过: {17 - failures}  失败: {failures}  总计: 17")
raise SystemExit(1 if failures else 0)
