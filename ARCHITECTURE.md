# Trading24 — 技术设计文档（design.md）

> PRD 的工程落地版。读者：开发者（含未来在 Mac mini 上部署的自己）。
> 配套文档：[PRD.md](./PRD.md)

| 版本 | v0.1 |
|------|------|
| 日期 | 2026-06-30 |
| 依赖框架 | TradingAgents（`~/TradingAgents`，作为库被 import） |
| 运行时 | Python 3.12+ / uv 管理 |

---

## 1. 设计原则

1. **不改 TradingAgents 源码**：把它当库 import，只调 `TradingAgentsGraph`。升级框架不影响我们。
2. **数据库为中心**：Runner/Scheduler 写库，看板读库，三者解耦，互不依赖运行态。
3. **可迁移优先**：无绝对路径、密钥进 `.env`、配置进文件、环境靠 uv lockfile 复现。
4. **逐步落库**：分析每完成一个 agent 节点就写库，使"实时进度"和"断点可查"成为可能。
5. **失败隔离**：单公司失败不影响批次；状态入库。

---

## 2. 目录结构

```
Trading24/
├── PRD.md
├── design.md
├── pyproject.toml            # 依赖（含 tradingagents 本地路径依赖）
├── .env                      # 密钥（不进 git）：LLM key 等
├── .env.example
├── config/
│   ├── watchlist.yaml        # 关注公司 + 阈值 + 每日运行时间
│   └── settings.yaml         # 模型、数据源 vendor、成本预算等
├── trading24/
│   ├── __init__.py
│   ├── config.py             # 读取 config/ 与 .env，解析为 dataclass
│   ├── db/
│   │   ├── schema.sql        # 建表 DDL
│   │   ├── database.py       # 连接、迁移、CRUD
│   │   └── models.py         # Run / Report / PriceSnapshot / AlertConfig dataclass
│   ├── runner.py             # 核心：跑一家公司，逐节点落库
│   ├── prices.py             # 取价格/指标快照（yfinance 封装）
│   ├── scheduler.py          # 遍历 watchlist 批量跑 + 容错
│   ├── notify/
│   │   ├── hermes.py         # hermes send 封装
│   │   ├── alerts.py         # 价格预警判定
│   │   └── digest.py         # 每日摘要卡片渲染
│   └── dashboard/
│       ├── app.py            # Streamlit 入口
│       ├── views/            # 实时进度 / 历史 / 趋势图 页面
│       └── charts.py         # ECharts/Plotly 趋势图构建
├── scripts/
│   ├── run_once.py           # 手动跑一家（一期验收用）
│   ├── run_batch.py          # 手动触发整批
│   └── install_launchd.sh    # 部署：装定时任务
└── data/
    └── trading24.sqlite      # 数据库文件（不进 git）
```

---

## 3. 数据模型（SQLite DDL）

```sql
-- 一次运行
CREATE TABLE runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    market        TEXT,                 -- us / cn / hk
    trade_date    TEXT NOT NULL,        -- yyyy-mm-dd
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,        -- running / success / failed
    decision      TEXT,                 -- BUY / HOLD / SELL
    confidence    TEXT,                 -- 可选：模型给的信心
    model         TEXT,
    tokens        INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    error         TEXT
);
CREATE INDEX idx_runs_ticker_date ON runs(ticker, trade_date);

-- 各 agent 段报告（逐节点写入）
CREATE TABLE reports (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL REFERENCES runs(id),
    section   TEXT NOT NULL,            -- market / news / fundamentals / sentiment
                                        -- / bull / bear / research_mgr / trader
                                        -- / risk / final
    content   TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_reports_run ON reports(run_id);

-- 每日价格 + 技术指标快照（喂趋势图）
CREATE TABLE price_snapshots (
    ticker   TEXT NOT NULL,
    date     TEXT NOT NULL,            -- yyyy-mm-dd
    open     REAL, high REAL, low REAL, close REAL, volume INTEGER,
    ma5 REAL, ma20 REAL, ma60 REAL,
    rsi REAL, macd REAL, macd_signal REAL,
    PRIMARY KEY (ticker, date)
);

-- 价格预警阈值
CREATE TABLE alert_configs (
    ticker     TEXT PRIMARY KEY,
    upper      REAL,                    -- 价格上限
    lower      REAL,                    -- 价格下限
    pct_move   REAL,                    -- 单日涨跌幅阈值(%)
    enabled    INTEGER DEFAULT 1
);

-- 通知发送记录（去重、审计）
CREATE TABLE notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER REFERENCES runs(id),
    kind      TEXT,                     -- price_alert / daily_digest / health
    channel   TEXT,                     -- telegram / weixin
    sent_at   TEXT,
    ok        INTEGER
);
```

---

## 4. 核心模块设计

### 4.1 Runner（`runner.py`）— 关键
封装对 `TradingAgentsGraph` 的调用，**逐节点落库**。

```
run_one(ticker, trade_date, config) -> run_id
  1. 解析市场（后缀 .SS/.SZ/.HK → cn/hk，否则 us）
  2. INSERT runs(status='running', started_at=now)
  3. ta = TradingAgentsGraph(config=config)
     init_state = ta.propagator.create_initial_state(...)
     for chunk in ta.graph.stream(init_state, ...):
         - 从 chunk 解析出本次完成的节点与其报告文本
         - INSERT reports(run_id, section, content)   # 逐步写
         - 累加 token/cost（用 stats_handler 回调）
  4. 抽取最终决策（BUY/HOLD/SELL）→ UPDATE runs
  5. 调 prices.snapshot(ticker, trade_date) 落 price_snapshots
  6. UPDATE runs(status='success', finished_at, tokens, cost)
  异常: UPDATE runs(status='failed', error=...) 并向上抛/记录
```

