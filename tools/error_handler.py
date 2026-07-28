import functools
import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)


EMPTY_INPUT_MESSAGE = "请输入问题后再发送。"
INPUT_TOO_LONG_MESSAGE = "输入内容较长，已截断前 500 字继续处理。"
INPUT_REJECT_MESSAGE = "输入内容无法识别，请换一种方式描述业务问题。"
CHAT_GUIDE_MESSAGE = "您好，我可以帮您查询商品、订单或售后政策，请描述具体业务问题。"
BUSY_MESSAGE = "系统繁忙，请稍后再试"
LOST_MESSAGE = "系统走丢了，请重试"
NOT_FOUND_MESSAGE = "未找到匹配信息"
RAG_FALLBACK_MESSAGE = "暂未找到相关知识，建议联系人工客服"
API_KEY_INVALID_MESSAGE = "API 密钥失效，请检查配置后重试"
API_BALANCE_MESSAGE = "API 余额不足，请充值后重试"
MAX_INPUT_LENGTH = 500

_CHAT_PHRASES = {
    "你好",
    "您好",
    "在吗",
    "谢谢",
    "感谢",
    "再见",
    "拜拜",
    "您好在吗",
    "你好在吗",
}

_JSON_LOCKS: Dict[str, threading.Lock] = {}
_JSON_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class InputValidationResult:
    ok: bool
    text: Optional[str] = None
    message: Optional[str] = None


def validate_user_input(raw_input: str) -> InputValidationResult:
    text = "" if raw_input is None else str(raw_input)
    stripped = text.strip()
    if not stripped:
        return InputValidationResult(False, message=EMPTY_INPUT_MESSAGE)

    if _unprintable_ratio(text) > 0.3 or _max_same_char_run_ratio(stripped) > 0.8:
        return InputValidationResult(False, message=INPUT_REJECT_MESSAGE)

    normalized = stripped.replace(" ", "")
    if len(normalized) <= 8 and normalized in _CHAT_PHRASES:
        return InputValidationResult(False, message=CHAT_GUIDE_MESSAGE)

    if len(stripped) > MAX_INPUT_LENGTH:
        return InputValidationResult(True, text=stripped[:MAX_INPUT_LENGTH], message=INPUT_TOO_LONG_MESSAGE)

    return InputValidationResult(True, text=stripped)


def _unprintable_ratio(text: str) -> float:
    if not text:
        return 0.0
    unprintable_count = sum(1 for ch in text if not ch.isprintable() and ch not in "\r\n\t")
    return unprintable_count / len(text)


def _max_same_char_run_ratio(text: str) -> float:
    if not text:
        return 0.0
    max_run = 1
    current_run = 1
    previous = text[0]
    for ch in text[1:]:
        if ch == previous:
            current_run += 1
        else:
            max_run = max(max_run, current_run)
            current_run = 1
            previous = ch
    max_run = max(max_run, current_run)
    return max_run / len(text)


def validate_tool_args(tool_name: str, func_args: Dict[str, Any], tool_schemas: List[Dict]) -> Dict[str, Any]:
    required = _get_required_args(tool_name, tool_schemas)
    missing = [name for name in required if _is_missing(func_args.get(name))]
    if missing:
        return {
            "ok": False,
            "msg": f"缺少必要参数：{', '.join(missing)}，请补充后再试",
        }
    return {"ok": True}


def _get_required_args(tool_name: str, tool_schemas: List[Dict]) -> List[str]:
    for schema in tool_schemas:
        func_info = schema.get("function", {})
        if func_info.get("name") == tool_name:
            params = func_info.get("parameters", {})
            return list(params.get("required", []))
    return []


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (dict, list, tuple, set)) and len(value) == 0:
        return True
    return False


def wrap_tool_result(tool_name: str, result: Any) -> Any:
    if tool_name in {"query_goods", "query_order", "query_stock"} and result == []:
        return {"status": "fail", "msg": NOT_FOUND_MESSAGE}
    return result


def safe_tool_call(func: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            return {"status": "fail", "msg": LOST_MESSAGE}

    return wrapper


def filter_rag_results(results: List[Dict], distance_threshold: float) -> List[Dict]:
    filtered = []
    for item in results or []:
        distance = item.get("distance")
        if distance is None or distance <= distance_threshold:
            filtered.append(item)
    if not filtered:
        return rag_fallback_result()
    return filtered


def rag_fallback_result() -> List[Dict]:
    return [{"text": RAG_FALLBACK_MESSAGE, "meta": {"fallback": True}, "distance": None}]


def llm_fallback_response(message: str = BUSY_MESSAGE) -> Dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": message,
                }
            }
        ]
    }


def safe_rag_call(func: Callable[..., List[Dict]]) -> Callable[..., List[Dict]]:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            return rag_fallback_result()

    return wrapper


def atomic_load_json(file_path: str) -> List[Dict]:
    with _get_file_lock(file_path):
        try:
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.error(f"JSON读取异常 path={file_path} error={exc}")
            raise


def atomic_save_json(file_path: str, data: List[Dict]) -> None:
    with _get_file_lock(file_path):
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error(f"JSON写入异常 path={file_path} error={exc}")
            raise


def _get_file_lock(file_path: str) -> threading.Lock:
    normalized_path = os.path.abspath(file_path)
    with _JSON_LOCKS_GUARD:
        if normalized_path not in _JSON_LOCKS:
            _JSON_LOCKS[normalized_path] = threading.Lock()
        return _JSON_LOCKS[normalized_path]


def recover_memory_file(file_path: str) -> List[Dict]:
    try:
        if not os.path.exists(file_path):
            atomic_save_json(file_path, [])
            return []
        data = atomic_load_json(file_path)
        if isinstance(data, list):
            return data
    except Exception:
        logger.error(f"会话文件损坏，自动重建 path={file_path}")
    atomic_save_json(file_path, [])
    return []


def summarize_memory(
    messages: List[Dict],
    max_rounds: int,
    summary_keep_rounds: int,
) -> Tuple[Optional[Dict], List[Dict]]:
    max_items = max_rounds * 2
    keep_items = summary_keep_rounds * 2
    if len(messages) <= max_items:
        return None, messages

    older = messages[:-keep_items]
    kept = messages[-keep_items:]
    parts = []
    for msg in older:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", "")).strip()
        if content:
            parts.append(f"{role}: {content}")
    summary_text = "历史摘要：\n" + "\n".join(parts)
    return {"role": "system", "content": summary_text}, kept
