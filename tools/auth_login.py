# tools/auth_login.py
# 独立登录模块 —— 账号注册、加盐哈希密码、登录校验
# ============================================================
# 设计原则：
#   1. 密码采用 PBKDF2-SHA256 加盐哈希，严禁明文保存
#   2. 账号文件按角色拆分：consumer_users.json / merchant_users.json
#   3. 文件自动初始化（空文件，无预设账号），无需手动创建
#   4. 不依赖 RAG、业务工具、Agent 代码，完全独立可集成
#   5. 完善的输入异常捕获与用户提示
# ============================================================

import json
import hashlib
import os
import sys

from typing import Tuple, Optional

import config
from utils.logger import get_logger
from utils.rate_limiter import rate_limit_login

logger = get_logger(__name__)

# ====================== 常量 ======================

# 自动推断项目根目录（兼容从不同位置运行）
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
DATA_DIR = os.path.join(_PROJECT_ROOT, "datas")

def _resolve_project_path(path: str) -> str:
    """将配置路径解析为项目内的绝对路径。
    :param path: 传入 ``path`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    if os.path.isabs(path):
        return path
    return os.path.join(_PROJECT_ROOT, path)


CONSUMER_FILE = _resolve_project_path(config.get("PATHS", "consumer_users_json"))
MERCHANT_FILE = _resolve_project_path(config.get("PATHS", "merchant_users_json"))

ROLE_CONSUMER = "consumer"
ROLE_MERCHANT = "merchant"

SECURITY_QUESTIONS = (
    "你的第一所学校名称是？",
    "你的童年昵称是？",
    "你最喜欢的城市是？",
)

# 密码哈希参数
HASH_ALGORITHM = "sha256"
HASH_ITERATIONS = 100_000   # PBKDF2 迭代次数，业内推荐 ≥ 100k
SALT_LENGTH = 32            # 盐值长度（字节）
KEY_LENGTH = 32             # 派生密钥长度（字节）

# ====================== 预设账号（已移除） ======================
# 不再提供任何预设/测试账号。用户必须通过注册流程自行创建账号。
# 如需恢复演示账号，请在此手动定义并调用 _init_with_preset。


# ====================== 密码哈希 ======================

def _hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
    """PBKDF2-SHA256 加盐哈希。
    :param password: 用户提供的登录密码。
    :param salt: 密码哈希使用的随机盐值。
    :return: 返回函数处理得到的结果。
    """
    if salt is None:
        salt = os.urandom(SALT_LENGTH)
    derived = hashlib.pbkdf2_hmac(
        HASH_ALGORITHM,
        password.encode("utf-8"),
        salt,
        HASH_ITERATIONS,
        dklen=KEY_LENGTH,
    )
    return derived.hex(), salt.hex()


def verify_password(stored_hash: str, stored_salt: str, password: str) -> bool:
    """校验明文密码与存储的密文是否匹配。
    :param stored_hash: 账户中保存的密码哈希值。
    :param stored_salt: 账户中保存的密码盐值。
    :param password: 用户提供的登录密码。
    :return: 条件成立时返回 ``True``，否则返回 ``False``。
    """
    salt = bytes.fromhex(stored_salt)
    computed_hash, _ = _hash_password(password, salt)
    return _constant_time_compare(computed_hash, stored_hash)


def _constant_time_compare(a: str, b: str) -> bool:
    """恒定时间字符串比较，防止时序侧信道攻击。
    :param a: 参与比较的第一个值。
    :param b: 传入 ``b`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def _normalize_security_answer(answer: str) -> str:
    """规范化安全问题答案，避免大小写和首尾空格影响校验。
    :param answer: 用户输入的安全问题答案。
    :return: 返回规范化后的答案。
    """
    return (answer or "").strip().lower()


def _validate_new_password(new_password: str, confirm_password: str) -> Tuple[bool, str]:
    """校验新密码及确认密码。
    :param new_password: 用户输入的新密码。
    :param confirm_password: 用户再次确认的新密码。
    :return: 返回校验是否通过及对应提示。
    """
    if new_password != confirm_password:
        return False, "两次新密码不一致"
    if not new_password:
        return False, "新密码不能为空"
    if len(new_password) < 4:
        return False, "新密码至少需要 4 个字符"
    return True, ""


def _has_security_question(user_data: dict) -> bool:
    """判断账户是否具有完整且受支持的安全问题配置。
    :param user_data: 账户数据。
    :return: 配置完整时返回 ``True``，否则返回 ``False``。
    """
    return (
        user_data.get("security_question") in SECURITY_QUESTIONS
        and bool(user_data.get("security_answer_hash"))
        and bool(user_data.get("security_answer_salt"))
    )


# ====================== 文件初始化 ======================

def init_auth_files() -> None:
    """自动初始化账号文件（无预设账号，仅创建空文件）。"""
    os.makedirs(DATA_DIR, exist_ok=True)

    for file_path, role in ((CONSUMER_FILE, ROLE_CONSUMER), (MERCHANT_FILE, ROLE_MERCHANT)):
        if not os.path.exists(file_path):
            _init_with_preset(file_path, {}, role)
        else:
            # 文件存在但可能格式有误，做一次健壮读取校验
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    json.load(f)
            except (json.JSONDecodeError, Exception):
                print(f"[WARNING] {file_path} 格式异常，将重新初始化为空文件")
                _init_with_preset(file_path, {}, role)


def _init_with_preset(file_path: str, preset: dict, role: str) -> None:
    """初始化账号文件，写入 preset 中的账号（传空字典则仅创建空文件）。
    :param file_path: 目标文件路径。
    :param preset: 初始化用户文件时写入的预置账户数据。
    :param role: 当前用户角色。
    """
    users = {}
    for username, info in preset.items():
        plain = info.get("_plain_preset", "")
        password_hash, salt = _hash_password(plain) if plain else ("", "")
        users[username] = {
            "password_hash": password_hash,
            "salt": salt,
            "created_at": _now_str(),
        }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    print(f"[INIT] 已创建 {file_path}（角色: {role}，预置账号: {', '.join(users.keys())}）")


# ====================== 用户文件读写 ======================

def _load_users(role: str) -> dict:
    """加载指定角色的用户文件，返回 {username: user_data}。
    :param role: 当前用户角色。
    :return: 返回完成读取、构建或转换后的结果。
    """
    file_path = _get_user_file(role)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] 用户文件不存在: {file_path}，请先调用 init_auth_files()")
        return {}
    except json.JSONDecodeError as e:
        print(f"[ERROR] 用户文件格式错误: {file_path} -> {e}")
        return {}


def _save_users(role: str, users: dict) -> bool:
    """保存用户数据到文件。
    :param role: 当前用户角色。
    :param users: 需要保存的用户数据映射。
    :return: 返回函数处理得到的结果。
    """
    file_path = _get_user_file(role)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[ERROR] 无法写入用户文件 {file_path}: {e}")
        return False


def _get_user_file(role: str) -> str:
    """获取角色对应的用户文件路径。
    :param role: 当前用户角色。
    :return: 返回完成读取、构建或转换后的结果。
    """
    return CONSUMER_FILE if role == ROLE_CONSUMER else MERCHANT_FILE


# ====================== 账号注册 ======================

def register_user(
    role: str,
    username: str,
    password: str,
    security_question: str,
    security_answer: str,
) -> Tuple[bool, str]:
    """注册新用户。
    :param role: 当前用户角色。
    :param username: 用户登录名。
    :param password: 用户提供的登录密码。
    :return: 返回函数处理得到的结果。
    """
    # -- 参数校验 --
    if role not in (ROLE_CONSUMER, ROLE_MERCHANT):
        return False, f"无效角色: {role}，仅支持 consumer / merchant"

    if not username or not password:
        return False, "用户名和密码不能为空"

    if len(username.strip()) < 3:
        return False, "用户名至少需要 3 个字符"

    if len(password) < 4:
        return False, "密码至少需要 4 个字符"

    if security_question not in SECURITY_QUESTIONS:
        return False, "安全问题不受支持"

    normalized_answer = _normalize_security_answer(security_answer)
    if not normalized_answer:
        return False, "安全问题答案不能为空"

    # 用户名合法性校验
    for ch in username:
        if not (ch.isalnum() or ch == "_" or "\u4e00" <= ch <= "\u9fff"):
            return False, "用户名仅允许字母、数字、下划线、中文"

    username = username.strip()

    # -- 查重 --
    users = _load_users(role)
    if username in users:
        return False, f"用户名 [{username}] 已存在，请更换"

    # -- 注册 --
    password_hash, salt = _hash_password(password)
    answer_hash, answer_salt = _hash_password(normalized_answer)
    users[username] = {
        "password_hash": password_hash,
        "salt": salt,
        "security_question": security_question,
        "security_answer_hash": answer_hash,
        "security_answer_salt": answer_salt,
        "created_at": _now_str(),
    }

    if not _save_users(role, users):
        return False, "注册失败：无法写入用户文件"

    return True, f"注册成功！角色: {role}，用户名: {username}"


def get_security_question(role: str, username: str) -> Tuple[bool, str]:
    """获取账户已设置的安全问题。
    :param role: 当前用户角色。
    :param username: 用户登录名。
    :return: 返回查询是否成功及安全问题或提示。
    """
    if role not in (ROLE_CONSUMER, ROLE_MERCHANT):
        return False, "无效角色"

    user_data = _load_users(role).get((username or "").strip())
    if user_data is None:
        return False, "用户不存在"
    if not _has_security_question(user_data):
        return False, "该账号尚未设置安全问题"
    return True, user_data["security_question"]


def reset_password(
    role: str,
    username: str,
    security_answer: str,
    new_password: str,
    confirm_password: str,
) -> Tuple[bool, str]:
    """通过安全问题答案重设密码。
    :param role: 当前用户角色。
    :param username: 用户登录名。
    :param security_answer: 用户输入的安全问题答案。
    :param new_password: 用户输入的新密码。
    :param confirm_password: 用户再次确认的新密码。
    :return: 返回重设是否成功及对应提示。
    """
    if role not in (ROLE_CONSUMER, ROLE_MERCHANT):
        return False, "无效角色"
    valid_password, message = _validate_new_password(new_password, confirm_password)
    if not valid_password:
        return False, message

    users = _load_users(role)
    username = (username or "").strip()
    user_data = users.get(username)
    if user_data is None:
        return False, "用户名或安全问题答案错误"
    if not _has_security_question(user_data):
        return False, "该账号尚未设置安全问题，无法找回密码"

    normalized_answer = _normalize_security_answer(security_answer)
    if not normalized_answer or not verify_password(
        user_data["security_answer_hash"],
        user_data["security_answer_salt"],
        normalized_answer,
    ):
        return False, "用户名或安全问题答案错误"

    password_hash, salt = _hash_password(new_password)
    user_data["password_hash"] = password_hash
    user_data["salt"] = salt
    if not _save_users(role, users):
        return False, "密码重设失败：无法写入用户文件"
    return True, "密码重设成功"


def change_password(
    role: str,
    username: str,
    old_password: str,
    new_password: str,
    confirm_password: str,
) -> Tuple[bool, str]:
    """使用旧密码修改账户密码。
    :param role: 当前用户角色。
    :param username: 用户登录名。
    :param old_password: 用户当前密码。
    :param new_password: 用户输入的新密码。
    :param confirm_password: 用户再次确认的新密码。
    :return: 返回修改是否成功及对应提示。
    """
    if role not in (ROLE_CONSUMER, ROLE_MERCHANT):
        return False, "无效角色"
    valid_password, message = _validate_new_password(new_password, confirm_password)
    if not valid_password:
        return False, message

    users = _load_users(role)
    user_data = users.get((username or "").strip())
    if user_data is None:
        return False, "用户名或旧密码错误"
    if not old_password or not verify_password(
        user_data.get("password_hash", ""), user_data.get("salt", ""), old_password
    ):
        return False, "用户名或旧密码错误"

    password_hash, salt = _hash_password(new_password)
    user_data["password_hash"] = password_hash
    user_data["salt"] = salt
    if not _save_users(role, users):
        return False, "密码修改失败：无法写入用户文件"
    return True, "密码修改成功"


def set_security_question(
    role: str,
    username: str,
    current_password: str,
    question: str,
    answer: str,
) -> Tuple[bool, str]:
    """登录后为账户设置或更新安全问题。
    :param role: 当前用户角色。
    :param username: 用户登录名。
    :param current_password: 用户当前密码。
    :param question: 选定的安全问题。
    :param answer: 用户输入的安全问题答案。
    :return: 返回设置是否成功及对应提示。
    """
    if role not in (ROLE_CONSUMER, ROLE_MERCHANT):
        return False, "无效角色"
    if question not in SECURITY_QUESTIONS:
        return False, "安全问题不受支持"
    normalized_answer = _normalize_security_answer(answer)
    if not normalized_answer:
        return False, "安全问题答案不能为空"

    users = _load_users(role)
    user_data = users.get((username or "").strip())
    if user_data is None:
        return False, "用户名或当前密码错误"
    if not current_password or not verify_password(
        user_data.get("password_hash", ""), user_data.get("salt", ""), current_password
    ):
        return False, "用户名或当前密码错误"

    answer_hash, answer_salt = _hash_password(normalized_answer)
    user_data["security_question"] = question
    user_data["security_answer_hash"] = answer_hash
    user_data["security_answer_salt"] = answer_salt
    if not _save_users(role, users):
        return False, "安全问题设置失败：无法写入用户文件"
    return True, "安全问题设置成功"


# ====================== 登录校验 ======================

@rate_limit_login
def login_user(role: str, username: str, password: str) -> Tuple[bool, str, Optional[str]]:
    """登录校验。
    :param role: 当前用户角色。
    :param username: 用户登录名。
    :param password: 用户提供的登录密码。
    :return: 返回函数处理得到的结果。
    """
    if role not in (ROLE_CONSUMER, ROLE_MERCHANT):
        logger.info(f"登录失败 reason=invalid_role role={role} username={username}")
        return False, "无效角色", None

    if not username or not password:
        logger.info(f"登录失败 reason=empty_username_or_password role={role} username={username}")
        return False, "用户名或密码不能为空", None

    username = username.strip()
    users = _load_users(role)
    user_data = users.get(username)

    if user_data is None:
        # 模糊提示：不透露用户是否存在
        logger.info(f"登录失败 reason=user_or_password_error role={role} username={username}")
        return False, "用户名或密码错误", None

    stored_hash = user_data.get("password_hash", "")
    stored_salt = user_data.get("salt", "")

    if not stored_hash or not stored_salt:
        logger.info(f"登录失败 reason=account_data_corrupted role={role} username={username}")
        return False, "账号数据损坏，请联系管理员", None

    if verify_password(stored_hash, stored_salt, password):
        logger.info(f"登录成功 role={role} username={username}")
        return True, f"登录成功，欢迎 {username}（{role}）", role
    else:
        logger.info(f"登录失败 reason=user_or_password_error role={role} username={username}")
        return False, "用户名或密码错误", None


# ====================== CLI 交互式登录流程 ======================

def auth_interactive() -> Tuple[Optional[str], Optional[str]]:
    """交互式登录/注册流程入口。
    :return: 返回函数处理得到的结果。
    """
    # -- 选择角色 --
    print("\n" + "=" * 50)
    print("  电商客服 Agent — 登录")
    print("=" * 50)
    print("  请选择角色：")
    print("    1. 买家 (consumer)")
    print("    2. 商家 (merchant)")
    print("    0. 退出")
    print("-" * 50)

    while True:
        choice = input("  请输入选项 (0/1/2): ").strip()
        if choice == "0":
            print("  已退出登录。")
            return None, None
        if choice == "1":
            role = ROLE_CONSUMER
            break
        if choice == "2":
            role = ROLE_MERCHANT
            break
        print("  无效选项，请重新输入")

    # -- 登录 or 注册 --
    role_label = "买家" if role == ROLE_CONSUMER else "商家"
    print(f"\n  当前角色: [{role_label}]")
    print("    1. 登录")
    print("    2. 注册新账号")
    print("    0. 返回")

    while True:
        choice = input("  请输入选项 (0/1/2): ").strip()
        if choice == "0":
            return auth_interactive()   # 递归返回角色选择
        if choice in ("1", "2"):
            break
        print("  无效选项，请重新输入")

    if choice == "1":
        # 登录
        for attempt in range(3):
            username = input("  用户名: ").strip()
            password = _getpass("  密码: ")
            if not username or not password:
                print("  用户名和密码不能为空，请重试")
                continue
            ok, msg, r = login_user(role, username, password)
            if ok:
                print(f"\n  ✅ {msg}")
                return r, username
            else:
                remaining = 2 - attempt
                if remaining > 0:
                    print(f"  ❌ {msg}，还剩 {remaining} 次尝试")
                else:
                    print(f"  ❌ {msg}，尝试次数已用完，返回主菜单")
                    return auth_interactive()

    else:
        # 注册
        print("\n  --- 注册新账号 ---")
        username = input("  用户名 (至少3位): ").strip()
        password = _getpass("  密码 (至少4位): ")
        confirm = _getpass("  确认密码: ")

        if password != confirm:
            print("  ❌ 两次密码不一致，注册失败")
            return auth_interactive()

        print("  请选择安全问题：")
        for index, question in enumerate(SECURITY_QUESTIONS, start=1):
            print(f"    {index}. {question}")
        question_choice = input("  请输入问题编号 (1/2/3): ").strip()
        try:
            security_question = SECURITY_QUESTIONS[int(question_choice) - 1]
        except (ValueError, IndexError):
            print("  ❌ 无效安全问题，注册失败")
            return auth_interactive()
        security_answer = _getpass("  安全问题答案: ")

        ok, msg = register_user(
            role, username, password, security_question, security_answer
        )
        if ok:
            print(f"  ✅ {msg}")
            return role, username
        else:
            print(f"  ❌ {msg}")
            return auth_interactive()

    return None, None


def _getpass(prompt: str) -> str:
    """跨平台安全密码输入。
    :param prompt: 展示给用户或发送给模型的提示文本。
    :return: 返回函数处理得到的结果。
    """
    try:
        import msvcrt
        # Windows 下用 msvcrt 实现不回显
        sys.stdout.write(prompt)
        sys.stdout.flush()
        password = ""
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                sys.stdout.write("\n")
                break
            elif ch == "\x08":  # Backspace
                if password:
                    password = password[:-1]
                    sys.stdout.write("\b \b")
            elif ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
            else:
                password += ch
                sys.stdout.write("*")
        sys.stdout.flush()
        return password
    except (ImportError, Exception):
        # 回退：无法隐藏回显但功能正常
        return input(prompt)


# ====================== 工具函数 ======================

def _now_str() -> str:
    """当前时间字符串。
    :return: 返回函数处理得到的结果。
    """
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ====================== 模块自检 ======================

if __name__ == "__main__":
    print("=== auth_login.py 模块自检 ===\n")

    # 1. 初始化文件
    print("1. 初始化账号文件...")
    init_auth_files()
    print()

    # 2. 验证密码哈希（密文 ≠ 明文）
    print("2. 密码哈希验证...")
    h, s = _hash_password("test123")
    print(f"   明文 'test123' → hash={h[:16]}...  salt={s[:16]}...")
    assert h != "test123", "密文不应等于明文！"
    assert verify_password(h, s, "test123"), "密码校验应通过"
    assert not verify_password(h, s, "wrong"), "错误密码应被拒绝"
    print("   ✅ 密码哈希 + 校验正常")

    # 3. 登录测试
    print("\n3. 登录校验测试...")
    ok, msg, _ = login_user(ROLE_CONSUMER, "user1", "123456")
    print(f"   consumer/user1/123456 → {'✅' if ok else '❌'} {msg}")
    ok, msg, _ = login_user(ROLE_CONSUMER, "user1", "wrong")
    print(f"   consumer/user1/wrong  → {'✅' if ok else '❌'} {msg}")
    ok, msg, _ = login_user(ROLE_CONSUMER, "不存在", "123456")
    print(f"   consumer/不存在/123456 → {'✅' if ok else '❌'} {msg}")

    # 4. 注册测试
    print("\n4. 注册功能测试...")
    ok, msg = register_user(
        ROLE_CONSUMER,
        "test_reg_user",
        "pass1234",
        SECURITY_QUESTIONS[0],
        "test_answer",
    )
    print(f"   注册 test_reg_user → {'✅' if ok else '❌'} {msg}")
    # 清理测试账号
    if ok:
        users = _load_users(ROLE_CONSUMER)
        users.pop("test_reg_user", None)
        _save_users(ROLE_CONSUMER, users)
        print("   (已清理测试账号)")

    print("\n=== 自检完成 ===")
