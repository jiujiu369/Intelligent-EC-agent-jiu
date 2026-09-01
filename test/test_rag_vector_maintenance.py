# test_rag_vector_maintenance.py
# RAG 向量维护测试：单商品更新、全量重建回滚、去重入库、关键词降级检索
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)
import importlib.util
import os
import shutil
import sys
import types


class FakeCollection:
    def __init__(self):
        """初始化对象所需的状态和依赖。"""
        self.items = {}
        self.fail_add = False

    def get(self, ids=None, where=None, include=None):
        """根据键读取配置项、缓存项或集合数据。
        :param ids: 传入 ``ids`` 的业务数据。
        :param where: 传入 ``where`` 的业务数据。
        :param include: 传入 ``include`` 的业务数据。
        :return: 返回函数处理得到的结果。
        """
        matched = []
        if ids is not None:
            matched = [item_id for item_id in ids if item_id in self.items]
        elif where is not None:
            matched = [
                item_id for item_id, item in self.items.items()
                if all(item["metadata"].get(k) == v for k, v in where.items())
            ]
        else:
            matched = list(self.items.keys())
        return {
            "ids": matched,
            "documents": [self.items[item_id]["document"] for item_id in matched],
            "metadatas": [self.items[item_id]["metadata"] for item_id in matched],
        }

    def add(self, documents, metadatas, ids):
        """执行 ``add`` 对应的项目处理逻辑。
        :param documents: 传入 ``documents`` 的业务数据。
        :param metadatas: 传入 ``metadatas`` 的业务数据。
        :param ids: 传入 ``ids`` 的业务数据。
        """
        if self.fail_add:
            self.fail_add = False
            raise RuntimeError("add failed")
        for item_id, doc, meta in zip(ids, documents, metadatas):
            if item_id in self.items:
                raise RuntimeError(f"duplicate id: {item_id}")
            self.items[item_id] = {"document": doc, "metadata": meta}

    def delete(self, ids=None, where=None):
        """执行 ``delete`` 对应的项目处理逻辑。
        :param ids: 传入 ``ids`` 的业务数据。
        :param where: 传入 ``where`` 的业务数据。
        """
        if ids is None and where is not None:
            ids = self.get(where=where)["ids"]
        for item_id in ids or []:
            self.items.pop(item_id, None)

    def query(self, **kwargs):
        """执行 ``query`` 对应的项目处理逻辑。
        :param kwargs: 传递给被包装函数的关键字参数。
        :return: 返回函数处理得到的结果。
        """
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


def _assert(condition, label):
    """记录测试断言结果，并输出对应的通过或失败信息。
    :param condition: 断言是否成立的布尔条件。
    :param label: 用于日志或测试输出的说明标签。
    :return: 返回函数处理得到的结果。
    """
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


