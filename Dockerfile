# syntax=docker/dockerfile:1

# Trading24 服务镜像：内置 tradingagents 依赖。dashboard 与 scheduler 共用此镜像。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# 系统依赖：git 拉 tradingagents；curl 装 supercronic；tzdata 让容器时区生效。
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/*

# supercronic：容器友好的 cron，scheduler 服务用它按点触发每日批量分析。
ARG SUPERCRONIC_VERSION=v0.2.33
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL -o /usr/local/bin/supercronic \
       "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${ARCH}" \
    && chmod +x /usr/local/bin/supercronic

# 关键：把 tradingagents 克隆到与本地开发完全相同的绝对路径。
# 这样 pyproject.toml 与 uv.lock 里的 path 依赖无需任何改动，uv sync --frozen 完全可复现。
# ⚠️ 本地 TradingAgents 含自有提交 df045a3（PM 输出建仓区/止损/仓位字段），上游没有。
#    构建镜像前必须把 REPO 换成你自己的 fork（并推上去），REF 换成 fork 上的 commit；
#    直接用上游 TauricResearch 会丢失该改动。
ARG TRADINGAGENTS_REPO=https://github.com/TauricResearch/TradingAgents.git
ARG TRADINGAGENTS_REF=c15200dc286b66abce3f1bcf09b298dc06b8539d
RUN git clone "${TRADINGAGENTS_REPO}" /Users/didi/TradingAgents \
    && git -C /Users/didi/TradingAgents checkout --quiet "${TRADINGAGENTS_REF}"

WORKDIR /app

# 先只复制锁文件装依赖（吃满 Docker 层缓存，源码改动不必重装依赖）。
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 再复制源码并安装本工程。
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# 直接用 venv 里的可执行文件运行，运行期不再触网/重解析依赖。
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # tradingagents 的产物/缓存/记忆重定向到挂载卷 → 跨重启保留、随备份一起覆盖。
    TRADINGAGENTS_RESULTS_DIR=/app/data/ta/logs \
    TRADINGAGENTS_CACHE_DIR=/app/data/ta/cache \
    TRADINGAGENTS_MEMORY_LOG_PATH=/app/data/ta/memory/trading_memory.md

EXPOSE 8501

# 默认起看板；scheduler 服务在 docker-compose.yml 里覆盖 command。
# 关 CORS/XSRF 是因为对外通过 Cloudflare Tunnel + Access 登录墙做鉴权，
# 而 Streamlit 的 websocket 经反代时这两项默认开启会握手失败。
CMD ["streamlit", "run", "trading24/dashboard/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false", \
     "--server.enableCORS=false", "--server.enableXsrfProtection=false"]
