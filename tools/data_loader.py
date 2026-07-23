import json
import os
from typing import List, Dict, Optional

# ====================== 路径配置（不要修改，适配你现有目录）======================
BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../datas"))
GOODS_PATH = os.path.join(BASE_PATH, "货品基础数据.json")
STOCK_PATH = os.path.join(BASE_PATH, "库存数据.json")
ORDER_PATH = os.path.join(BASE_PATH, "订单数据.json")
AFTERSALE_PATH = os.path.join(BASE_PATH, "售后工单.json")

# ====================== 内存缓存全局变量 ======================
_goods_data: List[Dict] = []
_stock_data: List[Dict] = []
_order_data: List[Dict] = []
_aftersale_data: List[Dict] = []


def load_json(file_path: str) -> List[Dict]:
    """加载json数组文件"""
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(file_path: str, data: List[Dict]):
    """写入json文件（持久化保存修改）"""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 程序启动自动加载全部数据
def init_data():
    global _goods_data, _stock_data, _order_data, _aftersale_data
    _goods_data = load_json(GOODS_PATH)
    _stock_data = load_json(STOCK_PATH)
    _order_data = load_json(ORDER_PATH)
    _aftersale_data = load_json(AFTERSALE_PATH)
    print("✅ 业务模拟数据加载完成！")


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
        if goods_id and item.get("goods_id") == goods_id:
            result.append(item)
        elif goods_name and goods_name in item.get("goods_name", ""):
            result.append(item)
        elif not goods_id and not goods_name:
            result.append(item)
    return result


def update_goods(goods_id: str, update_info: Dict) -> Dict:
    """
    修改商品信息
    :param goods_id: 需要修改的商品ID
    :param update_info: 待更新字段字典 {"price":99, "status":"下架"}
    :return: 修改后的商品 / 失败提示
    """
    for item in _goods_data:
        if item["goods_id"] == goods_id:
            item.update(update_info)
            save_json(GOODS_PATH, _goods_data)
            return {"status": "success", "data": item}
    return {"status": "fail", "msg": "未找到对应商品"}


def query_stock(goods_id: Optional[str] = None) -> List[Dict]:
    """查询库存，支持商品id筛选"""
    if not goods_id:
        return _stock_data
    return [s for s in _stock_data if s.get("goods_id") == goods_id]


def query_order(order_id: Optional[str] = None, goods_id: Optional[str] = None) -> List[Dict]:
    """
    查询订单
    :param order_id: 订单号精确查询
    :param goods_id: 根据商品id查询所有相关订单
    """
    res = []
    for order in _order_data:
        if order_id and order.get("order_id") == order_id:
            res.append(order)
        elif goods_id and order.get("goods_id") == goods_id:
            res.append(order)
        elif not order_id and not goods_id:
            res.append(order)
    return res


def create_aftersale_ticket(new_ticket: Dict) -> Dict:
    """
    创建售后工单
    :param new_ticket: 工单完整字典，必须包含ticket_id, order_id, problem, status等字段
    """
    _aftersale_data.append(new_ticket)
    save_json(AFTERSALE_PATH, _aftersale_data)
    return {"status": "success", "ticket": new_ticket}


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
        order_time_str = order.get("order_time", "")
        try:
            order_dt = datetime.datetime.strptime(order_time_str, "%Y-%m-%d")
        except:
            filter_orders.append(order)
            continue

        in_range = True
        if start_time:
            s_dt = datetime.datetime.strptime(start_time, "%Y-%m-%d")
            if order_dt < s_dt:
                in_range = False
        if end_time:
            e_dt = datetime.datetime.strptime(end_time, "%Y-%m-%d")
            if order_dt > e_dt:
                in_range = False
        if in_range:
            filter_orders.append(order)

    total_amount = sum(item.get("pay_amount", 0) for item in filter_orders)
    return {
        "start_time": start_time,
        "end_time": end_time,
        "order_count": len(filter_orders),
        "total_sales": round(total_amount, 2),
        "order_list": filter_orders
    }



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