# 装配尺寸链可行性校核 API
FROM python:3.11-slim

# 不生成 .pyc、不缓冲 stdout/stderr，便于容器内看日志
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖以利用 Docker 层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷贝应用代码
COPY app ./app

# 默认配置；可在 compose / 运行时覆盖
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    APP_LOG_LEVEL=info

EXPOSE 8000

# 容器内健康检查（uvicorn 提供的 /healthz）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c 'import os,urllib.request; urllib.request.urlopen("http://127.0.0.1:%s/healthz" % os.environ["APP_PORT"], timeout=3).read()'

CMD ["sh", "-c", "uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT} --log-level ${APP_LOG_LEVEL}"]
