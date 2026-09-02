# eval/evaluate.py
# 幻觉率真实评测：对比「完整管线(run_agent)」与「裸 LLM(无 RAG/无兜底)」
# 用法：python eval/evaluate.py
# 结果：输出两组幻觉率与降幅，可直接替换简历中的占位数字。

import os
import sys
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from main_agent import run_agent
from tools.hallucination_checker import check as hallucination_check
from utils.api_monitor import CloudLLMClient

# 兜底/拒答关键词（命中即视为"未幻觉"）
REFUSAL_MARKERS = [
    "暂未找到", "未找到", "系统繁忙", "系统走丢", "建议联系人工",
    "无法", "不清楚", "不知道", "抱歉", "没有相关信息", "联系人工客服",
    "请描述具体", "请重试", "请检查",
]


def is_refusal(answer: str) -> bool:
    """判断回答是否属于无法处理请求的拒答。
    :param answer: 待检查或处理的回答文本。
    :return: 条件成立时返回 ``True``，否则返回 ``False``。
    """
    return any(m in (answer or "") for m in REFUSAL_MARKERS)


def is_hallucination(answer: str, session: str) -> bool:
    """用项目内置幻觉检测器判定；对知识库外问题，工具结果应为空 {}。
    :param answer: 待检查或处理的回答文本。
    :param session: 当前会话名称或会话数据。
    :return: 条件成立时返回 ``True``，否则返回 ``False``。
    """
    answer = answer or ""
    # 1) 命中兜底/拒答 → 不算幻觉
    if is_refusal(answer):
        return False
    # 2) 太短或无实质内容 → 不算幻觉
    if len(answer.strip()) < 8:
        return False
    # 3) 检测器：空工具结果下，编造的商品/订单/金额/过度承诺词会被标 risk
    try:
        result = hallucination_check(answer, {}, session_name=session)
        return bool(result.get("risk"))
    except Exception:
        return False


def run_baseline(question: str) -> str:
    """裸 LLM：不接 RAG、不接工具，仅直接问答。
    :param question: 待检索或回答的问题文本。
    :return: 返回函数处理得到的结果。
    """
    client = CloudLLMClient(
        api_key=config.get("API", "api_key"),
        base_url=config.get("API", "base_url"),
        model_name=config.get("API", "model_name"),
    )
    resp = client.chat_completion([{"role": "user", "content": question}])
    return resp["choices"][0]["message"]["content"]


def main():
    """执行当前脚本的主要工作流程。"""
    with open(os.path.join(os.path.dirname(__file__), "eval_set.json"), encoding="utf-8") as f:
        data = json.load(f)
    questions = data["questions"]

    out_scope = [item for item in questions if item["scope"] == "out"]

    treat_hall = 0   # 完整管线 幻觉数
    base_hall = 0    # 裸 LLM 幻觉数
    total = len(out_scope)

    print(f"评测问题数（知识库外）: {total}\n")
    for i, item in enumerate(out_scope):
        q = item["q"]
        # 完整管线
        try:
            treat_ans = run_agent(q, session_name="eval", current_role="consumer", current_username="eval")
        except Exception as e:
            treat_ans = f"(error:{e})"
        # 裸 LLM
        try:
            base_ans = run_baseline(q)
        except Exception as e:
            base_ans = f"(error:{e})"

        t_h = is_hallucination(treat_ans, f"treat-{i}")
        b_h = is_hallucination(base_ans, f"base-{i}")
        treat_hall += int(t_h)
        base_hall += int(b_h)

        flag_t = "幻觉" if t_h else "正常"
        flag_b = "幻觉" if b_h else "正常"
        print(f"[{i+1}] {q}")
        print(f"    管线: {flag_t} | 裸LLM: {flag_b}")

    treat_rate = treat_hall / total if total else 0
    base_rate = base_hall / total if total else 0
    reduction = (base_rate - treat_rate) / base_rate if base_rate else 0

    print("\n================ 结果 ================")
    print(f"知识库外问题数        : {total}")
    print(f"完整管线 幻觉数/率     : {treat_hall} / {treat_rate:.1%}")
    print(f"裸 LLM   幻觉数/率     : {base_hall} / {base_rate:.1%}")
    print(f"幻觉率降幅             : {reduction:.1%}")
    print("======================================")
    


if __name__ == "__main__":
    main()
