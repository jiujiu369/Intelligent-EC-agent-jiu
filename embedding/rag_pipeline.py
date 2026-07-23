import logging
# 关闭网络请求、sentence-transformers冗余INFO日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)

import os
import re
from typing import List, Dict
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from PyPDF2 import PdfReader
from docx import Document

# ===================== 路径配置（无需修改） =====================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../datas"))
CHROMA_PERSIST_PATH = os.path.join(BASE_DIR, "chroma_db")
DOC_FOLDER_PATH = os.path.join(BASE_DIR, "docs")
COLLECTION_NAME = "customer_service_docs"

# 加载本地BGE向量化模型
embedding_fn = SentenceTransformerEmbeddingFunction(model_name="BAAI/bge-base-zh-v1.5")

# 初始化Chroma持久化客户端
chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
collection = chroma_client.get_or_create_collection(
    name=COLLECTION_NAME,
    embedding_function=embedding_fn
)

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
        print(f"⚠️ 不支持的文件格式，跳过：{os.path.basename(file_path)}")
        return ""


def load_all_docs(doc_dir: str = DOC_FOLDER_PATH) -> List[Dict[str, str]]:
    """遍历文件夹加载全部支持文档"""
    doc_list = []
    if not os.path.exists(doc_dir):
        print(f"⚠️ 目录不存在：{doc_dir}，请放入文档！")
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

# ===================== 文本滑动窗口切分 =====================
def split_text(text: str, chunk_size: int = 384, chunk_overlap: int = 64) -> List[str]:
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

# ===================== 向量入库 =====================
def build_vector_db():
    docs = load_all_docs()
    if len(docs) == 0:
        print("未读取到可用文档，终止入库")
        return

    all_chunks = []
    all_metadatas = []
    all_ids = []

    for idx, doc in enumerate(docs):
        blocks = split_text(doc["text"])
        for bidx, chunk in enumerate(blocks):
            chunk_id = f"{doc['source']}_{idx}_{bidx}"
            all_chunks.append(chunk)
            all_metadatas.append({"source_file": doc["source"]})
            all_ids.append(chunk_id)

    collection.add(
        documents=all_chunks,
        metadatas=all_metadatas,
        ids=all_ids
    )
    print(f"✅ 向量入库完成！片段总数：{len(all_chunks)}")

# ===================== RAG检索入口（工具调用函数） =====================
def rag_search(query: str, top_k: int = 2) -> List[Dict]:
    result = collection.query(
        query_texts=[query],
        n_results=top_k
    )
    output = []
    for text, meta, dist in zip(
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0]
    ):
        output.append({
            "text": text,
            "source": meta["source_file"],
            "distance": round(dist, 2)
        })
    return output

# ===================== 本地调试 =====================
if __name__ == "__main__":
    build_vector_db()
    res = rag_search("退货政策")
    print("\n====检索结果====")
    for item in res:
        print(f"【来源】{item['source']}\n【内容】{item['text']}\n")
