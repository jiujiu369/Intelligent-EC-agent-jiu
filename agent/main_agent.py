# agent/main_agent.py

import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
    
import json
from typing import List, Dict
from utils.api_monitor import llm_client
from tools.schema import tool_schemas, func_mapping


SYSTEM_PROMPT = """
你是电商商家智能客服助手。
你拥有一系列工具，可以查询商品、库存、订单、创建售后工单、检索客服知识库。
严格遵循规则：
1. 用户问题需要外部数据时，必须调用对应工具获取真实信息，禁止编造数据；
2. 知识库政策优先参考rag_search检索出来的文档内容；
3. 一次尽量不要并行调用多个工具，分步查询；
4. 回答简洁友好，使用中文，不要输出多余思考过程。
"""


def run_agent(user_query: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query}
    ]

    max_loop = 5  # 限制最大工具调用轮次，防止无限循环
    loop_times = 0

    while loop_times < max_loop:
        loop_times += 1
        # 请求云端大模型，传入工具清单
        llm_result = llm_client.chat_completion(
            messages=messages,
            tools=tool_schemas
        )
        choice = llm_result["choices"][0]
        message = choice["message"]

        # 情况1：不需要调用工具，直接输出回答
        if "tool_calls" not in message or message["tool_calls"] is None:
            return message["content"]

        # 情况2：需要调用工具
        messages.append(message)
        for tool_call in message["tool_calls"]:
            func_name = tool_call["function"]["name"]
            func_args = json.loads(tool_call["function"]["arguments"])
            print(f"\n🔧 执行工具: {func_name}, 参数:{func_args}")

            # 通过映射表拿到真实函数
            target_func = func_mapping.get(func_name)
            if target_func is None:
                tool_content = f"错误：不存在工具 {func_name}"
            else:
                # 执行工具
                tool_return = target_func(**func_args)
                tool_content = json.dumps(tool_return, ensure_ascii=False)

            # 将工具执行结果加入消息队列，传给LLM
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": func_name,
                "content": tool_content
            })

    return "已达到最大工具调用轮次，无法完成查询"


# 本地调试入口"""

if __name__ == "__main__":
    while True:
        question = input("\n请输入客服问题(exit退出)：")
        if question == "exit":
            break
        answer = run_agent(question)
        print(f"\n🤖客服回答：{answer}")
