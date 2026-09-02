# test_agent_error_integration.py
# Agent 异常处理集成测试：入口过滤、工具参数缺失、会话损坏恢复
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import os
import sys
import types

fake_rag = types.ModuleType("embedding.rag_pipeline")
fake_rag.rag_search = lambda *args, **kwargs: []
sys.modules["embedding.rag_pipeline"] = fake_rag

import main_agent


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


class FakeLLM:
    def __init__(self, responses):
        """初始化对象所需的状态和依赖。
        :param responses: 传入 ``responses`` 的业务数据。
        """
        self.responses = list(responses)

    def chat_completion(self, messages, tools=None, temperature=0.1, session_name="-"):
        """调用大模型聊天接口，并记录请求指标与重试信息。
        :param messages: 传入 ``messages`` 的业务数据。
        :param tools: 传入 ``tools`` 的业务数据。
        :param temperature: 传入 ``temperature`` 的业务数据。
        :param session_name: 用于隔离上下文的会话名称。
        :return: 返回函数处理得到的结果。
        """
        return self.responses.pop(0)


failures = 0

failures += _assert("请输入" in main_agent.run_agent("   ", use_memory=False), "空白输入不会进入 LLM")
failures += _assert("业务问题" in main_agent.run_agent("你好", use_memory=False), "闲聊输入返回业务引导")

old_llm = main_agent.llm_client
try:
    main_agent.llm_client = FakeLLM([{
        "choices": [{"message": {"role": "assistant", "content": "   "}}]
    }])
    blank_answer = main_agent.run_agent("查询水杯", use_memory=False)
    failures += _assert(
        "模型返回空内容" in blank_answer,
        "模型返回纯空白时给出明确提示而不是空气泡",
    )
finally:
    main_agent.llm_client = old_llm

old_llm = main_agent.llm_client
try:
    main_agent.llm_client = FakeLLM([
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "update_goods",
                                    "arguments": "{\"goods_id\":\"SP001\"}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "缺少必要参数：update_info，请补充后再试",
                    }
                }
            ]
        },
    ])
    answer = main_agent.run_agent("帮我修改 SP001", use_memory=False)
    failures += _assert("update_info" in answer, "工具调用前校验 required 参数")
finally:
    main_agent.llm_client = old_llm

session_name = "error_handler_broken_session"
session_path = main_agent.get_session_path(session_name)
os.makedirs(os.path.dirname(session_path), exist_ok=True)
with open(session_path, "w", encoding="utf-8") as f:
    f.write("{bad json")
memory = main_agent.load_memory(session_name)
failures += _assert(memory == [], "损坏会话文件读取时恢复为空")
with open(session_path, "r", encoding="utf-8") as f:
    failures += _assert(f.read().strip() == "[]", "损坏会话文件被重建")
os.remove(session_path)

print("=" * 60)
print(f"  通过: {6 - failures}  失败: {failures}  总计: 6")
raise SystemExit(1 if failures else 0)
