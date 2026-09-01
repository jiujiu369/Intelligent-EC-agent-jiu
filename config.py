import os
from typing import Any, Dict

# ---------- 自动加载 .env 文件 ----------
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]

    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=True)
except ImportError:
    pass

# ---------- 项目根目录 ----------
ROOT = os.path.dirname(os.path.abspath(__file__))


def _model_path(relative: str) -> str:
    """拼接模型路径：优先使用 .env 覆盖，其次绝对路径兼容，最后相对路径。
    :param relative: 相对于项目根目录的路径。
    :return: 返回函数处理得到的结果。
    """
    env_key = f"AGENT_RAG_{relative.upper().replace('-', '_').replace('.', '_')}"
    env_val = os.environ.get(env_key)
    if env_val and os.path.exists(env_val):
        return env_val
    # 兼容旧版 Windows 绝对路径：如果 models/ 在项目根存在则用相对路径
    candidate = os.path.join(ROOT, "models", relative)
    if os.path.exists(candidate):
        return candidate
    return candidate  # 返回相对路径，由 rag_pipeline 的 _resolve_model_name 兜底


API: Dict[str, Any] = {
    "api_key": "",
    "base_url": "https://api.apikl.ai/v1",
    "model_name": "gpt-5.5",
    "timeout": 60,
    "max_retry": 2,
    "temperature": 0.1,
}

PATHS: Dict[str, Any] = {
    "datas_dir": "datas",
    "docs_dir": "datas/docs",
    "chroma_persist_dir": "datas/chroma_db",
    "chroma_persist_dir_fallback": "datas/chroma_db_fallback_768",
    "agent_memory_dir": "agent_memory",
    "log_dir": "logs",
    "goods_json": "datas/货品基础数据.json",
    "stock_json": "datas/库存数据.json",
    "order_json": "datas/订单数据.json",
    "aftersale_json": "datas/售后工单.json",
    "consumer_users_json": "datas/consumer_users.json",
    "merchant_users_json": "datas/merchant_users.json",
}

RAG: Dict[str, Any] = {
    "embedding_model": _model_path("bge-base-zh-v1.5"),
    "fallback_model": _model_path("bge-base-zh-v1.5"),
    "chunk_size": 384,
    "chunk_overlap": 64,
    "distance_threshold": 1.5,
}

SESSION: Dict[str, Any] = {
    "default_session": "对话一",
    "max_message_rounds": 20,
    "summary_keep_rounds": 10,
}

AGENT: Dict[str, Any] = {
    "max_loop": 5,
    "llm_temperature": 0.1,
}


class Config:
    def __init__(self):
        """初始化对象所需的状态和依赖。"""
        self._sections = {
            "API": API,
            "PATHS": PATHS,
            "RAG": RAG,
            "SESSION": SESSION,
            "AGENT": AGENT,
        }

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """根据键读取配置项、缓存项或集合数据。
        :param section: 传入 ``section`` 的业务数据。
        :param key: 用于定位配置、缓存或数据项的键。
        :param default: 配置项不存在时使用的默认值。
        :return: 返回函数处理得到的结果。
        """
        section_name = section.upper()
        section_data = self._sections.get(section_name, {})
        value = section_data.get(key, default)

        env_value = self._get_env_override(section_name, key)
        if env_value is None:
            return value
        return self._cast_env_value(env_value, value)

    @staticmethod
    def _get_env_override(section: str, key: str) -> Any:
        """执行 ``_get_env_override`` 对应的项目处理逻辑。
        :param section: 传入 ``section`` 的业务数据。
        :param key: 用于定位配置、缓存或数据项的键。
        :return: 返回完成读取、构建或转换后的结果。
        """
        explicit_env_names = {
            ("RAG", "embedding_model"): "AGENT_RAG_PRIMARY_MODEL",
            ("RAG", "fallback_model"): "AGENT_RAG_FALLBACK_MODEL",
        }
        env_names = (
            explicit_env_names.get((section, key)),
            f"AGENT_{key.upper()}",
            f"AGENT_{section}_{key.upper()}",
        )
        for env_name in env_names:
            if env_name and env_name in os.environ:
                return os.environ[env_name]
        return None

    @staticmethod
    def _cast_env_value(env_value: str, default: Any) -> Any:
        """执行 ``_cast_env_value`` 对应的项目处理逻辑。
        :param env_value: 传入 ``env_value`` 的业务数据。
        :param default: 配置项不存在时使用的默认值。
        :return: 返回函数处理得到的结果。
        """
        if isinstance(default, bool):
            return env_value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(default, int) and not isinstance(default, bool):
            return int(env_value)
        if isinstance(default, float):
            return float(env_value)
        return env_value


config = Config()


def get(section: str, key: str, default: Any = None) -> Any:
    """根据键读取配置项、缓存项或集合数据。
    :param section: 传入 ``section`` 的业务数据。
    :param key: 用于定位配置、缓存或数据项的键。
    :param default: 配置项不存在时使用的默认值。
    :return: 返回函数处理得到的结果。
    """
    return config.get(section, key, default)
