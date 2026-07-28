# app.py — Hugging Face Spaces 云端部署入口
# 复用 web_ui.py 的全部 UI 与业务逻辑，仅补充云启动时必需的向量库构建。
# 本地仍可照常用 web_ui.py；本文件仅供云端（HF Spaces）使用。

import os

# 1) 导入 web_ui：触发模型加载、UI 组件构建，得到 demo 对象
from web_ui import demo

# 2) 云端 chroma_db 不随仓库上传（.gitignore 已忽略），
#    启动时构建向量库；已存在的分片会自动跳过，不会重复入库。
import embedding.rag_pipeline as rag

print("[app] 正在构建向量库（首次 / 冷启动可能需要 1-2 分钟）...")
try:
    rag.build_vector_db_docs()
    rag.build_vector_db_goods()
    print("[app] 向量库构建完成")
except Exception as e:  # 建库失败不致命，RAG 会走关键词降级
    print(f"[app] 向量库构建失败，将使用降级检索：{e}")

# 3) 启动服务。HF Spaces 会注入 PORT 环境变量（默认 7860）
port = int(os.environ.get("PORT", 7860))
demo.launch(
    server_name="0.0.0.0",
    server_port=port,
    show_error=True,
)
