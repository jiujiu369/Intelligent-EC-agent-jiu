import logging
# 关闭网络请求、sentence-transformers冗余INFO日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)

import os
import re
import json
import math
import importlib
from collections import Counter
from typing import List, Dict
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from PyPDF2 import PdfReader
from docx import Document
import config
from tools.error_handler import rag_fallback_result
from utils.logger import get_logger

logger = get_logger(__name__)

# ===================== 路径配置（无需修改） =====================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE_DIR = os.path.join(PROJECT_ROOT, config.get("PATHS", "datas_dir"))#RAG项目数据
CHROMA_PERSIST_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "chroma_persist_dir"))
FALLBACK_CHROMA_PERSIST_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "chroma_persist_dir_fallback"))
DOC_FOLDER_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "docs_dir"))#项目数据地址
GOODS_JSON_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "goods_json")) # 新增商品json路径
COLLECTION_NAME = "customer_service_docs_512"
FALLBACK_COLLECTION_NAME = "customer_service_docs_fallback_512"
PRIMARY_MODEL_NAME = config.get("RAG", "embedding_model")
FALLBACK_MODEL_NAME = config.get("RAG", "fallback_model")

#=================加载本地embedding模型=====================
def _load_embedding_function(model_name: str, label: str):
    """加载指定的文本嵌入模型，并记录加载状态。
    :param model_name: 嵌入模型名称或模型路径。
    :param label: 用于日志或测试输出的说明标签。
    :return: 返回完成读取、构建或转换后的结果。
    """
    try:
        resolved_model_name = _resolve_model_name(model_name)
        fn = SentenceTransformerEmbeddingFunction(model_name=resolved_model_name)
        logger.info(f"加载 {label} embedding 模型 model={resolved_model_name}")
        return fn
    except Exception as e:
        logger.warning(f"加载 {label} embedding 模型失败 error={e}")
        return None

#================解析模型路径,保证优先运行本地模型=============
def _resolve_model_name(model_name: str) -> str:
    """优先解析并返回项目本地存在的模型路径。
    :param model_name: 嵌入模型名称或模型路径。
    :return: 返回函数处理得到的结果。
    """
    if os.path.isabs(model_name):
        return model_name
    local_path = os.path.join(PROJECT_ROOT, model_name)
    if os.path.exists(local_path):
        return local_path
    return model_name

#=================初始化向量库：加载向量模型->创建持久化客户端->获取/新建向量库集合=======================
def _init_chroma_collection(
    model_name: str,
    persist_path: str,
    collection_name: str,
    label: str,
    embedding_function=None,
):
    """加载嵌入模型并连接或创建持久化向量集合。
    :param model_name: 嵌入模型名称或模型路径。
    :param persist_path: 向量数据库的持久化目录。
    :param collection_name: 向量集合名称。
    :param label: 用于日志或测试输出的说明标签。
    :param embedding_function: 可选的共享 embedding 实例；为空时按模型名加载。
    :return: 返回函数处理得到的结果。
    """
    if embedding_function is None:
        embedding_function = _load_embedding_function(model_name, label)
    if embedding_function is None:
        return None, None, None, False
    try:# 优先使用本地模型，没有则下载
        client = chromadb.PersistentClient(path=persist_path)#持久化向量库客户端
        target_collection = client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_function
        )
        return embedding_function, client, target_collection, False
    except Exception as e:#捕获异常，返回异常日志label
        logger.error(f"{label} ChromaDB连接异常 path={persist_path} error={e}")
        return embedding_function, None, None, True

#=====BGE small 512 维主库==========
embedding_fn, chroma_client, collection, chroma_connection_failed = _init_chroma_collection(
    PRIMARY_MODEL_NAME,
    CHROMA_PERSIST_PATH,
    COLLECTION_NAME,
    "primary-512",
)
#=========相同模型时复用 embedding 实例，备用库失效则兜底 jieba 关键词检索===============
shared_fallback_embedding_fn = (
    embedding_fn
    if _resolve_model_name(PRIMARY_MODEL_NAME) == _resolve_model_name(FALLBACK_MODEL_NAME)
    else None
)
fallback_embedding_fn, fallback_chroma_client, fallback_collection, fallback_chroma_connection_failed = _init_chroma_collection(
    FALLBACK_MODEL_NAME,
    FALLBACK_CHROMA_PERSIST_PATH,
    FALLBACK_COLLECTION_NAME,
    "fallback-512",
    embedding_function=shared_fallback_embedding_fn,
)

