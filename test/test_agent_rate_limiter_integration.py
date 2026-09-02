import os
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import sys
import types

from utils.rate_limiter import clear_all_caches

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


class CountingLLM:
    def __init__(self):
        """初始化对象所需的状态和依赖。"""
        self.count = 0

    def chat_completion(self, messages, tools=None, temperature=0.1, session_name="-"):
        """调用大模型聊天接口，并记录请求指标与重试信息。
        :param messages: 传入 ``messages`` 的业务数据。
        :param tools: 传入 ``tools`` 的业务数据。
        :param temperature: 传入 ``temperature`` 的业务数据。
        :param session_name: 用于隔离上下文的会话名称。
        :return: 返回函数处理得到的结果。
        """
        self.count += 1
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"第{self.count}次回答",
                    }
                }
            ]
        }


failures = 0
clear_all_caches()

old_llm = main_agent.llm_client
try:
    fake_llm = CountingLLM()
    main_agent.llm_client = fake_llm
    first = main_agent.run_agent("查询重复缓存测试", session_name="rate_limit_test", use_memory=False)
    second = main_agent.run_agent("查询重复缓存测试", session_name="rate_limit_test", use_memory=False)
    failures += _assert(first == second, "5 秒内重复 query 返回同一缓存答案")
    failures += _assert(fake_llm.count == 1, "重复 query 不再次调用 LLM")
finally:
    main_agent.llm_client = old_llm
    clear_all_caches()
    session_path = main_agent.get_session_path("rate_limit_test")
    if os.path.exists(session_path):
        os.remove(session_path)


print("=" * 60)
print(f"  通过: {2 - failures}  失败: {failures}  总计: 2")
raise SystemExit(1 if failures else 0)
