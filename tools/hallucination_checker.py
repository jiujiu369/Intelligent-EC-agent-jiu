import re
import threading
from typing import Any, Dict, List, Set

from utils.logger import get_logger

logger = get_logger(__name__)

BLACKLIST_WORDS = ("绝对", "肯定", "保证", "100%")
PRODUCT_SUFFIXES = ("摄像头", "耳机", "手表", "手机", "电脑", "键盘", "鼠标", "电视", "冰箱", "空调")
RISK_THRESHOLD = 0.3

_SESSION_STATS: Dict[str, Dict[str, int]] = {}
_STATS_LOCK = threading.Lock()


def check(answer: str, tool_results: Any, session_name: str = "-") -> Dict:
    """检查回答是否包含与工具事实不一致的高风险内容。
    :param answer: 待检查或处理的回答文本。
    :param tool_results: 传入 ``tool_results`` 的业务数据。
    :param session_name: 用于隔离上下文的会话名称。
    :return: 返回函数处理得到的结果。
    """
    answer = answer or ""
    known = _collect_known_values(tool_results)
    issues: List[Dict] = []

    for word in BLACKLIST_WORDS:
        if word in answer:
            issues.append({"type": "blacklist", "value": word, "msg": "包含过度承诺词"})

    for order_id in _extract_order_ids(answer):
        if order_id not in known["order_ids"]:
            issues.append({"type": "order_id", "value": order_id, "msg": "订单号未出现在工具返回数据中"})

    for amount in _extract_amounts(answer):
        if not _amount_exists(amount, known["amounts"]):
            issues.append({"type": "amount", "value": amount, "msg": "金额未出现在工具返回数据中"})

    for product_name in _extract_product_names(answer):
        if not _product_exists(product_name, known["product_names"]):
            issues.append({"type": "product_name", "value": product_name, "msg": "商品名未出现在工具返回数据中"})

    score = _score_issues(issues)
    risk = score > RISK_THRESHOLD
    _record_session_result(session_name, risk)
    if risk:
        logger.warning(f"幻觉风险 score={score} issue_count={len(issues)}", extra={"session_name": session_name})
    return {
        "score": score,
        "risk": risk,
        "issues": issues,
        "session_stats": get_session_stats(session_name),
    }


def get_session_stats(session_name: str = "-") -> Dict:
    """读取指定会话累计的风险检查统计。
    :param session_name: 用于隔离上下文的会话名称。
    :return: 返回完成读取、构建或转换后的结果。
    """
    with _STATS_LOCK:
        stats = _SESSION_STATS.get(session_name, {"total": 0, "risk_count": 0})
        total = stats["total"]
        risk_count = stats["risk_count"]
    return {
        "total": total,
        "risk_count": risk_count,
        "risk_ratio": round(risk_count / total, 4) if total else 0,
    }


def reset_session_stats(session_name: str = "-") -> None:
    """清空指定会话累计的风险检查统计。
    :param session_name: 用于隔离上下文的会话名称。
    """
    with _STATS_LOCK:
        _SESSION_STATS.pop(session_name, None)


def _record_session_result(session_name: str, risk: bool) -> None:
    """累加指定会话的回答风险统计。
    :param session_name: 用于隔离上下文的会话名称。
    :param risk: 传入 ``risk`` 的业务数据。
    """
    with _STATS_LOCK:
        stats = _SESSION_STATS.setdefault(session_name, {"total": 0, "risk_count": 0})
        stats["total"] += 1
        if risk:
            stats["risk_count"] += 1


def _collect_known_values(data: Any) -> Dict[str, Set[str]]:
    """执行 ``_collect_known_values`` 对应的项目处理逻辑。
    :param data: 需要保存或处理的数据。
    :return: 返回函数处理得到的结果。
    """
    values = {"product_names": set(), "order_ids": set(), "amounts": set()}

    def walk(node: Any, key: str = "") -> None:
        """执行 ``walk`` 对应的项目处理逻辑。
        :param node: 需要检查的 AST 函数定义节点。
        :param key: 用于定位配置、缓存或数据项的键。
        """
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
            return
        if isinstance(node, list):
            for item in node:
                walk(item, key)
            return
        text = str(node)
        if key in {"名称", "商品名称", "name", "goods_name"}:
            values["product_names"].add(text)
        if key in {"订单号", "关联订单号", "order_id"}:
            values["order_ids"].add(text.upper())
        if key in {"售价", "实付金额", "金额", "total_sales", "price", "amount"}:
            values["amounts"].add(_normalize_amount(text))

    walk(data)
    return values


def _extract_order_ids(answer: str) -> Set[str]:
    """从回答文本中提取订单编号。
    :param answer: 待检查或处理的回答文本。
    :return: 返回函数处理得到的结果。
    """
    return {item.upper() for item in re.findall(r"\b[A-Z]{1,4}\d{4,}\b", answer, flags=re.IGNORECASE)}


def _extract_amounts(answer: str) -> Set[str]:
    """从回答文本中提取可能的金额。
    :param answer: 待检查或处理的回答文本。
    :return: 返回函数处理得到的结果。
    """
    return {_normalize_amount(item) for item in re.findall(r"(\d+(?:\.\d+)?)\s*元", answer)}


def _extract_product_names(answer: str) -> Set[str]:
    """从回答文本中提取可能的商品名称。
    :param answer: 待检查或处理的回答文本。
    :return: 返回函数处理得到的结果。
    """
    suffix_pattern = "|".join(PRODUCT_SUFFIXES)
    pattern = rf"[\u4e00-\u9fffA-Za-z0-9]{{2,18}}(?:{suffix_pattern})"
    return set(re.findall(pattern, answer))


def _normalize_amount(value: Any) -> str:
    """将金额文本转换为便于比较的标准数值。
    :param value: 需要转换、缓存或检查的值。
    :return: 返回函数处理得到的结果。
    """
    try:
        return str(round(float(str(value).replace(",", "")), 2))
    except (TypeError, ValueError):
        return str(value)


def _amount_exists(amount: str, known_amounts: Set[str]) -> bool:
    """执行 ``_amount_exists`` 对应的项目处理逻辑。
    :param amount: 传入 ``amount`` 的业务数据。
    :param known_amounts: 传入 ``known_amounts`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    return amount in known_amounts


def _product_exists(product_name: str, known_products: Set[str]) -> bool:
    """判断提取出的商品名称是否能在已知数据中找到。
    :param product_name: 传入 ``product_name`` 的业务数据。
    :param known_products: 传入 ``known_products`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    return any(product_name == known or product_name in known or known in product_name for known in known_products)


def _score_issues(issues: List[Dict]) -> float:
    """根据检测到的问题类型计算回答风险分数。
    :param issues: 传入 ``issues`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    score = 0.0
    for issue in issues:
        if issue["type"] == "blacklist":
            score += 0.2
        else:
            score += 0.35
    return min(1.0, round(score, 2))