# ===================== 文件读取函数 =====================
#文件清洗函数
def clean_text(raw_text: str) -> str:
    """统一清洗文本：去除Markdown标记、多余换行、空白符号。
    :param raw_text: 尚未清洗的原始文本。
    :return: 返回函数处理得到的结果。
    """
    text = re.sub(r'[#*`>~-]{1,4}', '', raw_text) #删除[]中匹配的符号
    text = re.sub(r'\n{2,}', '\n', text)    #删除多余换行
    text = re.sub(r'\s+', ' ', text)    #删除多余空格
    return text.strip()


def load_txt_md(file_path: str) -> str:
    """读取txt / md。
    :param file_path: 目标文件路径。
    :return: 返回完成读取、构建或转换后的结果。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()
    return clean_text(raw)


def load_pdf(file_path: str) -> str:
    """读取PDF。
    :param file_path: 目标文件路径。
    :return: 返回完成读取、构建或转换后的结果。
    """
    reader = PdfReader(file_path)
    page_texts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            page_texts.append(text)
    raw = "\n".join(page_texts)
    return clean_text(raw)


def load_docx(file_path: str) -> str:
    """读取docx Word文档（不支持 .doc）。
    :param file_path: 目标文件路径。
    :return: 返回完成读取、构建或转换后的结果。
    """
    doc = Document(file_path)
    para_texts = []
    for para in doc.paragraphs:
        if para.text.strip():
            para_texts.append(para.text)
    raw = "\n".join(para_texts)
    return clean_text(raw)


def load_document(file_path: str) -> str:
    """自动根据后缀选择解析器。
    :param file_path: 目标文件路径。
    :return: 返回完成读取、构建或转换后的结果。
    """
    ext = os.path.splitext(file_path)[1].lower()#拆分后缀函数
    if ext in [".txt", ".md"]:
        return load_txt_md(file_path)
    elif ext == ".pdf":
        return load_pdf(file_path)
    elif ext == ".docx":
        return load_docx(file_path)
    else:
        logger.warning(f"不支持的文件格式 file={os.path.basename(file_path)}")
        return ""


def load_all_docs(doc_dir: str = DOC_FOLDER_PATH) -> List[Dict[str, str]]:
    """遍历文件夹加载全部支持文档【售后政策文档】。
    :param doc_dir: 待扫描的文档目录路径。
    :return: 返回完成读取、构建或转换后的结果。
    """
    doc_list = []
    if not os.path.exists(doc_dir):
        logger.warning(f"文档目录不存在 path={doc_dir}")
        return doc_list

    support_suffix = {".txt", ".md", ".pdf", ".docx"}
    for filename in os.listdir(doc_dir):
        full_path = os.path.join(doc_dir, filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in support_suffix:
            continue

        content = load_document(full_path)#调用前面函数解析文件后缀
        if len(content) > 10:
            doc_list.append({
                "source": filename,
                "text": content
            })
    return doc_list

# =====================【新增】商品JSON加载函数 =====================
#   在加载 JSON 后预处理，直接标准化所有键名
def normalize_goods_key(goods_dict:Dict) -> Dict:
    #把 JSON 里的英文字段名翻译成中文
    """将商品字段别名统一转换为标准字段名。
    :param goods_dict: 传入 ``goods_dict`` 的业务数据。
    :return: 返回完成读取、构建或转换后的结果。
    """
    mapping = {
        "name":"名称",
        "goods_id":"商品ID",
        "category":"分类",
        "spec":"规格",
        "summary":"图文摘要",
        "price":"售价"
    }
    new_data = {}
    for k,v in goods_dict.items():
        new_k = mapping.get(k, k)
        new_data[new_k] = v
    return new_data
##记录商品基础数据
def load_goods_json() -> List[Dict]:
    """货品基础数据json。
    :return: 返回完成读取、构建或转换后的结果。
    """
    if not os.path.exists(GOODS_JSON_PATH):
        logger.warning(f"商品文件不存在 path={GOODS_JSON_PATH}")
        return []
    with open(GOODS_JSON_PATH,"r",encoding="utf-8") as f:
        goods_data = json.load(f)
    return goods_data

# ===================== 文本滑动窗口切分 =====================
def split_text(
    text: str,
    chunk_size: int = config.get("RAG", "chunk_size"),
    chunk_overlap: int = config.get("RAG", "chunk_overlap"),
) -> List[str]:
    """按照长度和重叠窗口将长文本拆分为检索片段。
    :param text: 需要处理、检索或格式化的文本。
    :param chunk_size: 传入 ``chunk_size`` 的业务数据。
    :param chunk_overlap: 传入 ``chunk_overlap`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    chunks = []
    start = 0#记录字符，相当于路标=size-lop
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += (chunk_size - chunk_overlap)
    return chunks

#入库前查重，避免重复插入相同片段，防止向量库重复数据
def _collection_id_exists(chunk_id: str, target_collection=None) -> bool:
    """检查指定编号是否已经存在于向量集合中。
    :param chunk_id: 传入 ``chunk_id`` 的业务数据。
    :param target_collection: 传入 ``target_collection`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    if target_collection is None:
        target_collection = collection
    if target_collection is None:
        return False
    data = target_collection.get(ids=[chunk_id])
    return bool(data.get("ids"))

