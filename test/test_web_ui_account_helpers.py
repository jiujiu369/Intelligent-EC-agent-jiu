"""Web UI 账号安全回调测试：认证数据只写入本测试创建的临时文件。"""

import importlib
import os
import sys
import types
import unittest
import uuid
from pathlib import Path


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

import tools.auth_login as auth_login


class _FakeComponent:
    """代替仅在导入期构建界面所需的 Gradio 组件。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def click(self, *args, **kwargs):
        return None

    def submit(self, *args, **kwargs):
        return None


class _FakeGradio(types.SimpleNamespace):
    """提供 web_ui 导入边界实际调用到的 Gradio API。"""

    def update(self, **kwargs):
        return {"__type__": "update", **kwargs}


def _install_import_fakes():
    """屏蔽 Agent、向量库和 Gradio 的重型导入，不替换认证实现。"""
    fake_gradio = _FakeGradio(
        Blocks=_FakeComponent,
        Row=_FakeComponent,
        Column=_FakeComponent,
        Accordion=_FakeComponent,
        State=_FakeComponent,
        Markdown=_FakeComponent,
        Radio=_FakeComponent,
        Textbox=_FakeComponent,
        Button=_FakeComponent,
        Chatbot=_FakeComponent,
        Dropdown=_FakeComponent,
    )
    fake_agent = types.ModuleType("agent.main_agent")
    fake_agent.run_agent = lambda *args, **kwargs: ""
    fake_agent.list_sessions = lambda *args, **kwargs: ["默认会话"]
    fake_agent.clear_memory = lambda *args, **kwargs: None
    fake_agent.clear_all_memory = lambda *args, **kwargs: None
    fake_agent.save_memory = lambda *args, **kwargs: None
    fake_agent.get_recent_chat_records = lambda *args, **kwargs: []
    fake_agent.normalize_session_name = lambda value: value
    fake_agent._next_auto_session = lambda *args, **kwargs: "默认会话"
    fake_agent.DEFAULT_SESSION = "默认会话"
    fake_embedding = types.ModuleType("embedding")
    fake_embedding.rag_pipeline = types.SimpleNamespace()

    return {
        "gradio": fake_gradio,
        "agent.main_agent": fake_agent,
        "embedding": fake_embedding,
    }


class WebUiAccountHelperTests(unittest.TestCase):
    """验证 Web UI 对真实认证流程的角色映射、状态判断和结果提示。"""

    def setUp(self):
        """导入 UI 前将真实认证模块定向到本测试独占的文件。"""
        self.original_consumer_file = auth_login.CONSUMER_FILE
        self.original_merchant_file = auth_login.MERCHANT_FILE
        token = uuid.uuid4().hex
        data_dir = Path(ROOT_PATH) / "datas"
        self.temp_files = [
            data_dir / f"web_ui_auth_{token}_consumer.json",
            data_dir / f"web_ui_auth_{token}_merchant.json",
        ]
        self.addCleanup(self._remove_test_files)
        self.addCleanup(self._restore_auth_paths)
        auth_login.CONSUMER_FILE, auth_login.MERCHANT_FILE = self.temp_files
        auth_login.init_auth_files()

        self.module_backups = {name: sys.modules.get(name) for name in _install_import_fakes()}
        sys.modules.update(_install_import_fakes())
        sys.modules.pop("web_ui", None)
        self.ui = importlib.import_module("web_ui")
        self.addCleanup(self._restore_imports)

    def _restore_auth_paths(self):
        """恢复认证模块原有文件路径。"""
        auth_login.CONSUMER_FILE = self.original_consumer_file
        auth_login.MERCHANT_FILE = self.original_merchant_file

    def _remove_test_files(self):
        """删除本测试创建的独立认证文件。"""
        for file_path in self.temp_files:
            file_path.unlink(missing_ok=True)

    def _restore_imports(self):
        """恢复其他测试可见的导入模块。"""
        sys.modules.pop("web_ui", None)
        for name, module in self.module_backups.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _register(self, role, username, password, question, answer):
        """通过 UI 注册回调建立真实测试账号。"""
        result = self.ui.do_register(role, username, password, question, answer)
        self.assertTrue(result.startswith("✅"), result)

    def test_usage_guides_follow_role_capabilities(self):
        """角色分支错误时，买家和商家的界面说明会混淆。"""
        consumer_guide = self.ui._usage_guide(auth_login.ROLE_CONSUMER)
        merchant_guide = self.ui._usage_guide(auth_login.ROLE_MERCHANT)

        self.assertIn("订单", consumer_guide)
        self.assertIn("售后", consumer_guide)
        self.assertIn("库存", merchant_guide)
        self.assertIn("销售报表", merchant_guide)

    def test_recovery_helpers_map_role_and_report_question_or_wrong_answer(self):
        """找回回调角色映射或错误答案提示被破坏时，本用例会失败。"""
        question = auth_login.SECURITY_QUESTIONS[2]
        self._register("商家", "seller", "oldpass", question, "Taipei")

        question_result = self.ui.do_get_security_question("商家", "seller")
        wrong_answer = self.ui.do_reset_password(
            "商家", "seller", "wrong", "newpass", "newpass"
        )
        reset_result = self.ui.do_reset_password(
            "商家", "seller", "Taipei", "newpass", "newpass"
        )

        self.assertTrue(question_result.startswith("✅"), question_result)
        self.assertIn(question, question_result)
        self.assertTrue(wrong_answer.startswith("❌"), wrong_answer)
        self.assertIn("错误", wrong_answer)
        self.assertTrue(reset_result.startswith("✅"), reset_result)
        self.assertTrue(auth_login.login_user(auth_login.ROLE_MERCHANT, "seller", "newpass")[0])

    def test_logged_in_security_helpers_change_password_and_reject_unauthenticated_calls(self):
        """登录态检查或密码确认处理被删除时，本用例会失败。"""
        question = auth_login.SECURITY_QUESTIONS[0]
        self._register("消费者", "alice", "oldpass", question, "Taipei")
        logged_in_state = {
            "role": auth_login.ROLE_CONSUMER,
            "username": "alice",
            "session": "默认会话",
        }

        changed = self.ui.do_change_password("oldpass", "newpass", "newpass", logged_in_state)
        mismatch = self.ui.do_change_password("newpass", "newpass", "mismatch", logged_in_state)
        unauthenticated_change = self.ui.do_change_password("old", "newpass", "newpass", {})
        unauthenticated_question = self.ui.do_set_security_question(
            "newpass", question, "Taipei", {}
        )

        self.assertTrue(changed.startswith("✅"), changed)
        self.assertTrue(mismatch.startswith("❌"), mismatch)
        self.assertEqual(unauthenticated_change, "❌ 请先登录")
        self.assertEqual(unauthenticated_question, "❌ 请先登录")
        self.assertTrue(auth_login.login_user(auth_login.ROLE_CONSUMER, "alice", "newpass")[0])

    def test_login_returns_guide_and_relogin_clears_sensitive_inputs(self):
        """登录输出遗漏使用说明或重新登录保留密码时，本用例会失败。"""
        question = auth_login.SECURITY_QUESTIONS[1]
        self._register("消费者", "buyer", "oldpass", question, "Buddy")

        login_result = self.ui.do_login("消费者", "buyer", "oldpass")
        relogin_result = self.ui.relogin()

        self.assertIn("订单", login_result[8])
        self.assertIn("售后", login_result[8])
        cleared_values = [
            value for value in relogin_result if isinstance(value, dict) and value.get("value") == ""
        ]
        self.assertGreaterEqual(len(cleared_values), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
