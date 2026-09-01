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
        """模块加载后显式设置的模型环境变量覆盖当前配置。"""
        reloaded = importlib.reload(config)
        overrides = {
            "AGENT_RAG_PRIMARY_MODEL": "test-primary-model",
            "AGENT_RAG_FALLBACK_MODEL": "test-fallback-model",
        }
        with patch.dict(os.environ, overrides, clear=False):
            self.assertEqual(
                reloaded.get("RAG", "embedding_model"), "test-primary-model"
            )
            self.assertEqual(
                reloaded.get("RAG", "fallback_model"), "test-fallback-model"
            )
if __name__ == "__main__":
    unittest.main(verbosity=2)