#批量准备要入库的文本片段，先逐个检查 ID 是否已经存在向量库，跳过已存在数据，只新增不存在的 chunk，避免重复入库；最终返回新增数量、跳过数量
def _add_chunks_if_missing(documents: List[str], metadatas: List[Dict], ids: List[str], target_collection=None) -> tuple:
    """执行 ``_add_chunks_if_missing`` 对应的项目处理逻辑。
    :param documents: 传入 ``documents`` 的业务数据。
    :param metadatas: 传入 ``metadatas`` 的业务数据。
    :param ids: 传入 ``ids`` 的业务数据。
    :param target_collection: 传入 ``target_collection`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    if target_collection is None:
        target_collection = collection
    added_docs = []
    added_metas = []
    added_ids = []
    skipped = 0
    for doc, meta, chunk_id in zip(documents, metadatas, ids):
        if _collection_id_exists(chunk_id, target_collection):
            skipped += 1
            continue
        added_docs.append(doc)
        added_metas.append(meta)
        added_ids.append(chunk_id)
    if added_ids:
        target_collection.add(
            documents=added_docs,
            metadatas=added_metas,
            ids=added_ids
        )
    return len(added_ids), skipped

#json文件也入库，整合文本模板，结构化字典 → 拼接成文本 → 切片
def _goods_to_text(goods: Dict) -> str:
    """执行 ``_goods_to_text`` 对应的项目处理逻辑。
    :param goods: 商品数据记录。
    :return: 返回函数处理得到的结果。
    """
    return (
        f"商品名称：{goods['名称']}\n"
        f"商品ID：{goods['商品ID']}\n"
        f"商品分类：{goods['分类']}\n"
        f"规格参数：{goods['规格']}\n"
        f"产品简介：{goods['图文摘要']}\n"
        f"商品售价:{goods['售价']}\n"
    )

#根据筛选条件（where）批量删除向量数据，返回删除条数
def _delete_by_where(where: Dict, target_collection=None) -> int:
    """按照元数据条件删除向量集合中的记录。
    :param where: 传入 ``where`` 的业务数据。
    :param target_collection: 需要删除记录的目标集合，省略时使用主集合。
    :return: 返回删除的记录数量。
    """
    if target_collection is None:
        target_collection = collection
    if target_collection is None:
        return 0
    data = target_collection.get(where=where)
    ids = data.get("ids", [])
    if ids:
        target_collection.delete(ids=ids)
    return len(ids)

#修改向量库前先备份，出错可以恢复。
# 通过将要修改的向量文本暂时存储在内存中，如果需要回退则重新向量化
def _snapshot_collection(target_collection=None) -> Dict:
    """导出向量集合当前记录，供失败时恢复。
    :param target_collection: 需要快照的目标集合，省略时使用主集合。
    :return: 返回集合快照。
    """
    if target_collection is None:
        target_collection = collection
    if target_collection is None:
        return {"ids": [], "documents": [], "metadatas": []}
    try:
        return target_collection.get(include=["documents", "metadatas"])
    except TypeError:
        return target_collection.get()


def _delete_all_collection_records(target_collection) -> int:
    """删除目标集合的全部记录，包括旧版本缺少 doc_type 的记录。
    :param target_collection: 需要清空的目标向量集合。
    :return: 返回删除的向量记录数量。
    """
    data = target_collection.get()
    ids = data.get("ids", [])
    if ids:
        target_collection.delete(ids=ids)
    return len(ids)

#将暂存的数据重新向量化
def _restore_collection_snapshot(snapshot: Dict, target_collection=None) -> None:
    """执行 ``_restore_collection_snapshot`` 对应的项目处理逻辑。
    :param snapshot: 传入 ``snapshot`` 的业务数据。
    :param target_collection: 需要恢复的目标集合，省略时使用主集合。
    """
    if target_collection is None:
        target_collection = collection
    if target_collection is None:
        return
    _delete_all_collection_records(target_collection)
    ids = snapshot.get("ids", [])
    documents = snapshot.get("documents", [])
    metadatas = snapshot.get("metadatas", [])
    if ids:
        target_collection.delete(ids=ids)
        target_collection.add(documents=documents, metadatas=metadatas, ids=ids)

# =====================【原有】政策文档入库 =====================
def _build_vector_db_docs_for(target_collection, label: str):
    """执行 ``_build_vector_db_docs_for`` 对应的项目处理逻辑。
    :param target_collection: 传入 ``target_collection`` 的业务数据。
    :param label: 用于日志或测试输出的说明标签。
    :return: 返回完成读取、构建或转换后的结果。
    """
    if target_collection is None:
        logger.error(f"{label} ChromaDB不可用，跳过政策文档入库")
        return (0, 0)
    docs = load_all_docs(DOC_FOLDER_PATH)
    if len(docs) == 0:
        logger.warning(f"{label} 未读取到可用政策文档，终止入库")
        return (0, 0)

    all_chunks = []
    all_metadatas = []
    all_ids = []

    for idx, doc in enumerate(docs):
        blocks = split_text(doc["text"])
        for bidx, chunk in enumerate(blocks):
            chunk_id = f"rule_{doc['source']}_{idx}_{bidx}"
            all_chunks.append(chunk)
            all_metadatas.append({
                "source_file": doc["source"],
                "doc_type": "service_rule"   # 标记：售后政策
            })
            all_ids.append(chunk_id)

    added, skipped = _add_chunks_if_missing(all_chunks, all_metadatas, all_ids, target_collection)
    logger.info(f"{label} 政策文档入库完成 added={added} skipped={skipped}")
    return (added, skipped)


def build_vector_db_docs():
    """执行 ``build_vector_db_docs`` 对应的项目处理逻辑。
    :return: 返回完成读取、构建或转换后的结果。
    """
    return _build_vector_db_docs_for(collection, "primary-512")


def build_vector_db_docs_fallback():
    """将政策文档写入备用 512 维向量集合。
    :return: 返回完成读取、构建或转换后的结果。
    """
    return _build_vector_db_docs_for(fallback_collection, "fallback-512")

# =====================【新增】商品信息入库 =====================
def _build_vector_db_goods_for(target_collection, label: str):
    """执行 ``_build_vector_db_goods_for`` 对应的项目处理逻辑。
    :param target_collection: 传入 ``target_collection`` 的业务数据。
    :param label: 用于日志或测试输出的说明标签。
    :return: 返回完成读取、构建或转换后的结果。
    """
    if target_collection is None:
        logger.error(f"{label} ChromaDB不可用，跳过商品信息入库")
        return (0, 0)
    goods_list = load_goods_json()
    goods_list = [normalize_goods_key(item) for item in goods_list]
    
    if not goods_list:
        logger.warning(f"{label} 无商品数据，跳过商品入库")
        return (0, 0)

    all_chunks = []
    all_metadatas = []
    all_ids = []

    for goods in goods_list:
        # 拼接用于语义检索的文本，库存等动态数据
        goods_text = _goods_to_text(goods)
        chunks = split_text(goods_text)
        for bidx, chunk in enumerate(chunks):
            chunk_id = f"goods_{goods['商品ID']}_{bidx}"
            all_chunks.append(chunk)
            all_metadatas.append({
                "goods_id": goods["商品ID"],
                "doc_type": "goods_info"   # 标记：商品信息
            })
            all_ids.append(chunk_id)

    added, skipped = _add_chunks_if_missing(all_chunks, all_metadatas, all_ids, target_collection)
    logger.info(f"{label} 商品信息入库完成 added={added} skipped={skipped}")
    return (added, skipped)


def build_vector_db_goods():
    """执行 ``build_vector_db_goods`` 对应的项目处理逻辑。
    :return: 返回完成读取、构建或转换后的结果。
    """
    return _build_vector_db_goods_for(collection, "primary-512")


def build_vector_db_goods_fallback():
    """将商品信息写入备用 512 维向量集合。
    :return: 返回完成读取、构建或转换后的结果。
    """
    return _build_vector_db_goods_for(fallback_collection, "fallback-512")


def _is_meaningless_rag_query(query: str) -> bool:
    """判断查询是否缺少可用于知识库检索的有效语义。
    :param query: 传入 ``query`` 的业务数据。
    :return: 条件成立时返回 ``True``，否则返回 ``False``。
    """
    text = "" if query is None else str(query).strip()
    if not text:
        return True
    if re.search(r"[\u4e00-\u9fff]", text):
        return False
    normalized = re.sub(r"\s+", "", text)
    if re.fullmatch(r"SP\d+", normalized, flags=re.IGNORECASE):
        return False
    if len(normalized) <= 20 and re.fullmatch(r"[A-Za-z0-9]+", normalized):
        return True
    return False


# =====================【改造】RAG检索入口，支持类型过滤 =====================
def rag_search(query: str, top_k: int = 5, doc_type: str = None) -> List[Dict]:
    """依次使用主向量库、备用向量库和关键词策略检索知识。
    :param query: 传入 ``query`` 的业务数据。
    :param top_k: 传入 ``top_k`` 的业务数据。
    :param doc_type: 传入 ``doc_type`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    if _is_meaningless_rag_query(query):
        logger.warning(f"RAG输入被过滤 query={query}")
        return []

    if collection is None:
        logger.error(f"ChromaDB不可用，RAG降级 query={query} top_k={top_k}")
        if fallback_collection is None:
            return rag_fallback_result()
        return fallback_vector_search(query, top_k, doc_type)
    try:
        filtered = _search_collection(collection, query, top_k, doc_type, "primary-512")
        if not filtered:
            return fallback_vector_search(query, top_k, doc_type)
        return filtered
    except Exception as e:
        logger.error(f"RAG检索异常 query={query} top_k={top_k} error={e}")
        return fallback_vector_search(query, top_k, doc_type)


def fallback_vector_search(query: str, top_k: int = 5, doc_type: str = None) -> List[Dict]:
    """使用备用向量集合执行相似度检索。
    :param query: 传入 ``query`` 的业务数据。
    :param top_k: 传入 ``top_k`` 的业务数据。
    :param doc_type: 传入 ``doc_type`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    if fallback_collection is None:
        return rag_fallback_result()
    try:
        filtered = _search_collection(fallback_collection, query, top_k, doc_type, "fallback-512")
        if filtered:
            return filtered
    except Exception as e:
        logger.error(f"fallback-512 RAG检索异常 query={query} top_k={top_k} error={e}")
    return fallback_keyword_search(query, top_k)


