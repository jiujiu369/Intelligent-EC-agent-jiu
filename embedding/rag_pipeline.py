import logging
# 关闭网络请求、sentence-transformers冗余INFO日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)

import os
import re
import json
import math
from collections import Counter
from typing import List, Dict
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from PyPDF2 import PdfReader
from docx import Document
import config
from tools.error_handler import filter_rag_results, rag_fallback_result
from utils.logger import get_logger

logger = get_logger(__name__)

# ===================== 路径配置（无需修改） =====================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE_DIR = os.path.join(PROJECT_ROOT, config.get("PATHS", "datas_dir"))
CHROMA_PERSIST_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "chroma_persist_dir"))
DOC_FOLDER_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "docs_dir"))
GOODS_JSON_PATH = os.path.join(PROJECT_ROOT, config.get("PATHS", "goods_json")) # 新增商品json路径
COLLECTION_NAME = "customer_service_docs"

def _load_embedding_function():
    try:
        model_name = config.get("RAG", "embedding_model")
        fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
        logger.info(f"加载 embedding 模型 model={model_name}")
        return fn
    except Exception as e:
        logger.warning(f"加载 BGE 模型失败，将使用回退模型 error={e}")

    try:
        fallback_model = config.get("RAG", "fallback_model")
        fn = SentenceTransformerEmbeddingFunction(model_name=fallback_model)
        logger.info(f"加载 fallback embedding 模型 model={fallback_model}")
        return fn
    except Exception as e:
        logger.error(f"加载 fallback embedding 模型失败，RAG 将降级 error={e}")
        return None


embedding_fn = _load_embedding_function()

# 初始化Chroma持久化客户端
if embedding_fn is None:
    chroma_client = None
    collection = None
else:
    try:
        chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
        collection = chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=embedding_fn
        )
    except Exception as e:
        logger.error(f"ChromaDB连接异常 path={CHROMA_PERSIST_PATH} error={e}")
        chroma_client = None
        collection = None

