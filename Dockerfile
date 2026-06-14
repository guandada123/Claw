# Claw — A股投资辅助系统
# Python 3.12 + 核心依赖（akshare, numpy, pandas, requests）
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Claw"
LABEL org.opencontainers.image.description="A股投资辅助系统 — 模拟交易/实盘/策略生成/知识库"

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 应用目录
WORKDIR /app

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY .workbuddy/ .workbuddy/
COPY Makefile .

# 数据目录（持久化卷）
RUN mkdir -p /app/.workbuddy/data /app/.workbuddy/logs

# 入口：交互式 shell（脚本按需执行）
CMD ["python3", "-c", "print('Claw container ready. Use docker-compose run to execute scripts.')"]