def _search_collection(target_collection, query: str, top_k: int, doc_type: str, label: str) -> List[Dict]:
    """在指定向量集合中检索并整理相似文档。
    :param target_collection: 传入 ``target_collection`` 的业务数据。
    :param query: 传入 ``query`` 的业务数据。
    :param top_k: 传入 ``top_k`` 的业务数据。
    :param doc_type: 传入 ``doc_type`` 的业务数据。
    :param label: 用于日志或测试输出的说明标签。
    :return: 返回函数处理得到的结果。
    """
    query_condition = {"query_texts": [query], "n_results": top_k}
    if doc_type is not None:
        query_condition["where"] = {"doc_type": doc_type}

    result = target_collection.query(**query_condition)
    output = []
    distances = []
    for text, meta, dist in zip(
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0]
    ):
        rounded_dist = round(dist, 5)
        output.append({
            "text": text,
            "meta": meta,
            "distance": rounded_dist
        })
        distances.append(rounded_dist)

    threshold = config.get("RAG", "distance_threshold")
    filtered = [
        item for item in output
        if item.get("distance") is not None and item["distance"] <= threshold
    ]
    low_count = len([dist for dist in distances if dist > threshold])
    avg_distance = round(sum(distances) / len(distances), 5) if distances else None
    if low_count:
        logger.warning(
            f"{label} RAG低相似度过滤 query={query} top_k={top_k} low_count={low_count} threshold={threshold}"
        )
    logger.info(
        f"{label} RAG检索 query={query} top_k={top_k} result_count={len(filtered)} avg_distance={avg_distance}"
    )
    return filtered


