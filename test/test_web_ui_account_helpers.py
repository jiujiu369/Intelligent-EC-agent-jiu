"""Web UI 账号安全回调测试：认证数据只写入本测试创建的临时文件。"""

import importlib
import os
import queue
import sys
import time
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
            "cancels": kwargs.get("cancels"),
        })
        return self

    def submit(self, *args, **kwargs):
        self.events.append({
            "kind": "submit",
            "fn": args[0] if args else kwargs.get("fn"),
            "inputs": args[1] if len(args) > 1 else kwargs.get("inputs", []),
            "outputs": args[2] if len(args) > 2 else kwargs.get("outputs", []),
            "cancels": kwargs.get("cancels"),
        })
        return self

    def then(self, *args, **kwargs):
        self.events.append({
            "kind": "then",
            "fn": args[0] if args else kwargs.get("fn"),
            "inputs": args[1] if len(args) > 1 else kwargs.get("inputs", []),
            "outputs": args[2] if len(args) > 2 else kwargs.get("outputs", []),
            "cancels": kwargs.get("cancels"),
        })
        return self

    def tick(self, *args, **kwargs):
        self.events.append({
            "kind": "tick",
            "fn": args[0] if args else kwargs.get("fn"),
            "inputs": args[1] if len(args) > 1 else kwargs.get("inputs", []),
            "outputs": args[2] if len(args) > 2 else kwargs.get("outputs", []),
            "cancels": kwargs.get("cancels"),
        })
        return self


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
        Timer=_FakeComponent,
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
        self.assertIn("查询 DD001", consumer_guide)
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

    def test_chat_control_callbacks_switch_buttons_and_report_timeout(self):
        """按钮状态未切换或超时提示错误时，本用例应失败。"""
        started = self.ui.start_chat_request()
        request_id = started[3]
        finished = self.ui.finish_chat_request(request_id)
        timed_out = self.ui.stop_chat_request(request_id, True)
        manually_stopped = self.ui.stop_chat_request(request_id, False)

        self.assertEqual(started[0], {"__type__": "update", "visible": False})
        self.assertEqual(started[1], {"__type__": "update", "visible": True})
        self.assertEqual(started[2], {"__type__": "update", "active": True})
        self.assertTrue(request_id)
        self.assertEqual(finished[0], {"__type__": "update", "visible": True})
        self.assertEqual(finished[1], {"__type__": "update", "visible": False})
        self.assertEqual(finished[2], {"__type__": "update", "active": False})
        self.assertEqual(
            timed_out[3],
            "请求已超时（已超过45秒），已自动终止，这不是你的问题",
        )
        self.assertEqual(manually_stopped[3], "已终止当前请求")
        self.assertEqual(manually_stopped[4], "")

    def test_stop_chat_request_terminates_a_running_worker(self):
        """点击终止只隐藏结果但不结束子进程时，本用例应失败。"""
        class RunningProcess:
            def __init__(self):
                self.terminated = False
                self.joined = False

            def is_alive(self):
                return not self.terminated

            def terminate(self):
                self.terminated = True

            def join(self, timeout=None):
                self.joined = True

        process = RunningProcess()
        request_id = "running-request"
        self.ui._ACTIVE_CHAT_REQUESTS[request_id] = (process, None)

        self.ui.stop_chat_request(request_id, False)

        self.assertTrue(process.terminated)
        self.assertTrue(process.joined)
        self.assertNotIn(request_id, self.ui._ACTIVE_CHAT_REQUESTS)

    def test_stop_chat_request_kills_a_real_spawned_process(self):
        """Windows spawn 子进程必须在终止回调后实际退出。"""
        process = self.ui._CHAT_PROCESS_CONTEXT.Process(target=time.sleep, args=(30,))
        process.start()
        request_id = "spawned-request"
        self.ui._ACTIVE_CHAT_REQUESTS[request_id] = (process, None)

        self.ui.stop_chat_request(request_id, False)

        self.assertFalse(process.is_alive())
        self.assertNotIn(request_id, self.ui._ACTIVE_CHAT_REQUESTS)

    def test_subprocess_wait_enforces_the_backend_deadline(self):
        """即使浏览器计时器失效，后端也必须在截止时间终止任务。"""
        class EmptyQueue:
            def get(self, timeout=None):
                raise queue.Empty

            def close(self):
                pass

            def join_thread(self):
                pass

        class RunningProcess:
            def __init__(self):
                self.terminated = False

            def start(self):
                pass

            def is_alive(self):
                return not self.terminated

            def terminate(self):
                self.terminated = True

            def join(self, timeout=None):
                pass

        process = RunningProcess()
        context = types.SimpleNamespace(
            Queue=lambda: EmptyQueue(),
            Process=lambda **kwargs: process,
        )
        state = {
            "session": "默认会话",
            "role": auth_login.ROLE_CONSUMER,
            "username": "alice",
        }

        with patch.object(self.ui, "_CHAT_PROCESS_CONTEXT", context), patch.object(
            self.ui, "CHAT_TIMEOUT_SECONDS", 0
        ):
            answer, status = self.ui._run_chat_in_subprocess(
                "question", state, "deadline-request"
            )

        self.assertIsNone(answer)
        self.assertEqual(status, "请求已超时（已超过45秒），已自动终止，这不是你的问题")
        self.assertTrue(process.terminated)

    def test_agent_worker_returns_answer_or_error_through_queue(self):
        """子进程入口未通过队列回传回答或异常时，本用例应失败。"""
        class ResultQueue:
            def __init__(self):
                self.items = []

            def put(self, value):
                self.items.append(value)

        result_queue = ResultQueue()
        agent_module = sys.modules["agent.main_agent"]
        agent_module.run_agent = lambda *args, **kwargs: "worker-answer"

        self.ui.run_agent_process(
            result_queue,
            "question",
            "默认会话",
            auth_login.ROLE_CONSUMER,
            "alice",
        )
        self.assertEqual(result_queue.items, [("ok", "worker-answer")])

        def raise_error(*args, **kwargs):
            raise RuntimeError("worker-error")

        agent_module.run_agent = raise_error
        self.ui.run_agent_process(
            result_queue,
            "question",
            "默认会话",
            auth_login.ROLE_CONSUMER,
            "alice",
        )
        self.assertEqual(result_queue.items[-1], ("error", "worker-error"))

    def test_chat_stop_and_timeout_events_cancel_each_send_path(self):
        """停止事件遗漏点击发送或回车发送任务时，本用例应失败。"""
        stop_event = self.ui.stop_btn.events[0]
        timeout_event = self.ui.request_timer.events[0]

        self.assertEqual(stop_event["fn"].__name__, "stop_chat_request")
        self.assertEqual(timeout_event["fn"].__name__, "stop_chat_request")
        self.assertEqual(stop_event["cancels"], self.ui.chat_events)
        self.assertEqual(timeout_event["cancels"], self.ui.chat_events)
        self.assertEqual(stop_event["inputs"], [self.ui.request_id])
        self.assertEqual(
            timeout_event["inputs"], [self.ui.request_id, self.ui.timeout_flag]
        )
        self.assertEqual(self.ui.request_timer.args[0], 45)
        self.assertFalse(self.ui.request_timer.kwargs["active"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
