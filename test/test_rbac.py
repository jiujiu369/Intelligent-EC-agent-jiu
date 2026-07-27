# test_rbac.py
# RBAC 权限专项测试，可独立运行。
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import json

from tools.rbac import *
from tools.data_loader import *
from tools.schema import tool_schemas


_pass, _fail = 0, 0


def _assert(condition, label):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  [PASS] {label}")
    else:
        _fail += 1
        print(f"  [FAIL] {label}")


def _run_case(label, fn):
    try:
        fn()
    except Exception as exc:
        _assert(False, f"{label} -> {type(exc).__name__}: {exc}")


def _dispatch_with_rbac(func_name, role, **kwargs):
    if not check_permission(func_name, role):
        return {"status": "denied", "msg": get_permission_denied_msg(func_name, role)}
    mapping = {
        "query_goods": query_goods,
        "update_goods": update_goods,
        "query_stock": query_stock,
        "query_order": query_order,
        "create_aftersale_ticket": create_aftersale_ticket,
        "export_sales_report": export_sales_report,
    }
    return mapping[func_name](**kwargs)


def test_permission_whitelist():
    _assert(len(get_allowed_tools(ROLE_CONSUMER)) == 5, "consumer 权限白名单为 5 个工具")
    _assert(len(get_allowed_tools(ROLE_MERCHANT)) == 7, "merchant 权限白名单为 7 个工具")
    _assert("update_goods" not in get_allowed_tools(ROLE_CONSUMER), "consumer 无 update_goods 权限")
    _assert("export_sales_report" in get_allowed_tools(ROLE_MERCHANT), "merchant 有 export_sales_report 权限")


def test_schema_filter():
    consumer_names = {s["function"]["name"] for s in get_filtered_schemas(ROLE_CONSUMER, tool_schemas)}
    merchant_names = {s["function"]["name"] for s in get_filtered_schemas(ROLE_MERCHANT, tool_schemas)}
    _assert("update_goods" not in consumer_names, "consumer schemas 不含 update_goods")
    _assert("export_sales_report" not in consumer_names, "consumer schemas 不含 export_sales_report")
    _assert("update_goods" in merchant_names, "merchant schemas 含 update_goods")
    _assert("export_sales_report" in merchant_names, "merchant schemas 含 export_sales_report")


def test_permission_denied_dispatch():
    result = _dispatch_with_rbac("update_goods", ROLE_CONSUMER, goods_id="SP001", update_info={"售价": 1})
    _assert(isinstance(result, dict) and result.get("status") == "denied", "consumer 调 update_goods 返回 status=denied")


def test_query_goods_masking():
    goods = query_goods(goods_id="SP001")
    consumer_view = mask_tool_result("query_goods", goods, ROLE_CONSUMER)
    merchant_view = mask_tool_result("query_goods", goods, ROLE_MERCHANT)
    _assert(bool(consumer_view), "query_goods 返回测试商品")
    _assert(all("上架状态" not in item for item in consumer_view), "consumer 看不到 上架状态 字段")
    _assert(any("上架状态" in item for item in merchant_view), "merchant 可以看到 上架状态 字段")


def test_role_prompt_suffix():
    consumer_prompt = get_role_prompt_suffix(ROLE_CONSUMER)
    merchant_prompt = get_role_prompt_suffix(ROLE_MERCHANT)
    _assert("消费者" in consumer_prompt and "无法修改商品" in consumer_prompt, "consumer 角色提示词包含正确角色描述")
    _assert("商家" in merchant_prompt and "export_sales_report" in merchant_prompt, "merchant 角色提示词包含角色描述和权限说明")


print("=" * 60)
print("  test_rbac.py")
print("=" * 60)
for name, case in [
    ("权限白名单验证", test_permission_whitelist),
    ("Schema 过滤验证", test_schema_filter),
    ("越权拦截验证", test_permission_denied_dispatch),
    ("query_goods 脱敏验证", test_query_goods_masking),
    ("角色提示词注入验证", test_role_prompt_suffix),
]:
    _run_case(name, case)

print("=" * 60)
print(f"  通过: {_pass}  失败: {_fail}  总计: {_pass + _fail}")
raise SystemExit(0 if _fail == 0 else 1)
