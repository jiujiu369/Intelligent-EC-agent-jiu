# test_rag.py
# RAG 检索效果测试，可独立运行。
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
from embedding.rag_pipeline import collection, rag_search


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


def _has_doc_type(results, doc_type):
    """执行 ``_has_doc_type`` 对应的项目处理逻辑。
    :param results: 传入 ``results`` 的业务数据。
    :param doc_type: 传入 ``doc_type`` 的业务数据。
    :return: 条件成立时返回 ``True``，否则返回 ``False``。
    """
    return any(item.get("meta", {}).get("doc_type") == doc_type for item in results if isinstance(item, dict))


def _is_low_similarity_or_empty(results):
    """执行 ``_is_low_similarity_or_empty`` 对应的项目处理逻辑。
    :param results: 传入 ``results`` 的业务数据。
    :return: 条件成立时返回 ``True``，否则返回 ``False``。
    """
    if not results:
        return True
    for item in results:
        distance = item.get("distance") if isinstance(item, dict) else None
        if distance is None or distance > 1.5:
            return True
    return False


def test_collection_connected():
    """验证 collection connected 场景符合预期行为。"""
    ok = collection is not None and hasattr(collection, "count") and collection.count() > 0
    _assert(ok, "向量库正常连接且 collection.count() > 0")


def test_goods_search():
    """验证 goods search 场景符合预期行为。"""
    results = rag_search("家用监控摄像头", top_k=3)
    _assert(_has_doc_type(results, "goods_info"), "已知商品名检索返回 goods_info 类型")


def test_policy_search():
    """验证 policy search 场景符合预期行为。"""
    results = rag_search("退货政策", top_k=3)
    _assert(_has_doc_type(results, "service_rule"), "政策检索返回 service_rule 类型")


def test_low_similarity_query():
    """验证 low similarity query 场景符合预期行为。"""
    results = rag_search("xyz123xyz", top_k=3)
    _assert(_is_low_similarity_or_empty(results), "低相似度乱码查询返回空或高 distance")


def test_top_k():
    """验证 top k 场景符合预期行为。"""
    results = rag_search("退货政策", top_k=1)
    _assert(len(results) <= 1, "top_k=1 最多返回 1 条")


print("=" * 60)
print("  test_rag.py")
print("=" * 60)
for name, case in [
    ("向量库连接", test_collection_connected),
    ("商品检索", test_goods_search),
    ("政策检索", test_policy_search),
    ("低相似度查询", test_low_similarity_query),
    ("top_k 参数", test_top_k),
]:
    _run_case(name, case)

print("=" * 60)
print(f"  通过: {_pass}  失败: {_fail}  总计: {_pass + _fail}")
raise SystemExit(0 if _fail == 0 else 1)
