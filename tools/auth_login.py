# tools/auth_login.py
# 独立登录模块 —— 账号注册、加盐哈希密码、登录校验
# ============================================================
# 设计原则：
#   1. 密码采用 PBKDF2-SHA256 加盐哈希，严禁明文保存
#   2. 账号文件按角色拆分：consumer_users.json / merchant_users.json
#   3. 文件自动初始化（含预设测试账号），无需手动创建
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

logger = get_logger(__name__)

# ====================== 常量 ======================

# 自动推断项目根目录（兼容从不同位置运行）
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
DATA_DIR = os.path.join(_PROJECT_ROOT, "datas")

def _resolve_project_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(_PROJECT_ROOT, path)


CONSUMER_FILE = _resolve_project_path(config.get("PATHS", "consumer_users_json"))
MERCHANT_FILE = _resolve_project_path(config.get("PATHS", "merchant_users_json"))

ROLE_CONSUMER = "consumer"
ROLE_MERCHANT = "merchant"

# 密码哈希参数
HASH_ALGORITHM = "sha256"
HASH_ITERATIONS = 100_000   # PBKDF2 迭代次数，业内推荐 ≥ 100k
SALT_LENGTH = 32            # 盐值长度（字节）
KEY_LENGTH = 32             # 派生密钥长度（字节）

# ====================== 预设测试账号 ======================

_DEFAULT_CONSUMER = {
    "user1": {
        "password_hash": "",    # 运行时动态生成密文
        "_plain_preset": "123456",
    },
}

_DEFAULT_MERCHANT = {
    "admin": {
        "password_hash": "",
        "_plain_preset": "admin123",
    },
}


# ====================== 密码哈希 ======================

def _hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
    """
    PBKDF2-SHA256 加盐哈希。
    自动生成随机盐值；也可传入已有 salt 用于登录校验。
    返回 (password_hash_hex, salt_hex)。
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
    """
    校验明文密码与存储的密文是否匹配。
    使用恒定时间比较防止时序攻击。
    """
    salt = bytes.fromhex(stored_salt)
    computed_hash, _ = _hash_password(password, salt)
    return _constant_time_compare(computed_hash, stored_hash)


def _constant_time_compare(a: str, b: str) -> bool:
    """恒定时间字符串比较，防止时序侧信道攻击。"""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


# ====================== 文件初始化 ======================

def init_auth_files() -> None:
    """
    自动初始化账号文件。
    - consumer_users.json —— 预置测试买家账号 user1/123456
    - merchant_users.json —— 预置测试商家账号 admin/admin123
    如果文件已存在则跳过，不会覆盖已有数据。
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    # -- 买家文件 --
    if not os.path.exists(CONSUMER_FILE):
        _init_with_preset(CONSUMER_FILE, _DEFAULT_CONSUMER, ROLE_CONSUMER)
    else:
        # 文件存在但可能格式有误，做一次健壮读取校验
        try:
            with open(CONSUMER_FILE, "r", encoding="utf-8") as f:
                json.load(f)
        except (json.JSONDecodeError, Exception):
            print(f"[WARNING] {CONSUMER_FILE} 格式异常，将重新初始化")
            _init_with_preset(CONSUMER_FILE, _DEFAULT_CONSUMER, ROLE_CONSUMER)

    # -- 商家文件 --
    if not os.path.exists(MERCHANT_FILE):
        _init_with_preset(MERCHANT_FILE, _DEFAULT_MERCHANT, ROLE_MERCHANT)
    else:
        try:
            with open(MERCHANT_FILE, "r", encoding="utf-8") as f:
                json.load(f)
        except (json.JSONDecodeError, Exception):
            print(f"[WARNING] {MERCHANT_FILE} 格式异常，将重新初始化")
            _init_with_preset(MERCHANT_FILE, _DEFAULT_MERCHANT, ROLE_MERCHANT)


def _init_with_preset(file_path: str, preset: dict, role: str) -> None:
    """用预设账号初始化文件，自动生成密码密文。"""
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
    """加载指定角色的用户文件，返回 {username: user_data}。"""
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
    """保存用户数据到文件。"""
    file_path = _get_user_file(role)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[ERROR] 无法写入用户文件 {file_path}: {e}")
        return False


def _get_user_file(role: str) -> str:
    """获取角色对应的用户文件路径。"""
    return CONSUMER_FILE if role == ROLE_CONSUMER else MERCHANT_FILE


# ====================== 账号注册 ======================

def register_user(role: str, username: str, password: str) -> Tuple[bool, str]:
    """
    注册新用户。
    返回 (success, message)。
    规则：
      - 用户名至少 3 位，密码至少 4 位
      - 用户名仅允许字母、数字、下划线、中文
      - 用户名不可重复
      - 密码明文不做任何持久化，仅存密文 + 盐值
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
    users[username] = {
        "password_hash": password_hash,
        "salt": salt,
        "created_at": _now_str(),
    }

    if not _save_users(role, users):
        return False, "注册失败：无法写入用户文件"

    return True, f"注册成功！角色: {role}，用户名: {username}"


# ====================== 登录校验 ======================

def login_user(role: str, username: str, password: str) -> Tuple[bool, str, Optional[str]]:
    """
    登录校验。
    返回 (success, message, returned_role_or_None)。
    成功时 returned_role 为角色标识；失败时为 None。
    为防止暴力破解，无论用户是否存在，统一返回模糊提示。
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
    """
    交互式登录/注册流程入口。
    启动 → 选角色 → 登录 or 注册 → 返回 (current_role, username)。
    返回 (None, None) 表示用户放弃。
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

        ok, msg = register_user(role, username, password)
        if ok:
            print(f"  ✅ {msg}")
            return role, username
        else:
            print(f"  ❌ {msg}")
            return auth_interactive()

    return None, None


def _getpass(prompt: str) -> str:
    """
    跨平台安全密码输入。
    Windows 不支持 getpass 回显控制，回退到 input。
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
    """当前时间字符串。"""
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
    ok, msg = register_user(ROLE_CONSUMER, "test_reg_user", "pass1234")
    print(f"   注册 test_reg_user → {'✅' if ok else '❌'} {msg}")
    # 清理测试账号
    if ok:
        users = _load_users(ROLE_CONSUMER)
        users.pop("test_reg_user", None)
        _save_users(ROLE_CONSUMER, users)
        print("   (已清理测试账号)")

    print("\n=== 自检完成 ===")
