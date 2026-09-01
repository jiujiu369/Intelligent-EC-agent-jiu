"""配置环境变量覆盖测试。"""

import importlib
import os
import sys
import unittest
from unittest.mock import patch


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

import config


class ConfigEnvironmentTests(unittest.TestCase):
    """验证主、备用 RAG 模型使用设计约定的环境变量。"""

    def test_primary_and_fallback_model_environment_variables_override_real_config(self):
        """显式环境变量映射缺失时，真实配置重载不会返回两个覆盖值。"""
        overrides = {
            "AGENT_RAG_PRIMARY_MODEL": "test-primary-model",
            "AGENT_RAG_FALLBACK_MODEL": "test-fallback-model",
        }
        with patch.dict(os.environ, overrides, clear=False):
            reloaded = importlib.reload(config)
            self.assertEqual(
                reloaded.get("RAG", "embedding_model"), "test-primary-model"
            )
            self.assertEqual(
                reloaded.get("RAG", "fallback_model"), "test-fallback-model"
            )

        importlib.reload(config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
