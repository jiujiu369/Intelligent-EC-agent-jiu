# test_auth.py
# 登录模块完整测试 —— 覆盖文件初始化、密码安全、注册、登录、RBAC 集成
# ============================================================
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import json
import os
import shutil
import sys

# 如果 chromadb 等重依赖不可用，mock rag_pipeline 避免 import 时爆炸
try:
    from embedding.rag_pipeline import rag_search    # noqa: F401
except Exception:
    import embedding.rag_pipeline as _rag_mod
    # no-op placeholder
    if not hasattr(_rag_mod, "rag_search"):
        _rag_mod.rag_search = lambda **kw: []   # type: ignore[assignment]

# 直接导入 auth 模块，绕过 main_agent 的重依赖
from tools.auth_login import (
    _hash_password,
    verify_password,
    init_auth_files,
    register_user,
    login_user,
    _load_users,
    _save_users,
    _get_user_file,
    ROLE_CONSUMER,
    ROLE_MERCHANT,
    CONSUMER_FILE,
    MERCHANT_FILE,
)

# 导入 RBAC 做权限集成验证
from tools.rbac import (
    get_allowed_tools,
    check_permission,
    mask_goods_data,
)

# ====================== 测试工具 ======================

_pass, _fail = 0, 0

def _assert(condition, label):
    """记录测试断言结果，并输出对应的通过或失败信息。
    :param condition: 断言是否成立的布尔条件。
    :param label: 用于日志或测试输出的说明标签。
    """
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  [PASS] {label}")
    else:
        _fail += 1
        print(f"  [FAIL] {label}")

def _backup_files():
    """备份现有用户文件。
    :return: 返回函数处理得到的结果。
    """
    backups = {}
    for f in (CONSUMER_FILE, MERCHANT_FILE):
        if os.path.exists(f):
            with open(f, "r", encoding="utf-8") as fh:
                backups[f] = fh.read()
    return backups

def _restore_files(backups):
    """还原备份。
    :param backups: 需要恢复的文件备份映射。
    """
    for path, content in backups.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

def _cleanup_test_files():
    """清理测试中创建的文件。"""
    for f in (CONSUMER_FILE, MERCHANT_FILE):
        if os.path.exists(f):
            os.remove(f)


# ====================== 测试 1: 文件自动初始化 ======================

print("=" * 60)
print("  测试 1: 文件自动初始化")
print("=" * 60)

backup = _backup_files()
_cleanup_test_files()

# 调用初始化
init_auth_files()

_assert(os.path.exists(CONSUMER_FILE), "consumer_users.json 已创建")
_assert(os.path.exists(MERCHANT_FILE), "merchant_users.json 已创建")

# 验证文件内容结构
cons = _load_users(ROLE_CONSUMER)
merc = _load_users(ROLE_MERCHANT)
_assert("user1" in cons, "买家文件含预设账号 user1")
_assert("admin" in merc, "商家文件含预设账号 admin")

# 验证每个用户包含必要字段
for users_data, label in [(cons, "user1"), (merc, "admin")]:
    u = users_data.get(label, {})
    _assert("password_hash" in u, f"{label} 含 password_hash 字段")
    _assert("salt" in u, f"{label} 含 salt 字段")
    _assert("created_at" in u, f"{label} 含 created_at 字段")

_restore_files(backup)
print()


# ====================== 测试 2: 密码密文安全 ======================

print("=" * 60)
print("  测试 2: 密码密文安全")
print("=" * 60)

# 在测试隔离区重新初始化
backup = _backup_files()
_cleanup_test_files()
init_auth_files()

cons = _load_users(ROLE_CONSUMER)
merc = _load_users(ROLE_MERCHANT)

