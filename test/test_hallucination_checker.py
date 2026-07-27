import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import importlib


def _assert(condition, label):
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


failures = 0

checker = importlib.import_module("tools.hallucination_checker")
checker.reset_session_stats("对话一")

tool_results = [
    {
        "status": "success",
        "data": {
            "名称": "智能摄像头",
            "商品ID": "SP001",
            "售价": 199,
        },
    },
    [
        {
            "订单号": "O20260727001",
            "实付金额": 88.5,
            "商品名称": "无线耳机",
        }
    ],
]

safe = checker.check("智能摄像头售价199元，订单 O20260727001 实付88.5元。", tool_results, session_name="对话一")
failures += _assert(safe["score"] <= 0.3 and not safe["risk"], "工具数据一致时低风险")

risky = checker.check("智能手表售价399元，订单 O99999999999 实付1000元，保证今天送达。", tool_results, session_name="对话一")
failures += _assert(risky["score"] > 0.3 and risky["risk"], "数据不匹配和过度承诺时高风险")
failures += _assert(any(item["type"] == "blacklist" for item in risky["issues"]), "过度承诺词被标记")
failures += _assert(any(item["type"] == "order_id" for item in risky["issues"]), "不存在订单号被标记")

stats = checker.get_session_stats("对话一")
failures += _assert(stats["total"] == 2 and stats["risk_count"] == 1 and stats["risk_ratio"] == 0.5, "会话幻觉占比统计正确")


print("=" * 60)
print(f"  通过: {5 - failures}  失败: {failures}  总计: 5")
raise SystemExit(1 if failures else 0)
