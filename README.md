# Trading24

本仓库是 Trading24 本地看板的可复现版本，包含代码、配置、设计资料和当前 SQLite 数据库。

## 在新电脑启动

1. 安装依赖：

```bash
uv sync
```

2. 配置本机密钥：

```bash
cp .env.example .env
```

把 DeepSeek 等 API Key 填入 `.env`。真实 `.env` 不进入 GitHub，避免密钥进入 Git 历史。

3. 启动看板：

```bash
uv run streamlit run trading24/dashboard/app.py
```

默认地址是 `http://localhost:8501`。

## 数据

仓库包含 `data/trading24.sqlite`，新电脑拉取后可以直接看到当前已有分析结果和价格快照。`data/*.sqlite-wal`、`data/*.sqlite-shm`、运行日志和本地虚拟环境不会提交。

## 成本提示

运行 `scripts/run_once.py` 或 `scripts/run_batch.py` 会调用 LLM，可能产生费用；仅查看看板不会重新触发批量分析。