# ===================== 文件读取函数 =====================
def clean_text(raw_text: str) -> str:
    """统一清洗文本：去除Markdown标记、多余换行、空白符号"""
    text = re.sub(r'[#*`>~-]{1,4}', '', raw_text)
    text = re.sub(r'\n{2,}', '\n', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def load_txt_md(file_path: str) -> str:
    """读取txt / md"""
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()
    return clean_text(raw)


def load_pdf(file_path: str) -> str:
    """读取PDF"""
    reader = PdfReader(file_path)
    page_texts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            page_texts.append(text)
    raw = "\n".join(page_texts)
    return clean_text(raw)


def load_docx(file_path: str) -> str:
    """读取docx Word文档（不支持 .doc）"""
    doc = Document(file_path)
    para_texts = []
    for para in doc.paragraphs:
        if para.text.strip():
            para_texts.append(para.text)
    raw = "\n".join(para_texts)
    return clean_text(raw)


def load_document(file_path: str) -> str:
    """自动根据后缀选择解析器"""
    ext = os.path.splitext(file_path)[1].lower()
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
    """遍历文件夹加载全部支持文档【售后政策文档】"""
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

        content = load_document(full_path)
        if len(content) > 10:
            doc_list.append({
                "source": filename,
                "text": content
            })
    return doc_list

# =====================【新增】商品JSON加载函数 =====================
#   在加载 JSON 后预处理，直接标准化所有键名
def normalize_goods_key(goods_dict:Dict) -> Dict:
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

def load_goods_json() -> List[Dict]:
    """货品基础数据json"""
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
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += (chunk_size - chunk_overlap)
    return chunks


def _collection_id_exists(chunk_id: str) -> bool:
    if collection is None:
        return False
    data = collection.get(ids=[chunk_id])
    return bool(data.get("ids"))


def _add_chunks_if_missing(documents: List[str], metadatas: List[Dict], ids: List[str]) -> tuple:
    added_docs = []
    added_metas = []
    added_ids = []
    skipped = 0
    for doc, meta, chunk_id in zip(documents, metadatas, ids):
        if _collection_id_exists(chunk_id):
            skipped += 1
            continue
        added_docs.append(doc)
        added_metas.append(meta)
        added_ids.append(chunk_id)
    if added_ids:
        collection.add(
            documents=added_docs,
            metadatas=added_metas,
            ids=added_ids
        )
    return len(added_ids), skipped


def _goods_to_text(goods: Dict) -> str:
    return (
        f"商品名称：{goods['名称']}\n"
        f"商品ID：{goods['商品ID']}\n"
        f"商品分类：{goods['分类']}\n"
        f"规格参数：{goods['规格']}\n"
        f"产品简介：{goods['图文摘要']}\n"
        f"商品售价:{goods['售价']}\n"
    )


def _delete_by_where(where: Dict) -> int:
    if collection is None:
        return 0
    data = collection.get(where=where)
    ids = data.get("ids", [])
    if ids:
        collection.delete(ids=ids)
    return len(ids)


def _snapshot_collection() -> Dict:
    if collection is None:
        return {"ids": [], "documents": [], "metadatas": []}
    try:
        return collection.get(include=["documents", "metadatas"])
    except TypeError:
        return collection.get()


def _restore_collection_snapshot(snapshot: Dict) -> None:
    if collection is None:
        return
    _delete_by_where({"doc_type": "service_rule"})
    _delete_by_where({"doc_type": "goods_info"})
    ids = snapshot.get("ids", [])
    documents = snapshot.get("documents", [])
    metadatas = snapshot.get("metadatas", [])
    if ids:
        collection.delete(ids=ids)
        collection.add(documents=documents, metadatas=metadatas, ids=ids)

# =====================【原有】政策文档入库 =====================
def build_vector_db_docs():
    if collection is None:
        logger.error("ChromaDB不可用，跳过政策文档入库")
        return (0, 0)
    docs = load_all_docs(DOC_FOLDER_PATH)
    if len(docs) == 0:
        logger.warning("未读取到可用政策文档，终止入库")
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

    added, skipped = _add_chunks_if_missing(all_chunks, all_metadatas, all_ids)
    logger.info(f"政策文档入库完成 added={added} skipped={skipped}")
    return (added, skipped)

# =====================【新增】商品信息入库 =====================
def build_vector_db_goods():
    if collection is None:
        logger.error("ChromaDB不可用，跳过商品信息入库")
        return (0, 0)
    goods_list = load_goods_json()
    goods_list = [normalize_goods_key(item) for item in goods_list]
    
    if not goods_list:
        logger.warning("无商品数据，跳过商品入库")
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

    added, skipped = _add_chunks_if_missing(all_chunks, all_metadatas, all_ids)
    logger.info(f"商品信息入库完成 added={added} skipped={skipped}")
    return (added, skipped)

# =====================【改造】RAG检索入口，支持类型过滤 =====================
def rag_search(query: str, top_k: int = 5, doc_type: str = None) -> List[Dict]:
    """
    :param query: 用户问题
    :param top_k: 返回数量
    :param doc_type: 过滤 "service_rule" / "goods_info"，不传则全部检索
    """
    if collection is None:
        logger.error(f"ChromaDB不可用，RAG降级 query={query} top_k={top_k}")
        return fallback_keyword_search(query, top_k)
    try:
        query_condition = {"query_texts": [query], "n_results": top_k}
        if doc_type is not None:
            query_condition["where"] = {"doc_type": doc_type}

        result = collection.query(**query_condition)
        output = []
        distances = []
        for text, meta, dist in zip(
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0]
        ):
            rounded_dist = round(dist, 5)
            item = {
                "text": text,
                "meta": meta,
                "distance": rounded_dist
            }
            output.append(item)
            distances.append(rounded_dist)
        threshold = config.get("RAG", "distance_threshold")
        filtered = filter_rag_results(output, threshold)
        low_count = len([dist for dist in distances if dist > threshold])
        avg_distance = round(sum(distances) / len(distances), 5) if distances else None
        if low_count:
            logger.warning(
                f"RAG低相似度过滤 query={query} top_k={top_k} low_count={low_count} threshold={threshold}"
            )
        logger.info(
            f"RAG检索 query={query} top_k={top_k} result_count={len(filtered)} avg_distance={avg_distance}"
        )
        if filtered and filtered[0].get("meta", {}).get("fallback"):
            return fallback_keyword_search(query, top_k)
        return filtered
    except Exception as e:
        logger.error(f"RAG检索异常 query={query} top_k={top_k} error={e}")
        return fallback_keyword_search(query, top_k)