- **节点→section 映射**：在一处维护字典，把 graph 节点名映射到 `reports.section`。
- **成本统计**：复用 CLI 里用的 `stats_handler` 回调拿 token 数；成本按模型单价换算。
- **流式给看板**：看板读 `reports` 表轮询 `run_id` 下的新段即可呈现"实时进度"（DB 即消息总线，无需额外推送通道）。

### 4.2 Prices（`prices.py`）
- `history(ticker, period)` → OHLC（yfinance）。
- `snapshot(ticker, date)` → 当日 OHLC + 用 stockstats 算 MA/RSI/MACD，写 `price_snapshots`。
- 加节流与重试（应对 yfinance 429）：失败退避重试 N 次，仍失败则记 null 不阻塞。

### 4.3 Scheduler（`scheduler.py`）
```
run_batch():
  watchlist = load(config/watchlist.yaml)
  for ticker in watchlist:
      try: run_one(ticker, today(), settings)
      except: log + 记 failed + continue          # 失败隔离
  # 批次结束后触发通知
  alerts.check_and_send(today())
  digest.send(today())
```
- 串行执行（控成本/限流）。
- 由 launchd 每日定点触发 `scripts/run_batch.py`。

### 4.4 通知（`notify/`）
- `hermes.py`：`send(text, to, subject)` → 调 `hermes send --to {to} --subject {subject}`（stdin 传正文）。返回是否成功，记 `notifications`。
- `alerts.py`：读 `alert_configs`，对当日 `price_snapshots` 判断越界/涨跌幅，命中则 `hermes.send`。
- `digest.py`：聚合当日所有 `runs` 的决策+一句话理由+价格，渲染成一条 Markdown 卡片发出。
- 一期渠道：`telegram`（已验证）。微信留二期。

### 4.5 看板（`dashboard/`，Streamlit）
- **app.py**：侧栏 = watchlist + 手动触发表单；主区 = 三视图。
- **实时进度视图**：选中一个 `running` 的 run，轮询 `reports` 表，按节点顺序展开内容（流式观感）。
- **历史视图**：按公司/日期查 `runs`+`reports`，支持相邻两日并排对比。
- **趋势图视图**（`charts.py`）：
  - 数据来自 `price_snapshots`（价格+指标）+ `runs`（决策点）。
  - 用 ECharts/Plotly：价格 K 线/折线 + MA 线 + 在对应日期打 BUY(▲绿)/SELL(▼红)/HOLD(•灰) markPoint。
  - 交互：悬停 tooltip、缩放(dataZoom)、时间范围切换。
  - 自动刷新：`st_autorefresh` 周期重读库。

---

## 5. 配置文件

`config/watchlist.yaml`
```yaml
run_time: "08:30"          # 每日运行时间（本地时区）
tickers:
  - symbol: NVDA
    upper: 1400
    lower: 1100
  - symbol: 600519.SS       # 茅台
    upper: 1300
    lower: 1100
```

`config/settings.yaml`
```yaml
llm_provider: deepseek
deep_think_llm: deepseek-reasoner
quick_think_llm: deepseek-chat
data_vendors:
  fundamental_data: yfinance
  news_data: yfinance
max_debate_rounds: 1
cost_budget_usd_per_day: 5     # 超预算告警
notify:
  channel: telegram
  target: "telegram:shalow"
```

`.env`（密钥）：配置 DeepSeek 等 API Key；真实值不进 git。

---

## 6. 依赖（在 TradingAgents 之外新增）

- `streamlit` — 看板
- `streamlit-echarts` 或 `plotly` — 趋势图
- `pyyaml` — 配置
- `tradingagents` — 本地路径依赖（`~/TradingAgents`）
- 价格/指标：yfinance、stockstats（TradingAgents 已带）

---

## 7. 关键流程时序

**每日批量**
```
launchd(08:30) → run_batch.py
  → 逐公司 run_one() → 逐节点写 reports → 写 price_snapshots → 更新 runs
  → alerts.check_and_send() → digest.send()  → hermes → Telegram
```

**看板看实时进度**
```
浏览器 → Streamlit app → 轮询 reports(run_id) → 增量渲染各 agent 段
```

**看板看趋势图**
```
Streamlit → 读 price_snapshots + runs → charts.build() → ECharts 交互渲染
```

---

## 8. 部署 / 迁移

> **优先级:local 优先。** 固定顺序:① 本机笔记本跑通 → ② 打包迁移到家中 Mac mini → ③(最后、可选)上云。云端本期不投入。上云时(腾讯云 CVM 运行 + Cloudflare Tunnel 做安全门面)需重测数据源连通性,并把通知从本地 hermes 切换为直连 Telegram Bot API。

### 8.1 迁移到 Mac mini

```
scripts/install_launchd.sh:
  1. uv sync                       # 复现环境
  2. 校验 .env 与 config/ 存在
  3. 生成并 load ai.trading24.batch.plist（每日 run_time 触发 run_batch.py）
  4. 提示：在 mini 上确认 hermes 已装且 `hermes send --list` 含目标
```

迁移清单：拷贝项目目录（排除 `data/` 可选）、`.env`、`config/`；mini 上 `uv sync` + 跑安装脚本 + 复测连通性（yfinance / hermes）。

---

## 9. 验收标准（一期）

- [ ] `run_once.py NVDA` 跑通，`runs`/`reports`/`price_snapshots` 数据完整。
- [ ] 单公司失败不影响 `run_batch` 其余公司。
- [ ] 看板能看实时进度、历史、带决策标记的交互趋势图。
- [ ] 价格越界与每日摘要能发到 Telegram。
- [ ] 在 Mac mini 上 `uv sync` + 安装脚本后可定时自动运行。
