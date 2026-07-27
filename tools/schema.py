# AI 大模型「工具说明书」 + 调度器「函数地址簿」
# 工具描述与参数定义
tool_schemas = [
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "检索客服知识库，查询售后政策、活动规则、平台说明等文档内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户的问题或检索关键词"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回文档片段数量，默认2",
                        "default": 2
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_goods",
            "description": "查询商品信息，支持按商品ID精确查询或按商品名称模糊查询",
            "parameters": {
                "type": "object",
                "properties": {
                    "goods_id": {
                        "type": "string",
                        "description": "商品唯一ID，精确查询，可选"
                    },
                    "goods_name": {
                        "type": "string",
                        "description": "商品名称，模糊匹配，可选"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_goods",
            "description": "修改商品信息。修改价格时 update_info 可以使用 售价 或 价格，系统会统一更新商品数据里的【售价】字段。其他字段请使用原始字段名：name、分类、规格、上架状态、图文摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "goods_id": {"type": "string", "description": "商品编号，对应商品数据里的【商品ID】"
                    },
                    "update_info": {
                        "type": "object",
                        "description":"待更新的字段和值，例如 {'售价':65} 或 {'价格':65}。如果用户没有提供新值，不要调用本工具，应先询问要改成多少。"
                    }
                },
                "required": ["goods_id", "update_info"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_stock",
            "description": "查询商品库存，支持商品ID筛选，如果不传返回全部库存信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "goods_id": {
                        "type": "string",
                        "description": "商品ID，可选"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_order",
            "description": "查询订单信息，支持根据订单号或商品ID查询",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单编号，精确查询，可选"
                    },
                    "goods_id": {
                        "type": "string",
                        "description": "商品ID，查询该商品相关订单，可选"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_sales_report",
            "description": "生成销售统计报表，按时间范围统计订单数量和总销售额",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "起始日期，格式 YYYY-MM-DD，可选"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD，可选"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_aftersale_ticket",
            "description": "创建新的售后工单，录入订单号、问题描述、状态等信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_ticket": {
                        "type": "object",
                        "description": "工单完整信息，包含 ticket_id、order_id、problem、status 等字段"
                    }
                },
                "required": ["new_ticket"]
            }
        }
    }
]


#配置路径
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)


# 工具名称映射【工具调度器核心：名称 → 实际函数】
from tools.data_loader import (
    query_goods,
    update_goods,
    query_stock,
    query_order,
    create_aftersale_ticket,
    export_sales_report,
    get_all_orders
)

from embedding.rag_pipeline import rag_search
from utils.rate_limiter import cache_query_goods, cache_rag_search


# 全部工具已实现，名称→函数完整映射
func_mapping = {
    "query_goods": cache_query_goods(query_goods),
    "update_goods": update_goods,
    "query_stock": query_stock,
    "query_order": query_order,
    "create_aftersale_ticket": create_aftersale_ticket,
	"export_sales_report": export_sales_report,
    "rag_search": cache_rag_search(rag_search)
}