def update_single_goods_vector(goods_id: str) -> Dict:
    if collection is None:
        logger.error(f"ChromaDB不可用，单商品向量更新失败 goods_id={goods_id}")
        return {"status": "fail", "msg": "ChromaDB不可用"}
    goods_list = [normalize_goods_key(item) for item in load_goods_json()]
    target_goods = None
    for goods in goods_list:
        if str(goods.get("商品ID", "")).upper() == str(goods_id).upper():
            target_goods = goods
            break
    if target_goods is None:
        logger.warning(f"单商品向量更新失败，商品不存在 goods_id={goods_id}")
        return {"status": "fail", "msg": "商品不存在", "goods_id": goods_id}

    deleted = _delete_by_where({"goods_id": target_goods["商品ID"]})
    chunks = split_text(_goods_to_text(target_goods))
    all_ids = []
    all_metadatas = []
    for bidx, _chunk in enumerate(chunks):
        all_ids.append(f"goods_{target_goods['商品ID']}_{bidx}")
        all_metadatas.append({
            "goods_id": target_goods["商品ID"],
            "doc_type": "goods_info"
        })
    added, skipped = _add_chunks_if_missing(chunks, all_metadatas, all_ids)
    logger.info(
        f"单商品向量更新完成 goods_id={target_goods['商品ID']} deleted={deleted} added={added} skipped={skipped}"
    )
    return {
        "status": "success",
        "goods_id": target_goods["商品ID"],
        "deleted": deleted,
        "added": added,
        "skipped": skipped,
    }


def rebuild_all_vectors() -> Dict:
    if collection is None:
        logger.error("ChromaDB不可用，全量重建失败")
        return {"status": "fail", "msg": "ChromaDB不可用"}
    snapshot = _snapshot_collection()
    docs_count = len(load_all_docs(DOC_FOLDER_PATH))
    goods_count = len(load_goods_json())
    logger.info(f"开始全量重建向量 progress=0/3 docs={docs_count} goods={goods_count}")
    try:
        _delete_by_where({"doc_type": "service_rule"})
        _delete_by_where({"doc_type": "goods_info"})
        logger.info(f"全量重建进度 progress=1/3 deleted_old_vectors docs={docs_count} goods={goods_count}")
        docs_added, docs_skipped = build_vector_db_docs()
        logger.info(f"全量重建进度 progress=2/3 docs_added={docs_added} docs_skipped={docs_skipped}")
        goods_added, goods_skipped = build_vector_db_goods()
        logger.info(f"全量重建完成 progress=3/3 goods_added={goods_added} goods_skipped={goods_skipped}")
        return {
            "status": "success",
            "docs_count": docs_count,
            "goods_count": goods_count,
            "docs_added": docs_added,
            "docs_skipped": docs_skipped,
            "goods_added": goods_added,
            "goods_skipped": goods_skipped,
        }
    except Exception as e:
        logger.error(f"全量重建异常，开始回滚 error={e}")
        _restore_collection_snapshot(snapshot)
        return {"status": "fail", "msg": f"重建失败，已回滚：{e}"}


def fallback_keyword_search(query: str, top_k: int = 5) -> List[Dict]:
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
    try:
        import jieba
        tokens = jieba.lcut(text)
    except Exception:
        tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text)
    return [token.strip().lower() for token in tokens if token and token.strip()]

# =====================【新增工具】清空商品向量（商品大量更新时使用） =====================
def clear_all_goods_vector():
    """删除所有doc_type=goods_info的数据，用于商品更新重建"""
    if collection is None:
        logger.error("ChromaDB不可用，跳过清空商品向量")
        return
    all_data = collection.get(where={"doc_type": "goods_info"})
    if all_data["ids"]:
        collection.delete(ids=all_data["ids"])
        logger.info(f"已清空全部商品向量数据 count={len(all_data['ids'])}")

# ===================== 本地调试 =====================
if __name__ == "__main__":
    # 1. 先导入政策文档
    build_vector_db_docs()
    # 2. 导入商品信息
    build_vector_db_goods()

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
