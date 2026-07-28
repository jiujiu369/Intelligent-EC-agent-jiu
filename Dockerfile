# 电商客服 Agent — 云端生产部署镜像
# 适用：Oracle Always Free VM / 任意支持 Docker 的平台
FROM python:3.11-slim

# 编译 sentence-transformers / jieba 等需要 gcc/g++
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用层缓存（改业务代码不重装依赖）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 模型缓存与数据持久化（运行时挂载卷，避免重建容器后重新下载模型）
ENV HF_HOME=/app/.cache/hf
VOLUME ["/app/datas", "/app/.cache"]

EXPOSE 7860
ENV PORT=7860
CMD ["python", "app.py"]
