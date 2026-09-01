"""认证模块测试：只使用本次运行创建的临时用户 JSON。"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

import tools.auth_login as auth_login


class AuthSecurityFlowTests(unittest.TestCase):
    """验证注册、安全问题和密码修改的真实文件流程。"""

    def setUp(self):
        """将认证模块指向本测试独占的临时用户文件。"""
        self.original_consumer_file = auth_login.CONSUMER_FILE
        self.original_merchant_file = auth_login.MERCHANT_FILE
        self.temp_dir = Path(
            tempfile.mkdtemp(prefix=".project1_auth_", dir=os.path.dirname(__file__))
        )
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.addCleanup(self._restore_auth_paths)
        auth_login.CONSUMER_FILE = self.temp_dir / "consumer_users.json"
        auth_login.MERCHANT_FILE = self.temp_dir / "merchant_users.json"
        auth_login.init_auth_files()

    def _restore_auth_paths(self):
        """恢复模块原始路径，避免后续测试访问临时文件。"""
        auth_login.CONSUMER_FILE = self.original_consumer_file
        auth_login.MERCHANT_FILE = self.original_merchant_file

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