def _update_goods_in_collection(target_collection, label: str, target_goods: Dict) -> Dict:
    """在一个明确的向量集合中更新指定商品。"""
    if target_collection is None:
        return {"status": "fail", "msg": f"{label} ChromaDB不可用"}
    try:
        deleted = _delete_by_where(
            {"goods_id": target_goods["商品ID"]}, target_collection
        )
        chunks = split_text(_goods_to_text(target_goods))
        all_ids = []
        all_metadatas = []
        for bidx, _chunk in enumerate(chunks):
            all_ids.append(f"goods_{target_goods['商品ID']}_{bidx}")
            all_metadatas.append({
                "goods_id": target_goods["商品ID"],
                "doc_type": "goods_info",
            })
        added, skipped = _add_chunks_if_missing(
            chunks, all_metadatas, all_ids, target_collection
        )
        logger.info(
            f"{label} 单商品向量更新完成 goods_id={target_goods['商品ID']} "
            f"deleted={deleted} added={added} skipped={skipped}"
        )
        return {
            "status": "success",
            "deleted": deleted,
            "added": added,
            "skipped": skipped,
        }
    except Exception as e:
        logger.error(
            f"{label} 单商品向量更新失败 goods_id={target_goods['商品ID']} error={e}"
        )
        return {"status": "fail", "msg": str(e)}


