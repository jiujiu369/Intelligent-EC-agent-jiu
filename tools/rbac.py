# tools/rbac.py
# 角色权限控制模块（Role-Based Access Control）
# ============================================================
# 设计原则：
#   1. 双层防护 —— ① 传给 LLM 的 Function Schema 仅含当前角色允许的工具
#                  ② 代码执行工具前二次校验，越权直接拦截
#   2. 数据脱敏 —— 消费者查询商品时隐藏内部经营敏感字段
#   3. 角色切换 —— 切换角色时自动清空对话上下文缓存
#   4. 复用现有工具函数，不改动 data_loader / rag_pipeline 源码
# ============================================================

from typing import List, Dict, Set, Any
from utils.logger import get_logger

logger = get_logger(__name__)

# ====================== 角色定义 ======================
ROLE_CONSUMER = "consumer"    # 消费者
ROLE_MERCHANT = "merchant"    # 商家

ALL_ROLES = {ROLE_CONSUMER, ROLE_MERCHANT}

# ====================== 权限白名单 ======================
# 每个角色允许调用的工具名称集合
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    ROLE_CONSUMER: {
        "rag_search",
        "query_goods",
        "query_stock",
        "query_order",
        "create_aftersale_ticket",
    },
    ROLE_MERCHANT: {
        "rag_search",
        "query_goods",
        "query_stock",
        "query_order",
        "create_aftersale_ticket",
        "update_goods",
        "export_sales_report",
    },
}

# ====================== 数据脱敏配置 ======================
# 消费者查询商品时需要隐藏的内部经营敏感字段
CONSUMER_MASKED_GOODS_FIELDS: Set[str] = {
    "上架状态",  # 商品上下架状态属于内部运营管理数据，消费者无需感知
}


# ====================== 核心函数 ======================

def get_allowed_tools(role: str) -> Set[str]:
    """
    获取指定角色允许使用的工具名称集合。
    未知角色返回空集合（最小权限原则）。
    """
    return ROLE_PERMISSIONS.get(role, set())


def get_filtered_schemas(role: str, all_schemas: List[Dict]) -> List[Dict]:
    """
    【第一层防护】按角色过滤 Function Schema。
    仅返回当前角色允许调用的工具定义，从源头阻止 LLM 生成越权工具调用。
    """
    allowed = get_allowed_tools(role)
    return [
        schema for schema in all_schemas
        if schema.get("function", {}).get("name") in allowed
    ]


def check_permission(func_name: str, role: str) -> bool:
    """
    【第二层防护】代码执行前的权限二次校验。
    即使 LLM 绕过 Schema 限制生成了越权调用，此处也会拦截。
    返回 True 表示允许执行，False 表示越权。
    """
    allowed = func_name in get_allowed_tools(role)
    if not allowed:
        logger.warning(f"越权拦截 role={role} tool={func_name}")
    return allowed


def get_permission_denied_msg(func_name: str, role: str) -> str:
    """
    生成权限不足的提示信息（返回给 LLM 作为 tool 执行结果）。
    """
    return f"权限不足：当前角色[{role}]无权调用工具[{func_name}]。可用工具：{', '.join(sorted(get_allowed_tools(role)))}"


def mask_goods_data(result: List[Dict], role: str) -> List[Dict]:
    """
    对 query_goods 的返回结果进行数据脱敏。
    消费者角色隐藏内部经营敏感字段（如上架状态），商家角色不脱敏。
    """
    if role != ROLE_CONSUMER:
        return result
    masked: List[Dict] = []
    for item in result:
        masked_item = {
            k: v for k, v in item.items()
            if k not in CONSUMER_MASKED_GOODS_FIELDS
        }
        masked.append(masked_item)
    return masked


def mask_tool_result(func_name: str, result: Any, role: str) -> Any:
    """
    工具执行结果脱敏分发器。
    根据工具名称和角色决定是否对返回数据做脱敏处理。
    目前仅 query_goods 需要脱敏；后续扩展只需在此函数增加分支。
    """
    if func_name == "query_goods":
        return mask_goods_data(result, role)
    return result


def get_role_prompt_suffix(role: str) -> str:
    """
    生成追加到系统提示词的角色信息，让 LLM 感知当前用户角色。
    """
    role_labels = {
        ROLE_CONSUMER: "消费者（普通买家）",
        ROLE_MERCHANT: "商家（店铺管理员）",
    }
    label = role_labels.get(role, role)
    allowed = ", ".join(sorted(get_allowed_tools(role)))
    return (
        f"\n5. 当前用户角色为【{label}】，你只能使用以下工具：{allowed}。"
        f"消费者无法修改商品信息或查看销售报表，如用户提出此类需求请礼貌说明权限不足。"
    )
