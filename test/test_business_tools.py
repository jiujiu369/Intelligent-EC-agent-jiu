# test_business_tools.py
# 业务工具全场景回归，可独立运行，不经过 LLM。
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import os
import uuid

from tools.data_loader import *
import tools.data_loader as data_loader


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


def _run_case(label, fn):
    """执行单个测试用例，并将异常转换为失败记录。
    :param label: 用于日志或测试输出的说明标签。
    :param fn: 需要调用、包装或测试的函数。
    """
    try:
        fn()
    except Exception as exc:
        _assert(False, f"{label} -> {type(exc).__name__}: {exc}")


def _backup_files():
    """读取测试涉及的文件并保存可恢复的原始内容。
    :return: 返回函数处理得到的结果。
    """
    backups = {}
    for path in [GOODS_PATH, AFTERSALE_PATH]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                backups[path] = f.read()
    return backups


def _restore_files(backups):
    """将测试修改过的文件恢复为备份内容。
    :param backups: 需要恢复的文件备份映射。
    """
    for path, content in backups.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    data_loader.init_data()


def _first_list_item(value):
    """执行 ``_first_list_item`` 对应的项目处理逻辑。
    :param value: 需要转换、缓存或检查的值。
    :return: 返回函数处理得到的结果。
    """
    return value[0] if isinstance(value, list) and value else None


def test_query_goods():
    """验证 query goods 场景符合预期行为。"""
    first = _first_list_item(query_goods())
    _assert(first is not None, "query_goods 全量查询有数据")
    goods_id = first["商品ID"]
    name_part = first["名称"][:4]
    exact = query_goods(goods_id=goods_id)
    fuzzy = query_goods(goods_name=name_part)
    missing = query_goods(goods_id="NO_SUCH_GOODS_ID")
    _assert(isinstance(exact, list) and exact and exact[0]["商品ID"] == goods_id, "query_goods 精确 ID 查询")
    _assert(isinstance(fuzzy, list) and any(name_part in item["名称"] for item in fuzzy), "query_goods 模糊名称查询")
    _assert(isinstance(missing, dict) and missing.get("msg"), "query_goods 不存在 ID 返回友好提示")


def test_query_stock():
    """验证 query stock 场景符合预期行为。"""
    first = _first_list_item(query_stock())
    _assert(first is not None, "query_stock 全量查询有数据")
    goods_id = first["商品ID"]
    exact = query_stock(goods_id=goods_id)
    all_items = query_stock()
    missing = query_stock(goods_id="NO_SUCH_GOODS_ID")
    _assert(isinstance(exact, list) and exact and exact[0]["商品ID"] == goods_id, "query_stock 精确 ID 查询")
    _assert(isinstance(all_items, list) and len(all_items) >= len(exact), "query_stock 全量查询")
    _assert(isinstance(missing, dict) and missing.get("msg"), "query_stock 不存在 ID 返回友好提示")


def test_query_order():
    """验证 query order 场景符合预期行为。"""
    first = _first_list_item(query_order())
    _assert(first is not None, "query_order 全量查询有数据")
    order_id = first["订单号"]
    goods_id = first["商品ID"]
    by_order = query_order(order_id=order_id)
    by_goods = query_order(goods_id=goods_id)
    by_time = query_order(start_time="2026-06-01", end_time="2026-06-30")
    _assert(isinstance(by_order, list) and by_order and by_order[0]["订单号"] == order_id, "query_order 订单号查询")
    _assert(isinstance(by_goods, list) and any(item["商品ID"] == goods_id for item in by_goods), "query_order 商品 ID 查询")
    _assert(isinstance(by_time, list), "query_order 时间筛选返回列表")


def test_create_aftersale_ticket():
    """验证 create aftersale ticket 场景符合预期行为。"""
    ticket_id = f"TEST-{uuid.uuid4().hex[:8]}"
    created = create_aftersale_ticket({
        "ticket_id": ticket_id,
        "order_id": "DD001",
        "problem": "测试问题",
        "status": "待处理",
    })
    incomplete = create_aftersale_ticket({"ticket_id": f"TEST-{uuid.uuid4().hex[:8]}"})
    _assert(isinstance(created, dict) and created.get("status") == "success", "create_aftersale_ticket 正常创建")
    _assert(isinstance(incomplete, dict) and incomplete.get("status") == "fail", "create_aftersale_ticket 字段不完整时拒绝")


def test_update_goods():
    """验证 update goods 场景符合预期行为。"""
    first = _first_list_item(query_goods())
    goods_id = first["商品ID"]
    new_price = first["售价"] + 1
    price_result = update_goods(goods_id, {"售价": new_price})
    name_result = update_goods(goods_id, {"名称": first["名称"] + "测试"})
    missing = update_goods("NO_SUCH_GOODS_ID", {"售价": 1})
    _assert(isinstance(price_result, dict) and price_result.get("status") == "success" and price_result["data"]["售价"] == new_price, "update_goods 修改售价")
    _assert(isinstance(name_result, dict) and name_result.get("status") == "success" and name_result["data"]["名称"].endswith("测试"), "update_goods 修改名称")
    _assert(isinstance(missing, dict) and missing.get("status") == "fail", "update_goods 不存在商品返回失败")


def test_export_sales_report():
    """验证 export sales report 场景符合预期行为。"""
    all_report = export_sales_report()
    ranged = export_sales_report(start_time="2026-06-03", end_time="2026-06-03")
    empty = export_sales_report(start_time="2099-01-01", end_time="2099-01-02")
    _assert(isinstance(all_report, dict) and all_report.get("order_count", 0) > 0, "export_sales_report 全部时间")
    _assert(isinstance(ranged, dict) and ranged.get("order_count", 0) >= 0, "export_sales_report 指定日期范围")
    _assert(isinstance(empty, dict) and empty.get("order_count") == 0, "export_sales_report 无数据范围")


def test_get_all_orders():
    """验证 get all orders 场景符合预期行为。"""
    orders = get_all_orders()
    _assert(isinstance(orders, list) and len(orders) > 0, "get_all_orders 返回全部订单列表")
    _assert(all("订单号" in item for item in orders[:3]), "get_all_orders 订单结构包含订单号")


print("=" * 60)
print("  test_business_tools.py")
print("=" * 60)
backup = _backup_files()
try:
    for name, case in [
        ("query_goods", test_query_goods),
        ("query_stock", test_query_stock),
        ("query_order", test_query_order),
        ("create_aftersale_ticket", test_create_aftersale_ticket),
        ("update_goods", test_update_goods),
        ("export_sales_report", test_export_sales_report),
        ("get_all_orders", test_get_all_orders),
    ]:
        _run_case(name, case)
finally:
    _restore_files(backup)

print("=" * 60)
print(f"  通过: {_pass}  失败: {_fail}  总计: {_pass + _fail}")
raise SystemExit(0 if _fail == 0 else 1)
