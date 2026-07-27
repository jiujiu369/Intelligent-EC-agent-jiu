# test_rag_pipeline_fallback.py
# RAG 初始化失败兜底测试：ChromaDB 连接失败时 rag_search 返回降级话术
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import importlib.util
import os
import sys
import types

from tools.error_handler import RAG_FALLBACK_MESSAGE


def _assert(condition, label):
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


class BrokenChroma:
    def __init__(self, path):
        raise RuntimeError("chroma broken")


class FakeEmbeddingFunction:
    def __init__(self, model_name):
        self.model_name = model_name


old_modules = {}
for name in [
    "chromadb",
    "chromadb.utils",
    "chromadb.utils.embedding_functions",
    "PyPDF2",
    "docx",
]:
    old_modules[name] = sys.modules.get(name)

fake_chromadb = types.ModuleType("chromadb")
fake_chromadb.PersistentClient = BrokenChroma
fake_utils = types.ModuleType("chromadb.utils")
fake_embedding = types.ModuleType("chromadb.utils.embedding_functions")
fake_embedding.SentenceTransformerEmbeddingFunction = FakeEmbeddingFunction
fake_pypdf2 = types.ModuleType("PyPDF2")
fake_pypdf2.PdfReader = object
fake_docx = types.ModuleType("docx")
fake_docx.Document = object

sys.modules["chromadb"] = fake_chromadb
sys.modules["chromadb.utils"] = fake_utils
sys.modules["chromadb.utils.embedding_functions"] = fake_embedding
sys.modules["PyPDF2"] = fake_pypdf2
sys.modules["docx"] = fake_docx

try:
    module_path = os.path.join(os.getcwd(), "embedding", "rag_pipeline.py")
    spec = importlib.util.spec_from_file_location("rag_pipeline_fallback_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    failures = 0
    failures += _assert(module.collection is None, "ChromaDB 连接失败后 collection 为 None")
    result = module.rag_search("退货政策")
    failures += _assert(result[0]["text"] == RAG_FALLBACK_MESSAGE, "RAG 连接失败时返回降级话术")
finally:
    for name, module in old_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module

print("=" * 60)
print(f"  通过: {2 - failures}  失败: {failures}  总计: 2")
raise SystemExit(1 if failures else 0)
