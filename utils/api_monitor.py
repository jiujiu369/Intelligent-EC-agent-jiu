# utils/api_monitor.py

import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

import time
import requests
from typing import List, Dict, Optional
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

    def chat_completion(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.1
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

        retry_count = 0
        while retry_count <= self.max_retry:
            try:
                start_time = time.time()
                resp = requests.post(
                    url=f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout
                )
                cost_time = round(time.time() - start_time, 3)
                logger.info(f"【LLM API调用耗时】{cost_time}s")

                resp.raise_for_status()
                resp_json = resp.json()
                return resp_json

            except requests.exceptions.RequestException as e:
                retry_count += 1
                logger.error(f"API请求失败，重试 {retry_count}/{self.max_retry}，错误：{str(e)}")
                time.sleep(1)
        raise Exception("云端LLM API多次调用失败，请检查密钥、接口地址、网络")


# ===================== 实例全局对象（项目统一导入使用） =====================
# 使用时修改下面配置，换成你云平台信息
llm_client = CloudLLMClient(
    api_key="sk-c29e461d1a914965b3d7b879acd19c28",
    base_url="https://api.deepseek.com/v1",  # 切换平台只改url
    model_name="deepseek-v4-flash"
)
