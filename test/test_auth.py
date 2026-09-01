"""认证模块测试：只使用本次运行创建的临时用户 JSON。"""

import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

import tools.auth_login as auth_login
from tools.rbac import check_permission, get_allowed_tools, mask_goods_data


class AuthSecurityFlowTests(unittest.TestCase):
    """验证注册、安全问题和密码修改的真实文件流程。"""

    def setUp(self):
        """将认证模块指向本测试独占的临时用户文件。"""
        self.original_consumer_file = auth_login.CONSUMER_FILE
        self.original_merchant_file = auth_login.MERCHANT_FILE
        token = uuid.uuid4().hex
        data_dir = Path(ROOT_PATH) / "datas"
        self.temp_files = [
            data_dir / f"auth_test_{token}_consumer.json",
            data_dir / f"auth_test_{token}_merchant.json",
        ]
        self.addCleanup(self._remove_test_files)
        self.addCleanup(self._restore_auth_paths)
        auth_login.CONSUMER_FILE, auth_login.MERCHANT_FILE = self.temp_files
        if hasattr(auth_login, "_reset_password_attempts"):
            auth_login._reset_password_attempts.clear()
        auth_login.init_auth_files()

    def _restore_auth_paths(self):
        """恢复模块原始路径，避免后续测试访问临时文件。"""
        auth_login.CONSUMER_FILE = self.original_consumer_file
        auth_login.MERCHANT_FILE = self.original_merchant_file

    def _remove_test_files(self):
        """只删除本用例创建的两个用户文件。"""
        for file_path in self.temp_files:
            file_path.unlink(missing_ok=True)

    def _register(self, role, username="alice", password="oldpass", answer="Taipei"):
        """创建带安全问题的真实测试账号。"""
        return auth_login.register_user(
            role,
            username,
            password,
            auth_login.SECURITY_QUESTIONS[0],
            answer,
        )

    def test_auth_files_initialize_as_empty_json_without_touching_real_files(self):
        """文件初始化被移除或写入预设账号时，本用例会失败。"""
        self.assertEqual(json.loads(auth_login.CONSUMER_FILE.read_text(encoding="utf-8")), {})
        self.assertEqual(json.loads(auth_login.MERCHANT_FILE.read_text(encoding="utf-8")), {})

    def test_registration_validation_and_login_generic_failures_preserve_legacy_contract(self):
        """注册校验或登录通用错误提示退化时，本用例会失败。"""
        question = auth_login.SECURITY_QUESTIONS[0]
        cases = [
            ("bad-role", "valid_user", "oldpass", question, "answer"),
            (auth_login.ROLE_CONSUMER, "ab", "oldpass", question, "answer"),
            (auth_login.ROLE_CONSUMER, "valid_user", "123", question, "answer"),
            (auth_login.ROLE_CONSUMER, "bad<user>", "oldpass", question, "answer"),
            (auth_login.ROLE_CONSUMER, "valid_user", "oldpass", "自定义问题", "answer"),
            (auth_login.ROLE_CONSUMER, "valid_user", "oldpass", question, ""),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertFalse(auth_login.register_user(*args)[0])

        self.assertTrue(self._register(auth_login.ROLE_CONSUMER)[0])
        ok, _, role = auth_login.login_user(auth_login.ROLE_CONSUMER, "alice", "oldpass")
        self.assertTrue(ok)
        self.assertEqual(role, auth_login.ROLE_CONSUMER)

        wrong = auth_login.login_user(auth_login.ROLE_CONSUMER, "alice", "wrong")
        missing = auth_login.login_user(auth_login.ROLE_CONSUMER, "missing", "wrong")
        self.assertFalse(wrong[0])
        self.assertFalse(missing[0])
        self.assertEqual(wrong[1], "用户名或密码错误")
        self.assertEqual(missing[1], wrong[1])
        self.assertIsNone(wrong[2])
        self.assertIsNone(missing[2])

    def test_password_hashes_use_independent_salts_and_verify(self):
        """随机盐被复用或密码校验退化时，本用例会失败。"""
        first_hash, first_salt = auth_login._hash_password("same-password")
        second_hash, second_salt = auth_login._hash_password("same-password")

        self.assertNotEqual(first_salt, second_salt)
        self.assertNotEqual(first_hash, second_hash)
        self.assertTrue(auth_login.verify_password(first_hash, first_salt, "same-password"))
        self.assertTrue(auth_login.verify_password(second_hash, second_salt, "same-password"))

    def test_auth_roles_still_match_rbac_and_masking_contract(self):
        """账号角色与既有 RBAC 权限或脱敏规则脱节时，本用例会失败。"""
        consumer_tools = get_allowed_tools(auth_login.ROLE_CONSUMER)
        merchant_tools = get_allowed_tools(auth_login.ROLE_MERCHANT)
        self.assertNotIn("update_goods", consumer_tools)
        self.assertIn("update_goods", merchant_tools)
        self.assertFalse(check_permission("update_goods", auth_login.ROLE_CONSUMER))
        self.assertTrue(check_permission("update_goods", auth_login.ROLE_MERCHANT))

        goods = [{"商品ID": "SP001", "售价": 99, "上架状态": "已上架"}]
        self.assertNotIn("上架状态", mask_goods_data(goods, auth_login.ROLE_CONSUMER)[0])
        self.assertIn("上架状态", mask_goods_data(goods, auth_login.ROLE_MERCHANT)[0])

    def test_password_recovery_locks_after_three_failures_and_cooldown_is_deterministic(self):
        """连续猜答案未触发锁定或锁定不按时钟解除时，本用例会失败。"""
        self.assertTrue(self._register(auth_login.ROLE_CONSUMER)[0])

        with patch("time.monotonic", return_value=100.0):
            failures = [
                auth_login.reset_password(
                    auth_login.ROLE_CONSUMER, "alice", "wrong", "newpass", "newpass"
                )
                for _ in range(3)
            ]
            locked = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "Taipei", "newpass", "newpass"
            )

        self.assertTrue(all(not ok for ok, _ in failures))
        self.assertFalse(locked[0])
        self.assertIn("稍后", locked[1])

        with patch("time.monotonic", return_value=401.0):
            recovered = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "Taipei", "newpass", "newpass"
            )
        self.assertTrue(recovered[0], recovered[1])

    def test_unknown_account_and_wrong_answer_share_message_and_success_clears_failures(self):
        """账号枚举提示或成功后残留失败次数时，本用例会失败。"""
        self.assertTrue(self._register(auth_login.ROLE_CONSUMER)[0])
        with patch("time.monotonic", return_value=200.0):
            missing = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "missing", "wrong", "newpass", "newpass"
            )
            wrong = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "wrong", "newpass", "newpass"
            )
            success = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "Taipei", "newpass", "newpass"
            )
            first_after_success = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "wrong", "finalpass", "finalpass"
            )
            second_after_success = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "wrong", "finalpass", "finalpass"
            )
            final_success = auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "Taipei", "finalpass", "finalpass"
            )

        self.assertEqual(missing, wrong)
        self.assertTrue(success[0], success[1])
        self.assertFalse(first_after_success[0])
        self.assertFalse(second_after_success[0])
        self.assertTrue(final_success[0], final_success[1])

    def test_registration_recovery_and_password_change_store_no_plain_answer(self):
        """注册答案会哈希保存，且可用于找回和后续改密。"""
        ok, _ = auth_login.register_user(
            auth_login.ROLE_CONSUMER,
            "alice",
            "oldpass",
            auth_login.SECURITY_QUESTIONS[0],
            "Taipei",
        )
        self.assertTrue(ok)
        self.assertNotIn("Taipei", auth_login.CONSUMER_FILE.read_text(encoding="utf-8"))
        self.assertEqual(
            auth_login.get_security_question(auth_login.ROLE_CONSUMER, "alice"),
            (True, auth_login.SECURITY_QUESTIONS[0]),
        )
        self.assertFalse(
            auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "wrong", "newpass", "newpass"
            )[0]
        )
        self.assertTrue(
            auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "alice", "taipei", "newpass", "newpass"
            )[0]
        )
        self.assertTrue(auth_login.login_user(auth_login.ROLE_CONSUMER, "alice", "newpass")[0])
        self.assertTrue(
            auth_login.change_password(
                auth_login.ROLE_CONSUMER, "alice", "newpass", "finalpass", "finalpass"
            )[0]
        )
        self.assertTrue(auth_login.login_user(auth_login.ROLE_CONSUMER, "alice", "finalpass")[0])

    def test_legacy_user_can_log_in_change_password_then_set_question_for_recovery(self):
        """旧账号缺少安全问题时不影响登录和改密，但找回必须先设置问题。"""
        password_hash, salt = auth_login._hash_password("oldpass")
        auth_login.CONSUMER_FILE.write_text(
            json.dumps(
                {
                    "legacy": {
                        "password_hash": password_hash,
                        "salt": salt,
                        "created_at": "2026-09-01 00:00:00",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.assertTrue(auth_login.login_user(auth_login.ROLE_CONSUMER, "legacy", "oldpass")[0])
        self.assertTrue(
            auth_login.change_password(
                auth_login.ROLE_CONSUMER, "legacy", "oldpass", "changedpass", "changedpass"
            )[0]
        )
        ok, message = auth_login.get_security_question(auth_login.ROLE_CONSUMER, "legacy")
        self.assertFalse(ok)
        self.assertIn("未设置", message)
        self.assertFalse(
            auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "legacy", "answer", "resetpass", "resetpass"
            )[0]
        )
        self.assertTrue(
            auth_login.set_security_question(
                auth_login.ROLE_CONSUMER,
                "legacy",
                "changedpass",
                auth_login.SECURITY_QUESTIONS[1],
                "Buddy",
            )[0]
        )
        self.assertEqual(
            auth_login.get_security_question(auth_login.ROLE_CONSUMER, "legacy"),
            (True, auth_login.SECURITY_QUESTIONS[1]),
        )
        self.assertTrue(
            auth_login.reset_password(
                auth_login.ROLE_CONSUMER, "legacy", " buddy ", "resetpass", "resetpass"
            )[0]
        )
        self.assertTrue(auth_login.login_user(auth_login.ROLE_CONSUMER, "legacy", "resetpass")[0])

    def test_security_and_password_updates_reject_invalid_inputs(self):
        """错误凭据、密码确认不一致和不支持的问题均不会更新账号。"""
        ok, _ = auth_login.register_user(
            auth_login.ROLE_MERCHANT,
            "seller",
            "oldpass",
            auth_login.SECURITY_QUESTIONS[2],
            "Taipei",
        )
        self.assertTrue(ok)
        self.assertFalse(
            auth_login.register_user(
                auth_login.ROLE_MERCHANT, "badquestion", "oldpass", "自定义问题", "answer"
            )[0]
        )
        self.assertFalse(
            auth_login.change_password(
                auth_login.ROLE_MERCHANT, "seller", "wrongpass", "newpass", "newpass"
            )[0]
        )
        self.assertFalse(
            auth_login.change_password(
                auth_login.ROLE_MERCHANT, "seller", "oldpass", "newpass", "different"
            )[0]
        )
        self.assertFalse(
            auth_login.set_security_question(
                auth_login.ROLE_MERCHANT, "seller", "oldpass", "自定义问题", "answer"
            )[0]
        )
        self.assertFalse(
            auth_login.set_security_question(
                auth_login.ROLE_MERCHANT,
                "seller",
                "oldpass",
                auth_login.SECURITY_QUESTIONS[0],
                "",
            )[0]
        )
        self.assertTrue(auth_login.login_user(auth_login.ROLE_MERCHANT, "seller", "oldpass")[0])

    def test_invalid_role_cannot_read_or_overwrite_merchant_users(self):
        """直接调用文件助手时，非法角色也不能触碰商家用户文件。"""
        merchant_content = '{"merchant_only": {"sentinel": "keep"}}'
        auth_login.MERCHANT_FILE.write_text(merchant_content, encoding="utf-8")

        loaded = auth_login._load_users("attacker")
        saved = auth_login._save_users("attacker", {"attacker": {}})

        self.assertEqual(loaded, {})
        self.assertFalse(saved)
        self.assertEqual(
            auth_login.MERCHANT_FILE.read_text(encoding="utf-8"), merchant_content
        )

    def test_all_updates_preserve_original_file_when_atomic_replace_fails(self):
        """原子替换失败时，所有更新接口均失败且不会截断原用户文件。"""
        ok, _ = auth_login.register_user(
            auth_login.ROLE_CONSUMER,
            "alice",
            "oldpass",
            auth_login.SECURITY_QUESTIONS[0],
            "Taipei",
        )
        self.assertTrue(ok)
        actions = [
            (
                "register_user",
                lambda: auth_login.register_user(
                    auth_login.ROLE_CONSUMER,
                    "newuser",
                    "newpass",
                    auth_login.SECURITY_QUESTIONS[1],
                    "Buddy",
                ),
            ),
            (
                "reset_password",
                lambda: auth_login.reset_password(
                    auth_login.ROLE_CONSUMER,
                    "alice",
                    "taipei",
                    "resetpass",
                    "resetpass",
                ),
            ),
            (
                "change_password",
                lambda: auth_login.change_password(
                    auth_login.ROLE_CONSUMER,
                    "alice",
                    "oldpass",
                    "changedpass",
                    "changedpass",
                ),
            ),
            (
                "set_security_question",
                lambda: auth_login.set_security_question(
                    auth_login.ROLE_CONSUMER,
                    "alice",
                    "oldpass",
                    auth_login.SECURITY_QUESTIONS[1],
                    "Buddy",
                ),
            ),
        ]
        results = []
        for name, action in actions:
            original_content = auth_login.CONSUMER_FILE.read_text(encoding="utf-8")
            with patch.object(auth_login.os, "replace", side_effect=OSError("replace failed")):
                update_ok, _ = action()
            results.append(
                (name, update_ok, original_content, auth_login.CONSUMER_FILE.read_text(encoding="utf-8"))
            )
            auth_login.CONSUMER_FILE.write_text(original_content, encoding="utf-8")

        for name, update_ok, original_content, current_content in results:
            with self.subTest(update=name):
                self.assertFalse(update_ok)
                self.assertEqual(current_content, original_content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