for users_data, label, plain in [
    (cons, "user1", "123456"),
    (merc, "admin", "admin123"),
]:
    u = users_data[label]
    # 密文 ≠ 明文
    _assert(u["password_hash"] != plain, f"{label} 密码密文 ≠ 明文")
    # 文件中不含明文字符串
    raw = json.dumps(users_data, ensure_ascii=False)
    _assert(plain not in raw, f"{label} 文件中不含明文 '{plain}'")
    # hash 和 salt 都是 64 位 hex (SHA-256 → 32 bytes → 64 hex chars)
    _assert(len(u["password_hash"]) == 64, f"{label} password_hash 为 64 位 hex")
    _assert(len(u["salt"]) == 64, f"{label} salt 为 64 位 hex")
    # 不同用户 salt 不同
    other_u = merc["admin"] if label == "user1" else cons["user1"]
    _assert(u["salt"] != other_u["salt"], f"{label} salt 与另一角色不同（独立 salt）")

_restore_files(backup)
print()


# ====================== 测试 3: 注册流程 ======================

print("=" * 60)
print("  测试 3: 账号注册")
print("=" * 60)

backup = _backup_files()
_cleanup_test_files()
init_auth_files()

# 成功注册
ok, msg = register_user(ROLE_CONSUMER, "test_consumer", "mypassword")
_assert(ok, f"注册 test_consumer → {msg}")
if ok:
    users = _load_users(ROLE_CONSUMER)
    _assert("test_consumer" in users, "test_consumer 已写入文件")
    _assert(verify_password(
        users["test_consumer"]["password_hash"],
        users["test_consumer"]["salt"],
        "mypassword",
    ), "注册后可用正确密码登录")

# 重复注册
ok, msg = register_user(ROLE_CONSUMER, "test_consumer", "another")
_assert(not ok, f"重复注册被拒绝 → {msg}")

# 用户名太短
ok, msg = register_user(ROLE_CONSUMER, "ab", "12345678")
_assert(not ok, f"用户名过短被拒绝 → {msg}")

# 密码太短
ok, msg = register_user(ROLE_CONSUMER, "valid_user", "123")
_assert(not ok, f"密码过短被拒绝 → {msg}")

# 非法字符
ok, msg = register_user(ROLE_CONSUMER, "bad<user>", "123456")
_assert(not ok, f"非法字符被拒绝 → {msg}")

# 空参数
ok, msg = register_user(ROLE_CONSUMER, "", "")
_assert(not ok, f"空参数被拒绝 → {msg}")

_restore_files(backup)
print()


# ====================== 测试 4: 登录校验 ======================

print("=" * 60)
print("  测试 4: 登录校验")
print("=" * 60)

backup = _backup_files()
_cleanup_test_files()
init_auth_files()

# 正��登录 - consumer
ok, msg, role = login_user(ROLE_CONSUMER, "user1", "123456")
_assert(ok and role == ROLE_CONSUMER, f"consumer 正确登录 → {msg}")

# 正确登录 - merchant
ok, msg, role = login_user(ROLE_MERCHANT, "admin", "admin123")
_assert(ok and role == ROLE_MERCHANT, f"merchant 正确登录 → {msg}")

# 错误密码
ok, msg, role = login_user(ROLE_CONSUMER, "user1", "wrongpass")
_assert(not ok and role is None, f"错误密码被拒绝 → {msg}")

# 不存在用户（同一提示）
ok, msg, role = login_user(ROLE_CONSUMER, "ghost_user", "anything")
_assert(not ok and role is None, f"不存在用户被拒绝 → {msg}")
_assert("用户名或密码错误" in msg, "不存在用户提示为通用消息（防用户枚举）")

# 错误密码 → 模糊提示
ok, msg, role = login_user(ROLE_CONSUMER, "user1", "wrong")
_assert("用户名或密码错误" in msg, "错误密码提示为通用消息（防暴力破解）")

# 空参数
ok, msg, role = login_user(ROLE_CONSUMER, "", "")
_assert(not ok, f"空参数被拒绝 → {msg}")

# 无效角色
ok, msg, role = login_user("admin_role", "user1", "123456")
_assert(not ok, f"无效角色被拒绝 → {msg}")

