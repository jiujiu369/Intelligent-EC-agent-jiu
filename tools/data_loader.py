import functools
import json
import os
from typing import List, Dict, Optional
import config
from tools.error_handler import atomic_load_json, atomic_save_json, safe_tool_call, wrap_tool_result
from utils.logger import get_logger

logger = get_logger(__name__)

# ====================== 路径配置（不要修改，适配你现有目录）======================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "datas_dir"))
GOODS_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "goods_json"))
STOCK_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "stock_json"))
ORDER_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "order_json"))
AFTERSALE_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "aftersale_json"))

# ====================== 内存缓存全局变量 ======================
_goods_data: List[Dict] = []
_stock_data: List[Dict] = []
_order_data: List[Dict] = []
_aftersale_data: List[Dict] = []


def load_json(file_path: str) -> List[Dict]:
    """加载json数组文件"""
    try:
        data = atomic_load_json(file_path)
        logger.info(f"JSON读取 path={file_path} count={len(data)}")
        return data
    except Exception as exc:
        logger.error(f"JSON读取异常 path={file_path} error={exc}")
        return []


def save_json(file_path: str, data: List[Dict]):
    """写入json文件（持久化保存修改）"""
    try:
        atomic_save_json(file_path, data)
        logger.info(f"JSON写入 path={file_path} count={len(data)}")
    except Exception as exc:
        logger.error(f"JSON写入异常 path={file_path} error={exc}")


# 程序启动自动加载全部数据
def init_data():
    global _goods_data, _stock_data, _order_data, _aftersale_data
    _goods_data = load_json(GOODS_PATH)
    _stock_data = load_json(STOCK_PATH)
    _order_data = load_json(ORDER_PATH)
    _aftersale_data = load_json(AFTERSALE_PATH)
    logger.info("业务模拟数据加载完成")


# ===================== 对外提供接口（Agent直接调用）=====================
def query_goods(goods_id: Optional[str] = None, goods_name: Optional[str] = None) -> List[Dict]:
    """
    查询商品信息
    :param goods_id: 商品ID，精确查询
    :param goods_name: 商品名称，模糊匹配
    :return: 商品列表
    """
    result = []
    for item in _goods_data:
        match = False
        # 匹配商品ID，忽略大小写
        if goods_id:
            item_id = item.get("商品ID", "").lower()
            if item_id == goods_id.lower():
                match = True
        # 模糊匹配商品名称
        if goods_name:
            item_name = item.get("名称", "")
            if goods_name in item_name:
                match = True
        # 无参数返回全部商品
        if (not goods_id and not goods_name) or match:
            result.append(item)
    return result


def update_goods(goods_id: str, update_info: Optional[Dict] = None) -> Dict:
    """
    修改商品信息
    :param goods_id: 需要修改的商品ID
    :param update_info: 待更新字段字典
    :return: 修改后的商品 / 失败提示
    """
    if not goods_id:
        return {"status": "fail", "msg": "缺少商品ID"}
    if not isinstance(update_info, dict) or not update_info:
        return {"status": "fail", "msg": "缺少要修改的字段和值，例如：修改商品SP123售价为99"}

    # 别名映射：把大模型输出的常见key翻译成JSON真实字段名
    alias_map = {
        "price": "售价",
        "价格": "售价",
        "商品价格": "售价",
        "商品售价": "售价",
        "sale_price": "售价",
        "selling_price": "售价",
    }
    real_update = {}
    for key, val in update_info.items():
        real_key = alias_map.get(str(key).strip(), key)
        real_update[real_key] = val

    for item in _goods_data:
        # 重点：把 goods_id → 商品ID，兼容大小写
        if item["商品ID"].upper() == str(goods_id).upper():
            item.update(real_update)
            save_json(GOODS_PATH, _goods_data)
            return {"status": "success", "data": item}
    return {"status": "fail", "msg": f"未找到商品ID：{goods_id}"}



def query_stock(goods_id: Optional[str] = None) -> List[Dict]:
    """查询库存，支持商品id筛选"""
    if not goods_id:
        return _stock_data
    # 数据字段为【商品ID】，兼容大小写
    return [s for s in _stock_data if str(s.get("商品ID", "")).upper() == str(goods_id).upper()]


