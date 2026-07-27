# utils/api_monitor.py

import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

import time
import json
import requests
from typing import List, Dict, Optional
import config
from tools.error_handler import (
    API_BALANCE_MESSAGE,
    API_KEY_INVALID_MESSAGE,
    BUSY_MESSAGE,
    llm_fallback_response,
)
from utils.logger import get_logger
from utils.rate_limiter import rate_limit

logger = get_logger(__name__)


class CloudLLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
        timeout: int = 60,
        max_retry: int = 2
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.timeout = timeout
        self.max_retry = max_retry
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    @rate_limit
    def chat_completion(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        temperature: float = config.get("API", "temperature"),
        session_name: str = "-"
    ):
        """
        调用云端大模型对话接口，支持function call工具入参
        :param messages: 对话历史消息列表
        :param tools: tool_schemas工具列表（传入给LLM）
        :param temperature: 温度，业务Agent建议0.1~0.3
        :return: llm原始返回json
        """
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature
        }
        if tools is not None and len(tools) > 0:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        for attempt in range(self.max_retry + 1):
            try:
                logger.debug(
                    "LLM request=%s token_estimate=%s",
                    json.dumps(_safe_request_log(self.base_url, payload, self.timeout), ensure_ascii=False),
                    _estimate_tokens(payload),
                    extra={"session_name": session_name},
                )
                start_time = time.time()
                resp = requests.post(
                    url=f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout
                )
                cost_time = round(time.time() - start_time, 3)
                logger.info(
                    f"HTTP请求完成 status={resp.status_code} cost={cost_time}s attempt={attempt + 1}",
                    extra={"session_name": session_name},
                )

                resp.raise_for_status()
                resp_json = resp.json()
                logger.debug(
                    "LLM response=%s token_estimate=%s",
                    json.dumps(resp_json, ensure_ascii=False),
                    _estimate_tokens(resp_json),
                    extra={"session_name": session_name},
                )
                return resp_json

            except requests.exceptions.Timeout as e:
                logger.error(
                    f"API请求超时 status=timeout retry={attempt + 1}/{self.max_retry} error={str(e)}",
                    extra={"session_name": session_name},
                )
                if attempt < self.max_retry:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                return llm_fallback_response(BUSY_MESSAGE)

            except requests.exceptions.HTTPError as e:
                status_code = getattr(e.response, "status_code", None)
                if status_code == 401:
                    logger.error("API密钥失效 status=401 retry=%s/%s", attempt + 1, self.max_retry, extra={"session_name": session_name})
                    return llm_fallback_response(API_KEY_INVALID_MESSAGE)
                if status_code == 402:
                    logger.error("API余额不足 status=402 retry=%s/%s", attempt + 1, self.max_retry, extra={"session_name": session_name})
                    return llm_fallback_response(API_BALANCE_MESSAGE)
                if status_code == 429:
                    retry_after = _parse_retry_after(getattr(e.response, "headers", {}))
                    logger.error(
                        f"API请求限流 status=429 retry={attempt + 1}/{self.max_retry} retry_after={retry_after}s",
                        extra={"session_name": session_name},
                    )
                    if attempt < self.max_retry:
                        time.sleep(retry_after)
                        continue
                    return llm_fallback_response(BUSY_MESSAGE)
                if status_code is not None and 500 <= status_code < 600:
                    backoff = min(2 ** attempt, 4)
                    logger.error(
                        f"API服务端异常 status={status_code} retry={attempt + 1}/{self.max_retry} backoff={backoff}s",
                        extra={"session_name": session_name},
                    )
                    if attempt < self.max_retry:
                        time.sleep(backoff)
                        continue
                    return llm_fallback_response(BUSY_MESSAGE)
                logger.error(
                    f"API请求失败 status={status_code} retry={attempt + 1}/{self.max_retry} error={str(e)}",
                    extra={"session_name": session_name},
                )
                return llm_fallback_response(BUSY_MESSAGE)

            except requests.exceptions.RequestException as e:
                logger.error(
                    f"API请求异常 status=unknown retry={attempt + 1}/{self.max_retry} error={str(e)}",
                    extra={"session_name": session_name},
                )
                if attempt < self.max_retry:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                return llm_fallback_response(BUSY_MESSAGE)

        return llm_fallback_response(BUSY_MESSAGE)


def _parse_retry_after(headers: Dict) -> int:
    try:
        return max(0, int(headers.get("Retry-After", 1)))
    except (TypeError, ValueError):
        return 1


def _estimate_tokens(payload) -> int:
    return max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)


def _safe_request_log(base_url: str, payload: Dict, timeout: int) -> Dict:
    return {
        "url": f"{base_url}/chat/completions",
        "headers": {"Authorization": "Bearer ***", "Content-Type": "application/json"},
        "json": payload,
        "timeout": timeout,
    }


# ===================== 实例全局对象（项目统一导入使用） =====================
llm_client = CloudLLMClient(
    api_key=config.get("API", "api_key"),
    base_url=config.get("API", "base_url"),
    model_name=config.get("API", "model_name"),
    timeout=config.get("API", "timeout"),
    max_retry=config.get("API", "max_retry"),
)