_restore_files(backup)
print()


# ====================== 测试 5: RBAC 权限集成 ======================

print("=" * 60)
print("  测试 5: RBAC 权限集成验证")
print("=" * 60)

# 模拟登录后获取 current_role
backup = _backup_files()
_cleanup_test_files()
init_auth_files()
ok, msg, role = login_user(ROLE_CONSUMER, "user1", "123456")
_assert(ok, f"买家登录成功 → {msg}")
consumer_role = role

ok, msg, role = login_user(ROLE_MERCHANT, "admin", "admin123")
_assert(ok, f"商家登录成功 → {msg}")
merchant_role = role

# 验证权限与 RBAC 一致
consumer_tools = get_allowed_tools(consumer_role)   # type: ignore[arg-type]
merchant_tools = get_allowed_tools(merchant_role)   # type: ignore[arg-type]

_assert(
    "update_goods" not in consumer_tools
    and "export_sales_report" not in consumer_tools
    and "query_stock" not in consumer_tools,
    "买家无权 query_stock / update_goods / export_sales_report",
)
_assert(
    "update_goods" in merchant_tools and "export_sales_report" in merchant_tools,
    "商家拥有全部工具权限",
)

# 越权拦截
_assert(
    not check_permission("update_goods", consumer_role),   # type: ignore[arg-type]
    "第二层防护：买家 update_goods 被 check_permission 拦截",
)
_assert(
    not check_permission("query_stock", consumer_role),     # type: ignore[arg-type]
    "第二层防护：买家 query_stock 被 check_permission 拦截",
)
_assert(
    check_permission("update_goods", merchant_role),       # type: ignore[arg-type]
    "商家 update_goods 通过 check_permission",
)

# 数据脱敏
sample_goods = [
    {"商品ID": "SP001", "名称": "商品A", "售价": 100, "上架状态": "已��架"},
    {"商品ID": "SP002", "名称": "商品B", "售价": 200, "上架状态": "已下架"},
]
masked = mask_goods_data(sample_goods, consumer_role)      # type: ignore[arg-type]
_assert(
    all("上架状态" not in item for item in masked),
    "买家 query_goods 结果不含上架状态（数据脱敏）",
)
_assert(
    all("售价" in item for item in masked),
    "买家仍可见售价等非敏感字段",
)

unmasked = mask_goods_data(sample_goods, merchant_role)    # type: ignore[arg-type]
_assert(
    all("上架状态" in item for item in unmasked),
    "商家 query_goods 结果含全部字段（不脱敏）",
)

_restore_files(backup)
print()


# ====================== 测试 6: 密码哈希独立性 ======================

print("=" * 60)
print("  测试 6: 密码哈希独立性")
print("=" * 60)

# 相同密码、不同盐值 → 不同密文
h1, s1 = _hash_password("same_password")
h2, s2 = _hash_password("same_password")
_assert(s1 != s2, "每次随机生成不同盐值")
_assert(h1 != h2, "相同明文 + 不同盐 → 不同密文")

# 验证两个 hash 都能各自校验通过
_assert(verify_password(h1, s1, "same_password"), "hash1 校验通过")
_assert(verify_password(h2, s2, "same_password"), "hash2 校验通过")

print()


# ====================== 汇总 ======================

print("=" * 60)
print("  测试结果汇总")
print("=" * 60)
total = _pass + _fail
for test_name in ["文件自动初始化", "密码密文安全", "账号注册", "登录校验", "RBAC 权限集成", "密码哈希独立性"]:
    # 无法反向映射，仅打印汇总
    pass
print(f"  通过: {_pass}  失败: {_fail}  总计: {total}")
if _fail == 0:
    print(f"  ✅ 全部通过 ({_pass}/{total})")
else:
    print(f"  ❌ 存在 {_fail} 项失败")
print()

sys.exit(0 if _fail == 0 else 1)