def query_order(order_id: Optional[str] = None, goods_id: Optional[str] = None) -> List[Dict]:
    """
    查询订单
    :param order_id: 订单号精确查询
    :param goods_id: 根据商品id查询所有相关订单
    """
    res = []
    for order in _order_data:
        if order_id and order.get("订单号") == order_id:
            res.append(order)
        elif goods_id and str(order.get("商品ID", "")).upper() == str(goods_id).upper():
            res.append(order)
        elif not order_id and not goods_id:
            res.append(order)
    return res


def create_aftersale_ticket(new_ticket: Dict) -> Dict:
    """
    创建售后工单
    :param new_ticket: 工单完整字典，支持中英文字段名
    工单字段：工单ID、关联订单号、问题类型、处理状态、处理记录、创建时间
    """
    import datetime
    # 别名映射：把大模型/菜单常用key统一翻译成JSON真实字段名
    alias_map = {
        "ticket_id": "工单ID",
        "order_id": "关联订单号",
        "problem": "问题类型",
        "status": "处理状态",
        "工单ID": "工单ID",
        "关联订单号": "关联订单号",
        "问题类型": "问题类型",
        "处理状态": "处理状态",
    }
    normalized = {}
    for k, v in new_ticket.items():
        real_key = alias_map.get(str(k).strip(), k)
        normalized[real_key] = v
    # 补全默认字段，保证与历史工单结构一致
    normalized.setdefault("工单ID", f"WG{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}")
    normalized.setdefault("关联订单号", "")
    normalized.setdefault("问题类型", "未分类")
    normalized.setdefault("处理状态", "待处理")
    normalized.setdefault("处理记录", "暂未安排客服跟进")
    normalized.setdefault("创建时间", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    _aftersale_data.append(normalized)
    save_json(AFTERSALE_PATH, _aftersale_data)
    return {"status": "success", "ticket": normalized}


def get_all_orders() -> List[Dict]:
    """获取全部订单，用于销售报表统计"""
    return _order_data


def export_sales_report(start_time: str = None, end_time: str = None) -> Dict:
    """
    导出销售报表，统计订单总额、订单数量
    :param start_time: YYYY-MM-DD
    :param end_time: YYYY-MM-DD
    """
    import datetime
    filter_orders = []
    for order in _order_data:
        order_time_str = order.get("下单时间", "")
        try:
            # 订单时间格式为 YYYY-MM-DD HH:MM:SS
            order_dt = datetime.datetime.strptime(order_time_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            filter_orders.append(order)
            continue

        in_range = True
        if start_time:
            s_dt = datetime.datetime.strptime(start_time, "%Y-%m-%d")
            if order_dt < s_dt:
                in_range = False
        if end_time:
            e_dt = datetime.datetime.strptime(end_time, "%Y-%m-%d")
            # 结束日期按当天结束（23:59:59）计算，避免漏掉当天订单
            e_dt = e_dt.replace(hour=23, minute=59, second=59)
            if order_dt > e_dt:
                in_range = False
        if in_range:
            filter_orders.append(order)

    total_amount = sum(item.get("实付金额", 0) for item in filter_orders)
    return {
        "start_time": start_time,
        "end_time": end_time,
        "order_count": len(filter_orders),
        "total_sales": round(total_amount, 2),
        "order_list": filter_orders
    }


def _guard_tool_result(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = wrap_tool_result(func.__name__, func(*args, **kwargs))
        if isinstance(result, dict) and result.get("msg") == "未找到匹配信息":
            logger.warning(f"工具查无结果 name={func.__name__} args={args} kwargs={kwargs}")
        return result

    return safe_tool_call(wrapper)


query_goods = _guard_tool_result(query_goods)
update_goods = _guard_tool_result(update_goods)
query_stock = _guard_tool_result(query_stock)
query_order = _guard_tool_result(query_order)
create_aftersale_ticket = _guard_tool_result(create_aftersale_ticket)
export_sales_report = _guard_tool_result(export_sales_report)


# 程序启动自动执行加载
init_data()


# ============ 本地调试测试代码（写完可以运行测试）============
if __name__ == "__main__":
    # 测试查询商品
    goods = query_goods()
    print("商品数量：", len(goods))

    # 测试查询订单
    orders = query_order()
    print("订单数量：", len(orders))