def update_single_goods_vector(goods_id: str) -> Dict:
    """同步更新指定商品在主库和备用库中的向量记录。
    :param goods_id: 商品的唯一编号。
    :return: 返回函数处理得到的结果。
    """
    goods_list = [normalize_goods_key(item) for item in load_goods_json()]
    target_goods = None
    for goods in goods_list:
        if str(goods.get("商品ID", "")).upper() == str(goods_id).upper():
            target_goods = goods
            break
    if target_goods is None:
        logger.warning(f"单商品向量更新失败，商品不存在 goods_id={goods_id}")
        return {"status": "fail", "msg": "商品不存在", "goods_id": goods_id}

    primary_result = _update_goods_in_collection(
        collection, "primary-512", target_goods
    )
    fallback_result = _update_goods_in_collection(
        fallback_collection, "fallback-512", target_goods
    )
    successes = sum(
        result["status"] == "success"
        for result in (primary_result, fallback_result)
    )
    status = "success" if successes == 2 else "partial" if successes else "fail"
    return {
        "status": status,
        "goods_id": target_goods["商品ID"],
        "deleted": primary_result.get("deleted", 0),
        "added": primary_result.get("added", 0),
        "skipped": primary_result.get("skipped", 0),
        "primary": primary_result,
        "fallback": fallback_result,
    }


