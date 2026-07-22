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
# 默认指向我们的私有 fork（含 d97751d：4 个分析师并行；以及此前 PM 输出建仓区/止损/仓位字段）。
# 该 commit 目前在 fork 的 feat/parallel-analysts 分支上，clone 时会拉到、可直接 checkout；
# 若把 fork PR 合进 main，可把此 ref 改成 main 的新 commit（更长期稳妥，避免分支删除后被 GC）。
# ⚠️ fork 是私有仓库：构建时用 --build-arg GH_TOKEN=<有 repo 读权限的 token> 传入
#    （或用一个具备访问权限的 SSH agent 挂载改成 SSH clone）；否则 clone 会因鉴权失败报错。
ARG TRADINGAGENTS_REPO=https://github.com/EasonYan7/TradingAgents.git
ARG TRADINGAGENTS_REF=d97751da6b5d9c300f5072d8154c2e798e554f0c
ARG GH_TOKEN=""
RUN CLONE_URL="${TRADINGAGENTS_REPO}"; \
    if [ -n "${GH_TOKEN}" ]; then \
        CLONE_URL=$(echo "${TRADINGAGENTS_REPO}" | sed "s#https://#https://${GH_TOKEN}@#"); \
    fi; \
    git clone "${CLONE_URL}" /Users/didi/TradingAgents \
    && git -C /Users/didi/TradingAgents checkout --quiet "${TRADINGAGENTS_REF}" \
    && git -C /Users/didi/TradingAgents remote set-url origin "${TRADINGAGENTS_REPO}"

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
