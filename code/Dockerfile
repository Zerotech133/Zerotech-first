# ---------- 构建阶段：装依赖到独立 venv ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 仅复制安装所需，最大化利用层缓存
COPY pyproject.toml ./
COPY src ./src
# alembic 迁移脚本（migrate 服务运行时需要）
COPY alembic.ini ./
COPY migrations ./migrations

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ---------- 运行阶段：精简 + 非 root ----------
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 只带上构建好的 venv（含已安装的 zhice_os_v2 包）和 alembic 迁移脚本；不拷源码/前端/测试
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/alembic.ini ./
COPY --from=builder /app/migrations ./migrations

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8002/api/v1/health/ready').status==200 else sys.exit(1)"

CMD ["uvicorn", "zhice_os_v2.api.main:app", "--host", "0.0.0.0", "--port", "8002"]