def _rebuild_collection(target_collection, label: str) -> Dict:
    """独立重建一个目标集合，并在该集合失败时回滚。"""
    if target_collection is None:
        return {"status": "fail", "msg": f"{label} ChromaDB不可用"}
    try:
        snapshot = _snapshot_collection(target_collection)
    except Exception as e:
        logger.error(f"{label} 全量重建快照失败 error={e}")
        return {"status": "fail", "msg": f"快照失败：{e}"}
    try:
        _delete_all_collection_records(target_collection)
        docs_added, docs_skipped = _build_vector_db_docs_for(
            target_collection, label
        )
        goods_added, goods_skipped = _build_vector_db_goods_for(
            target_collection, label
        )
        return {
            "status": "success",
            "docs_added": docs_added,
            "docs_skipped": docs_skipped,
            "goods_added": goods_added,
            "goods_skipped": goods_skipped,
        }
    except Exception as e:
        logger.error(f"{label} 全量重建异常，开始回滚 error={e}")
        try:
            _restore_collection_snapshot(snapshot, target_collection)
            return {"status": "fail", "msg": f"重建失败，已回滚：{e}"}
        except Exception as rollback_error:
            logger.error(f"{label} 全量重建回滚失败 error={rollback_error}")
            return {
                "status": "fail",
                "msg": f"重建失败：{e}；回滚失败：{rollback_error}",
            }


def rebuild_all_vectors() -> Dict:
    """重新构建主库和备用库的全部文档及商品向量。
    :return: 返回函数处理得到的结果。
    """
    docs_count = len(load_all_docs(DOC_FOLDER_PATH))
    goods_count = len(load_goods_json())
    logger.info(f"开始全量重建向量 docs={docs_count} goods={goods_count}")
    primary_result = _rebuild_collection(collection, "primary-512")
    fallback_result = _rebuild_collection(fallback_collection, "fallback-512")
    successes = sum(
        result["status"] == "success"
        for result in (primary_result, fallback_result)
    )
    status = "success" if successes == 2 else "partial" if successes else "fail"
    return {
        "status": status,
        "docs_count": docs_count,
        "goods_count": goods_count,
        "docs_added": primary_result.get("docs_added", 0),
        "docs_skipped": primary_result.get("docs_skipped", 0),
        "goods_added": primary_result.get("goods_added", 0),
        "goods_skipped": primary_result.get("goods_skipped", 0),
        "primary": primary_result,
        "fallback": fallback_result,
    }


