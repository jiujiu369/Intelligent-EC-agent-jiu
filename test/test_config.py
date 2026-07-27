# test_config.py
# 配置模块测试：验证默认配置、环境变量覆盖、路径常量来源
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import importlib
import os
import sys


def _assert(condition, label):
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


failures = 0

os.environ.pop("AGENT_API_KEY", None)
os.environ.pop("AGENT_TIMEOUT", None)
sys.modules.pop("config", None)

import config

failures += _assert(config.get("API", "api_key") == "", "api_key 默认不包含硬编码密钥")
failures += _assert(config.get("API", "base_url") == "https://apihub.agnes-ai.com/v1", "base_url 默认值正确")
failures += _assert(config.get("API", "timeout") == 60, "timeout 默认值为 int")
failures += _assert(config.get("PATHS", "goods_json") == "datas/货品基础数据.json", "goods_json 路径来自配置")
failures += _assert(config.get("RAG", "chunk_size") == 384, "chunk_size 默认值正确")
failures += _assert(config.get("SESSION", "default_session") == "对话一", "default_session 默认值正确")
failures += _assert(config.get("AGENT", "max_loop") == 5, "max_loop 默认值正确")

os.environ["AGENT_API_KEY"] = "sk-env-test"
os.environ["AGENT_TIMEOUT"] = "90"
config = importlib.reload(config)

failures += _assert(config.get("API", "api_key") == "sk-env-test", "AGENT_API_KEY 覆盖 api_key")
failures += _assert(config.get("API", "timeout") == 90, "AGENT_TIMEOUT 覆盖 timeout 并保持 int")

os.environ.pop("AGENT_API_KEY", None)
os.environ.pop("AGENT_TIMEOUT", None)

sys.exit(1 if failures else 0)
