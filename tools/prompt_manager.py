from typing import Dict, Optional


ROLE_CONSUMER = "consumer"
ROLE_MERCHANT = "merchant"

PROMPT_TEMPLATES: Dict[str, object] = {
    "BASE_SYSTEM": (
        "你是电商客服智能助手，负责帮助用户查询商品、订单、售后工单和客服知识库。"
        "请始终使用中文，回答简洁、准确、友好。"
    ),
    "ROLE_CONSUMER": (
        "当前用户角色为买家。买家可以查询商品、订单和售后政策，"
        "可以创建售后工单；无权查看库存、无权修改商品信息，也无权查看销售报表。"
    ),
    "ROLE_MERCHANT": (
        "当前用户角色为商家（店铺管理员）。商家拥有全部管理权限，"
        "可以查询和管理商品、库存、订单、售后工单，并可查看销售报表。"
    ),
    "TOOL_CONSTRAINTS": (
        "Function Call 约束：用户问题需要外部数据时必须调用对应工具；"
        "禁止编造工具参数；调用工具前必须确认必填项完整；"
        "不得连续调用同一工具超过 2 次；一次尽量不要并行调用多个工具。"
    ),
    "ANTI_HALLUCINATION": (
        "反幻觉约束：所有商品、价格、库存、订单、售后和政策数据必须来自工具返回；"
        "不得自行编造商品名、价格、订单号、库存数量或售后处理结果。"
    ),
    "ERROR_FALLBACK": {
        "api_failure": "API 失败：系统繁忙，请稍后再试",
        "rag_empty": "RAG 无结果：暂未找到相关知识，建议联系人工客服",
        "tool_error": "工具异常：系统走丢了，请重试",
        "default": "系统走丢了，请重试",
    },
}


def build_system_prompt(role: str, session_context: Optional[str] = None) -> str:
    parts = [
        str(PROMPT_TEMPLATES["BASE_SYSTEM"]),
        get_role_description(role),
        str(PROMPT_TEMPLATES["TOOL_CONSTRAINTS"]),
        str(PROMPT_TEMPLATES["ANTI_HALLUCINATION"]),
        _format_error_fallback(),
    ]
    if session_context:
        parts.append(str(session_context).strip())
    return "\n\n".join(part for part in parts if part)


def get_role_description(role: str) -> str:
    role_key = {
        ROLE_CONSUMER: "ROLE_CONSUMER",
        ROLE_MERCHANT: "ROLE_MERCHANT",
    }.get(role, "ROLE_CONSUMER")
    return str(PROMPT_TEMPLATES[role_key])


def get_error_message(error_type: str) -> str:
    error_fallback = PROMPT_TEMPLATES["ERROR_FALLBACK"]
    if not isinstance(error_fallback, dict):
        return "系统走丢了，请重试"
    return str(error_fallback.get(error_type, error_fallback["default"]))


def _format_error_fallback() -> str:
    error_fallback = PROMPT_TEMPLATES["ERROR_FALLBACK"]
    if not isinstance(error_fallback, dict):
        return ""
    return (
        "异常兜底话术："
        f"{error_fallback['api_failure']}；"
        f"{error_fallback['rag_empty']}；"
        f"{error_fallback['tool_error']}。"
    )
