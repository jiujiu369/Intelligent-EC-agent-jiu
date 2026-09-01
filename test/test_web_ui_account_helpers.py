"""Web UI 账号安全回调测试：认证数据只写入本测试创建的临时文件。"""

import importlib
import os
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

import tools.auth_login as auth_login


class _FakeComponent:
    """代替仅在导入期构建界面所需的 Gradio 组件。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.events = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def click(self, *args, **kwargs):
        self.events.append({
            "kind": "click",
            "fn": args[0] if args else kwargs.get("fn"),
            "inputs": args[1] if len(args) > 1 else kwargs.get("inputs", []),
            "outputs": args[2] if len(args) > 2 else kwargs.get("outputs", []),
        })
        return None

    def submit(self, *args, **kwargs):
        self.events.append({
            "kind": "submit",
            "fn": args[0] if args else kwargs.get("fn"),
            "inputs": args[1] if len(args) > 1 else kwargs.get("inputs", []),
            "outputs": args[2] if len(args) > 2 else kwargs.get("outputs", []),
        })
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
        if hasattr(auth_login, "_reset_password_attempts"):
            auth_login._reset_password_attempts.clear()
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
        self.assertEqual(len(result), 3)
        self.assertTrue(result[0].startswith("✅"), result[0])

    def _assert_empty_update(self, update):
        """验证回调返回清空敏感组件的 Gradio 更新。"""
        self.assertEqual(update, {"__type__": "update", "value": ""})

    def test_usage_guides_follow_role_capabilities(self):
        """角色分支错误时，买家和商家的界面说明会混淆。"""
        consumer_guide = self.ui._usage_guide(auth_login.ROLE_CONSUMER)
        merchant_guide = self.ui._usage_guide(auth_login.ROLE_MERCHANT)

        self.assertIn("订单", consumer_guide)
        self.assertIn("售后", consumer_guide)
        self.assertIn("库存", merchant_guide)
        self.assertIn("销售报表", merchant_guide)
        self.assertIn("查询订单 ORD001", consumer_guide)
        self.assertIn("把 SP001 的售价改为 99 元", merchant_guide)
        self.assertIn("刷新运维面板", merchant_guide)

    def test_role_mapping_fails_closed_for_every_account_callback(self):
        """未知界面角色不得落入商家分支或调用任何认证操作。"""
        self.assertIsNone(self.ui._role_from_radio(None))
        self.assertIsNone(self.ui._role_from_radio("消费者（伪造）"))
        self.assertEqual(self.ui._role_label("unknown"), "未知角色")
        self.assertEqual(self.ui._usage_guide("unknown"), "❌ 无效角色")

        with patch.object(self.ui, "login_user") as login_mock:
            login_result = self.ui.do_login("unknown", "alice", "password")
            self.assertTrue(login_result[2].startswith("❌"))
            login_mock.assert_not_called()
        with patch.object(self.ui, "register_user") as register_mock:
            register_result = self.ui.do_register(
                "unknown", "alice", "password", auth_login.SECURITY_QUESTIONS[0], "answer"
            )
            self.assertTrue(register_result[0].startswith("❌"))
            register_mock.assert_not_called()
        with patch.object(self.ui, "get_security_question") as question_mock:
            self.assertTrue(self.ui.do_get_security_question("unknown", "alice").startswith("❌"))
            question_mock.assert_not_called()
        with patch.object(self.ui, "reset_password") as reset_mock:
            reset_result = self.ui.do_reset_password(
                "unknown", "alice", "answer", "newpass", "newpass"
            )
            self.assertTrue(reset_result[0].startswith("❌"))
            reset_mock.assert_not_called()
        invalid_state = {"role": "unknown", "username": "alice"}
        with patch.object(self.ui, "change_password") as change_mock:
            change_result = self.ui.do_change_password(
                "oldpass", "newpass", "newpass", invalid_state
            )
            self.assertTrue(change_result[0].startswith("❌"))
            change_mock.assert_not_called()
        with patch.object(self.ui, "set_security_question") as set_question_mock:
            set_result = self.ui.do_set_security_question(
                "oldpass", auth_login.SECURITY_QUESTIONS[0], "answer", invalid_state
            )
            self.assertTrue(set_result[0].startswith("❌"))
            set_question_mock.assert_not_called()

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
        self.assertEqual(len(wrong_answer), 4)
        self.assertTrue(wrong_answer[0].startswith("❌"), wrong_answer[0])
        self.assertIn("错误", wrong_answer[0])
        self.assertTrue(reset_result[0].startswith("✅"), reset_result[0])
        for update in wrong_answer[1:] + reset_result[1:]:
            self._assert_empty_update(update)
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

        self.assertEqual(len(changed), 4)
        self.assertTrue(changed[0].startswith("✅"), changed[0])
        self.assertTrue(mismatch[0].startswith("❌"), mismatch[0])
        self.assertEqual(unauthenticated_change[0], "❌ 请先登录")
        self.assertEqual(unauthenticated_question[0], "❌ 请先登录")
        for result in (changed, mismatch, unauthenticated_change):
            for update in result[1:]:
                self._assert_empty_update(update)
        for update in unauthenticated_question[1:]:
            self._assert_empty_update(update)
        self.assertTrue(auth_login.login_user(auth_login.ROLE_CONSUMER, "alice", "newpass")[0])

    def test_registration_and_set_question_clear_sensitive_inputs_on_failure_and_success(self):
        """注册或安全问题回调保留密码/答案时，本用例会失败。"""
        question = auth_login.SECURITY_QUESTIONS[0]
        failed_registration = self.ui.do_register(
            "消费者", "ab", "secret", question, "answer"
        )
        successful_registration = self.ui.do_register(
            "消费者", "alice", "secret", question, "answer"
        )
        state = {"role": auth_login.ROLE_CONSUMER, "username": "alice"}
        failed_question = self.ui.do_set_security_question(
            "wrong", question, "new-answer", state
        )
        successful_question = self.ui.do_set_security_question(
            "secret", question, "new-answer", state
        )

        for result, cardinality in (
            (failed_registration, 3),
            (successful_registration, 3),
            (failed_question, 3),
            (successful_question, 3),
        ):
            self.assertEqual(len(result), cardinality)
            for update in result[1:]:
                self._assert_empty_update(update)

    def test_account_event_bindings_match_callback_cardinality_and_sensitive_outputs(self):
        """事件输出少绑敏感组件或数量不匹配时，本用例会失败。"""
        bindings = [
            (
                self.ui.register_btn.events[0],
                5,
                [self.ui.login_status, self.ui.password_box, self.ui.register_answer],
            ),
            (
                self.ui.recover_question_btn.events[0],
                2,
                [self.ui.recover_question],
            ),
            (
                self.ui.recover_btn.events[0],
                5,
                [
                    self.ui.recover_status,
                    self.ui.recover_answer,
                    self.ui.recover_new_password,
                    self.ui.recover_confirm_password,
                ],
            ),
            (
                self.ui.change_password_btn.events[0],
                4,
                [
                    self.ui.change_password_status,
                    self.ui.change_old_password,
                    self.ui.change_new_password,
                    self.ui.change_confirm_password,
                ],
            ),
            (
                self.ui.set_question_btn.events[0],
                4,
                [
                    self.ui.set_question_status,
                    self.ui.set_question_password,
                    self.ui.set_question_answer,
                ],
            ),
        ]
        for binding, input_count, expected_outputs in bindings:
            with self.subTest(callback=binding["fn"].__name__):
                self.assertEqual(len(binding["inputs"]), input_count)
                self.assertEqual(binding["outputs"], expected_outputs)

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