def fallback_keyword_search(query: str, top_k: int = 5) -> List[Dict]:
    """在向量检索不可用时使用关键词相关度检索。
    :param query: 传入 ``query`` 的业务数据。
    :param top_k: 传入 ``top_k`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    candidates = _load_keyword_candidates()
    if not candidates:
        return rag_fallback_result()

    query_tokens = _tokenize(query)
    if not query_tokens:
        return rag_fallback_result()

    tokenized_candidates = []
    document_frequency = Counter()
    for candidate in candidates:
        tokens = _tokenize(candidate["text"])
        token_set = set(tokens)
        document_frequency.update(token_set)
        tokenized_candidates.append((candidate, tokens))

    total_docs = len(tokenized_candidates)
    scored = []
    for candidate, tokens in tokenized_candidates:
        token_counts = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            if token not in token_counts:
                continue
            tf = token_counts[token] / max(1, len(tokens))
            idf = math.log((total_docs + 1) / (document_frequency[token] + 1)) + 1
            score += tf * idf
        if score > 0:
            scored.append((score, candidate))

    if not scored:
        return rag_fallback_result()

    scored.sort(key=lambda item: item[0], reverse=True)
    output = []
    for score, candidate in scored[:top_k]:
        output.append({
            "text": candidate["text"],
            "meta": candidate["meta"],
            "distance": round(1 / (score + 1), 5),
        })
    logger.info(f"关键词降级检索 query={query} top_k={top_k} result_count={len(output)}")
    return output


def _load_keyword_candidates() -> List[Dict]:
    """整理商品与文档数据，生成关键词检索候选项。
    :return: 返回完成读取、构建或转换后的结果。
    """
    candidates = []
    for doc in load_all_docs(DOC_FOLDER_PATH):
        for idx, chunk in enumerate(split_text(doc["text"])):
            candidates.append({
                "text": chunk,
                "meta": {
                    "source_file": doc["source"],
                    "doc_type": "service_rule",
                    "chunk_index": idx,
                },
            })

    for goods in [normalize_goods_key(item) for item in load_goods_json()]:
        goods_text = _goods_to_text(goods)
        for idx, chunk in enumerate(split_text(goods_text)):
            candidates.append({
                "text": chunk,
                "meta": {
                    "goods_id": goods["商品ID"],
                    "doc_type": "goods_info",
                    "chunk_index": idx,
                },
            })
    return candidates


def _tokenize(text: str) -> List[str]:
    """对中英文混合文本执行关键词检索分词。
    :param text: 需要处理、检索或格式化的文本。
    :return: 返回函数处理得到的结果。
    """
    try:
        jieba = importlib.import_module("jieba")
        tokens = jieba.lcut(text)
    except Exception:
        tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text)
    normalized_tokens = []
    for token in tokens:
        token = token.strip().lower()
        if not token:
            continue
        normalized_tokens.append(token)
        if re.search(r"[\u4e00-\u9fff]", token) and len(token) > 1:
            for size in (2, 3):
                if len(token) >= size:
                    normalized_tokens.extend(
                        token[idx:idx + size]
                        for idx in range(0, len(token) - size + 1)
                    )
    return normalized_tokens

# =====================【新增工具】清空商品向量（商品大量更新时使用） =====================
def _clear_goods_in_collection(target_collection, label: str) -> Dict:
    """清空一个明确目标集合中的商品向量。"""
    if target_collection is None:
        return {"status": "fail", "msg": f"{label} ChromaDB不可用", "deleted": 0}
    try:
        deleted = _delete_by_where({"doc_type": "goods_info"}, target_collection)
        logger.info(f"{label} 已清空全部商品向量数据 count={deleted}")
        return {"status": "success", "deleted": deleted}
    except Exception as e:
        logger.error(f"{label} 清空商品向量失败 error={e}")
        return {"status": "fail", "msg": str(e), "deleted": 0}


def clear_all_goods_vector():
    """删除主、备用集合中所有 ``doc_type=goods_info`` 的数据。"""
    primary_result = _clear_goods_in_collection(collection, "primary-512")
    fallback_result = _clear_goods_in_collection(fallback_collection, "fallback-512")
    successes = sum(
        result["status"] == "success"
        for result in (primary_result, fallback_result)
    )
    status = "success" if successes == 2 else "partial" if successes else "fail"
    return {
        "status": status,
        "primary": primary_result,
        "fallback": fallback_result,
    }

# ===================== 本地调试 =====================
if __name__ == "__main__":
    # 1. 导入主、备用政策文档
    build_vector_db_docs()
    build_vector_db_docs_fallback()
    # 2. 导入主、备用商品信息
    build_vector_db_goods()
    build_vector_db_goods_fallback()

    # 测试1：只检索商品信息
    print("\n====【测试：商品检索】====")
    goods_res = rag_search(query="家用监控摄像头", top_k=2, doc_type="goods_info")
    for item in goods_res:
        print(f"goods_id:{item['meta'].get('goods_id')}")
        print(f"内容:{item['text']}\n")

    # 测试2：只检索售后政策
    print("\n====【测试：政策检索】====")
    rule_res = rag_search(query="退货政策", top_k=2, doc_type="service_rule")
    for item in rule_res:
        print(f"文件:{item['meta'].get('source_file')}")
        print(f"内容:{item['text']}\n")
