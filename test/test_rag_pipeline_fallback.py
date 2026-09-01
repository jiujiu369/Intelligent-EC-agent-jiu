# RAG 主、备用 512 维库测试：两个集合共用一个小模型实例
import importlib.util
import os
import sys
import types

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)


def _assert(condition, label):
    """记录测试断言结果，并输出对应的通过或失败信息。"""
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


class FakeCollection:
    """记录隔离 Chroma 集合的入库数据。"""

    def __init__(self):
        self.items = {}

    def get(self, ids=None, **_kwargs):
        """按 ID 返回已存在的数据。"""
        return {"ids": [item_id for item_id in ids or [] if item_id in self.items]}

    def add(self, documents, metadatas, ids):
        """保存入库数据，供测试验证写入目标。"""
        for item_id, document, metadata in zip(ids, documents, metadatas):
            self.items[item_id] = {"document": document, "metadata": metadata}


class FakeEmbeddingFunction:
    """替代真实嵌入模型，避免加载本地模型。"""

    def __init__(self, model_name):
        self.model_name = model_name
        embedding_instances.append(self)


primary_path = os.path.abspath("isolated-primary-512")
fallback_path = os.path.abspath("isolated-fallback-512")
init_calls = []
init_labels = []
embedding_instances = []
collection_embedding_functions = []
collections = {}


def _get_config(section, key, default=None):
    """提供隔离导入所需的精确 RAG 配置。"""
    values = {
        ("PATHS", "datas_dir"): "datas",
        ("PATHS", "chroma_persist_dir"): primary_path,
        ("PATHS", "chroma_persist_dir_fallback"): fallback_path,
        ("PATHS", "chroma_persist_dir_384"): "legacy-fallback-384",
        ("PATHS", "docs_dir"): "datas/docs",
        ("PATHS", "goods_json"): "datas/goods.json",
        ("PATHS", "log_dir"): "logs",
        ("RAG", "embedding_model"): "BAAI/bge-small-zh-v1.5",
        ("RAG", "fallback_model"): "BAAI/bge-small-zh-v1.5",
        ("RAG", "chunk_size"): 384,
        ("RAG", "chunk_overlap"): 64,
        ("RAG", "distance_threshold"): 1.5,
    }
    return values.get((section, key), default)


class FakeClient:
    """记录集合创建参数并返回对应的内存集合。"""

    def __init__(self, path):
        self.path = path

    def get_or_create_collection(self, name, embedding_function):
        init_calls.append((embedding_function.model_name, self.path, name))
        collection_embedding_functions.append(embedding_function)
        return collections.setdefault(name, FakeCollection())


class FakeLogger:
    """记录初始化日志中的库标签。"""

    def info(self, message):
        if message.startswith("加载 "):
            init_labels.append(message.split(" ", 2)[1])

    def warning(self, _message):
        pass

    def error(self, _message):
        pass


old_modules = {}
for name in [
    "chromadb",
    "chromadb.utils",
    "chromadb.utils.embedding_functions",
    "PyPDF2",
    "docx",
    "config",
    "tools.error_handler",
    "utils.logger",
]:
    old_modules[name] = sys.modules.get(name)

fake_chromadb = types.ModuleType("chromadb")
fake_chromadb.PersistentClient = FakeClient
fake_utils = types.ModuleType("chromadb.utils")
fake_embedding = types.ModuleType("chromadb.utils.embedding_functions")
fake_embedding.SentenceTransformerEmbeddingFunction = FakeEmbeddingFunction
fake_pypdf2 = types.ModuleType("PyPDF2")
fake_pypdf2.PdfReader = object
fake_docx = types.ModuleType("docx")
fake_docx.Document = object
fake_config = types.ModuleType("config")
fake_config.get = _get_config
fake_error_handler = types.ModuleType("tools.error_handler")
fake_error_handler.rag_fallback_result = lambda: []
fake_logger_module = types.ModuleType("utils.logger")
fake_logger_module.get_logger = lambda _name: FakeLogger()

sys.modules["chromadb"] = fake_chromadb
sys.modules["chromadb.utils"] = fake_utils
sys.modules["chromadb.utils.embedding_functions"] = fake_embedding
sys.modules["PyPDF2"] = fake_pypdf2
sys.modules["docx"] = fake_docx
sys.modules["config"] = fake_config
sys.modules["tools.error_handler"] = fake_error_handler
sys.modules["utils.logger"] = fake_logger_module

try:
    module_path = os.path.join(os.getcwd(), "embedding", "rag_pipeline.py")
    spec = importlib.util.spec_from_file_location("rag_pipeline_fallback_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    module.load_all_docs = lambda _path: [{"source": "policy.md", "text": "七天退货政策"}]
    module.load_goods_json = lambda: [{
        "商品ID": "SP001", "名称": "摄像头", "分类": "数码", "规格": "1080P",
        "图文摘要": "家用监控摄像头", "售价": 99,
    }]

    failures = 0
    resolved_bge_path = "BAAI/bge-small-zh-v1.5"
    failures += _assert(init_calls == [
        (resolved_bge_path, primary_path, "customer_service_docs_512"),
        (resolved_bge_path, fallback_path, "customer_service_docs_fallback_512"),
    ], "主、备用 RAG 使用独立的 512 维集合初始化")
    failures += _assert(
        len(embedding_instances) == 1
        and collection_embedding_functions[0] is collection_embedding_functions[1],
        "两个 512 维集合只加载一次并共用同一个 embedding 实例",
    )

    has_fallback_builders = all(hasattr(module, name) for name in [
        "build_vector_db_docs_fallback", "build_vector_db_goods_fallback",
    ])
    failures += _assert(has_fallback_builders, "提供备用 512 维入库入口")
    if has_fallback_builders:
        failures += _assert(module.build_vector_db_docs_fallback() == (1, 0), "备用政策文档入库返回统计")
        failures += _assert(module.build_vector_db_goods_fallback() == (1, 0), "备用商品入库返回统计")
        failures += _assert(
            not module.collection.items and len(module.fallback_collection.items) == 2,
            "备用入库只写入 fallback_collection",
        )
finally:
    for name, saved_module in old_modules.items():
        if saved_module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved_module

print("=" * 60)
print(f"  通过: {6 - failures} 失败: {failures} 总计: 6")
raise SystemExit(1 if failures else 0)