def _load_module(tmpdir):
    """在隔离依赖环境中重新加载待测试模块。
    :param tmpdir: 传入 ``tmpdir`` 的业务数据。
    :return: 返回完成读取、构建或转换后的结果。
    """
    old_modules = {}
    for name in [
        "chromadb",
        "chromadb.utils",
        "chromadb.utils.embedding_functions",
        "PyPDF2",
        "docx",
        "jieba",
    ]:
        old_modules[name] = sys.modules.get(name)

    fake_chromadb = types.ModuleType("chromadb")
    fake_chromadb.PersistentClient = lambda path: types.SimpleNamespace(
        get_or_create_collection=lambda name, embedding_function: FakeCollection()
    )
    fake_utils = types.ModuleType("chromadb.utils")
    fake_embedding = types.ModuleType("chromadb.utils.embedding_functions")
    fake_embedding.SentenceTransformerEmbeddingFunction = lambda model_name: object()
    fake_pypdf2 = types.ModuleType("PyPDF2")
    fake_pypdf2.PdfReader = object
    fake_docx = types.ModuleType("docx")
    fake_docx.Document = object
    fake_jieba = types.ModuleType("jieba")
    fake_jieba.lcut = lambda text: [token for token in text.replace("\n", " ").split(" ") if token]

    sys.modules["chromadb"] = fake_chromadb
    sys.modules["chromadb.utils"] = fake_utils
    sys.modules["chromadb.utils.embedding_functions"] = fake_embedding
    sys.modules["PyPDF2"] = fake_pypdf2
    sys.modules["docx"] = fake_docx
    sys.modules["jieba"] = fake_jieba

    module_path = os.path.join(os.getcwd(), "embedding", "rag_pipeline.py")
    spec = importlib.util.spec_from_file_location("rag_pipeline_vector_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    module.DOC_FOLDER_PATH = os.path.join(tmpdir, "docs")
    module.GOODS_JSON_PATH = os.path.join(tmpdir, "goods.json")
    os.makedirs(module.DOC_FOLDER_PATH, exist_ok=True)
    with open(os.path.join(module.DOC_FOLDER_PATH, "policy.md"), "w", encoding="utf-8") as f:
        f.write("七天 退货 政策 支持 换货")
    with open(module.GOODS_JSON_PATH, "w", encoding="utf-8") as f:
        f.write(
            '[{"商品ID":"SP001","名称":"摄像头","分类":"数码","规格":"1080P",'
            '"图文摘要":"家用 监控 摄像头","售价":99},'
            '{"商品ID":"SP002","名称":"耳机","分类":"数码","规格":"蓝牙",'
            '"图文摘要":"无线 音乐 耳机","售价":199}]'
        )
    return module, old_modules


def _restore_modules(old_modules):
    """恢复测试期间替换的 Python 模块。
    :param old_modules: 传入 ``old_modules`` 的业务数据。
    """
    for name, module in old_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


failures = 0

WORKTREE_TEMP_DIR = os.path.join(ROOT_PATH, "datas", "rag_vector_maintenance_runtime")
os.makedirs(WORKTREE_TEMP_DIR, exist_ok=True)

try:
    rag, old = _load_module(WORKTREE_TEMP_DIR)
    try:
        added, skipped = rag.build_vector_db_docs()
        failures += _assert(added > 0 and skipped == 0, "政策文档首次入库返回新增数")
        added_again, skipped_again = rag.build_vector_db_docs()
        failures += _assert(added_again == 0 and skipped_again > 0, "政策文档重复入库跳过已存在 ID")

        goods_added, goods_skipped = rag.build_vector_db_goods()
        failures += _assert(goods_added == 2 and goods_skipped == 0, "商品信息首次入库返回统计")
        goods_added_again, goods_skipped_again = rag.build_vector_db_goods()
        failures += _assert(goods_added_again == 0 and goods_skipped_again == 2, "商品信息重复入库去重")

        update_result = rag.update_single_goods_vector("SP001")
        failures += _assert(update_result["status"] == "success" and update_result["added"] == 1, "单商品向量更新成功")
        failures += _assert(rag.update_single_goods_vector("NOPE")["status"] == "fail", "单商品不存在返回失败")

        keyword_result = rag.fallback_keyword_search("退货 政策", top_k=1)
        failures += _assert(keyword_result[0]["meta"]["doc_type"] == "service_rule", "关键词检索返回 policy 结果")
        rag_result = rag.rag_search("退货 政策", top_k=1)
        failures += _assert(rag_result[0]["meta"]["doc_type"] == "service_rule", "RAG 无结果时自动启用关键词检索")

        rag.collection.items["old_policy"] = {"document": "old", "metadata": {"doc_type": "service_rule"}}
        rag.collection.items["external_keep"] = {"document": "external", "metadata": {"doc_type": "external"}}
        rag.collection.fail_add = True
        rebuild_result = rag.rebuild_all_vectors()
        failures += _assert(rebuild_result["status"] == "fail", "全量重建异常时返回失败")
        failures += _assert("old_policy" in rag.collection.items, "全量重建异常时回滚旧向量")
        failures += _assert("external_keep" in rag.collection.items, "全量重建回滚保留非 RAG 向量")
    finally:
        _restore_modules(old)
finally:
    shutil.rmtree(WORKTREE_TEMP_DIR, ignore_errors=True)

print("=" * 60)
print(f"  通过: {11 - failures}  失败: {failures}  总计: 11")
raise SystemExit(1 if failures else 0)
