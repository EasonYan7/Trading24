# Trading24 — 交接文档（HANDOVER）

> 给接手的 AI / 工程师（codex）。读完应能冷启动继续开发，不重复踩坑。
> 配套文档：[PRD.md](./PRD.md)（要做什么）· [design.md](./design.md)（设计规范）· [ARCHITECTURE.md](./ARCHITECTURE.md)（技术架构）· [todo.md](./todo.md)（任务清单）
> 交接时间：2026-06-30 · 交接人：claude-code

---

## 1. 一句话

Trading24 = 基于开源 **TradingAgents**（多智能体选股框架，本地 `~/TradingAgents`）做的**每日批量选股分析平台**：批量跑关注列表 → 多智能体出 BUY/HOLD/SELL 决策 → 存 SQLite → Streamlit 本地看板看趋势图+结论+完整报告。后续要加 Telegram 通知、迁移到家中 Mac mini。

**当前状态：本地端到端已跑通**。后端（运行器+调度+成本统计）、前端看板（总览/详情/历史/管理）、Agentic 设计主题都已完成并在真实数据上验证。库里已有 6 次真实运行。

---

## 2. 快速跑起来

```bash
cd /Users/didi/Desktop/DiDi/AHT/Trading24
uv sync                                              # 复现环境（已建好 .venv）

# 跑一只股票（约几分钟，调 DeepSeek，~$0.1）
uv run python scripts/run_once.py NVDA 2026-06-26

# 批量跑整个 watchlist
uv run python scripts/run_batch.py 2026-06-26

# 启动看板（浏览器开 http://localhost:8501）
uv run streamlit run trading24/dashboard/app.py
```

环境：Python 3.12+，uv 管理。依赖见 `pyproject.toml`，其中 `tradingagents` 是**本地路径依赖**（`/Users/didi/TradingAgents`，editable）——迁移时改 `[tool.uv.sources]` 里的 path。

---

## 2.5 版本控制 / GitHub

- 远端：**https://github.com/EasonYan7/Trading24**（private），默认分支 `main`，已配 `origin`。
- 鉴权：gh CLI 已登录（EasonYan7），git 用 HTTPS + gh 凭证助手。提交后直接 `git push` 即可。
- `.env`（DeepSeek/FRED 密钥）与 `data/`（数据库）已被 `.gitignore`，**永远不要提交**；只有 `.env.example` 在库里。
- 迁移到新机器时：`git clone` 后需自建 `.env`（照 `.env.example` 填 key）并 `uv sync`。

```bash
git add -A && git commit -m "说明"    # 结尾带 Co-Authored-By 行（见项目约定）
git push
```

---

## 3. 目录与文件职责

```
trading24/
  config.py            读 config/*.yaml + .env → Settings/TickerCfg；save_watchlist 写回；market_of()
  prices.py            yfinance 取价 + stockstats 算 MA/RSI/MACD → 落 price_snapshots（带重试）
  runner.py            ★核心：调 TradingAgentsGraph.propagate() 跑一家，抽各段报告落库，算成本
  scheduler.py         遍历 watchlist 批量跑，失败隔离续跑，成本预算检查
  stats.py             LLM token 统计回调（传给 TradingAgentsGraph 的 callbacks）
  db/
    schema.sql         5 张表：runs / reports / price_snapshots / alert_configs / notifications
    database.py        连接 + CRUD（注意 connect(check_same_thread=)）
    models.py          Run / Report / PriceSnapshot dataclass
  dashboard/
    app.py             ★Streamlit 看板：总览(卡片)/详情/历史/管理 四视图 + Agentic CSS
    charts.py          plotly 趋势图(build_trend_figure)、迷你图(build_sparkline)、决策归一化(_norm_decision)
config/
  watchlist.yaml       关注列表 + 阈值 + 运行时间（看板"管理"页可写）
  settings.yaml        模型/数据源 vendor/成本单价/通知配置
scripts/run_once.py    手动跑一家
scripts/run_batch.py   手动批量
.env                   密钥（不进 git）：DEEPSEEK_API_KEY, FRED_API_KEY
.streamlit/config.toml Streamlit 主题（Agentic：暖白底+橙色）
data/trading24.sqlite  数据库（不进 git）
_typeui/DESIGN.md      Agentic 设计系统原始 token（typeui pull 拉的）
```

---

## 4. 关键配置 / 密钥

`.env`（已配，迁移需重新填）：
- `DEEPSEEK_API_KEY` — 批量分析的 LLM（便宜）
- `FRED_API_KEY` — **必需**，见下方坑 #3

`config/settings.yaml` 要点：
- `deep_think_llm: deepseek-v4-pro` / `quick_think_llm: deepseek-v4-flash`（见坑 #2）
- `data_vendors` 全 `yfinance`（无 key，覆盖美股/A股/港股）
- `price_per_1m_input/output` — 成本估算单价，按实际账单调
- `notify.target: "telegram:shalow"` — Phase 4 用

---

## 5. ⚠️ 必读：踩过的坑 / 关键经验（交接最值钱的部分）

这些都是**只有真跑才暴露**的，文档/设计阶段看不出来：

1. **不改 TradingAgents 源码**。一律 import + 配置 + 回调。`TradingAgentsGraph(selected_analysts=, config=, callbacks=)`；`propagate(ticker, date)` 返回 `(final_state, 决策)`。

2. **DeepSeek 模型名**是 `deepseek-v4-pro` / `deepseek-v4-flash`，不是老的 `deepseek-reasoner/chat`（会 404）。换 key 后先 `GET /v1/models` 确认可用模型名。

3. **FRED 是隐藏的硬依赖**。数据源默认 yfinance 无需 key，但 **News 分析师会调宏观工具 `get_macro_indicators` → 走 FRED → 没配 `FRED_API_KEY` 会抛异常、整次运行崩**。`macro_data` 只有 fred 一个 vendor，绕不开。要么配 FRED key，要么 `selected_analysts` 去掉 `news`（runner 支持 analysts 参数）。

4. **决策词汇是机构评级**（Overweight/Neutral/Underweight），不是 BUY/HOLD/SELL。已在 `charts._norm_decision` 做归一化映射（增持/中性/减持）。新增词汇在那里加。

5. **Streamlit + SQLite 跨线程**：`sqlite3.connect` 默认绑线程，Streamlit rerun 换线程会报错。已用 `connect(check_same_thread=False)` + 每 rerun 新建连接（见 app.py `get_conn`）。

6. **Streamlit markdown 把 `$` 当 LaTeX**：报告里 `$195` 会被当行内公式（吃空格、斜体、溢出）。已用 `md()` 转义 `$`（markdown 语境）；HTML 语境用 `html_safe()`（只转义 <>&，不动 $）。**别混用**。

7. **字体 CSS 别全局覆盖**：早期用 `[class*="st-"]{font-family:..}` 把 Streamlit 的 Material 图标字体也覆盖了，导致展开器箭头显示成文字 "keyboard_arrow_right"。已改为精确选择器 + 显式恢复图标字体（见 app.py CSS 块）。

8. **趋势图 Y 轴别从 0**：`fill="tozeroy"` 会把曲线压扁。已在 `build_trend_figure` 计算 `yrange=[min*0.97, max*1.03]`。

9. **数据源限流**：yfinance 偶发 HTTP 429，prices.py 已带退避重试；Reddit（情绪分析）也会 429，TradingAgents 内部自己退避，不影响出结果。

10. **验证 Streamlit 渲染**：无头 Chrome `--screenshot` 只能截到加载骨架（Streamlit 是 websocket 驱动渲染）。要真截图用 Claude-in-Chrome MCP（navigate→wait 3-4s→screenshot），或人工开浏览器看。

11. **设计系统 = Agentic**（`npx typeui.sh pull agentic`）：暖白 `#F6F6F1` + 白卡 + 橙 `#FF5701`；Playfair Display 衬线标题/正文 + JetBrains Mono 标签/数字。决策色：增持 `#16A34A` / 中性 `#9A958A` / 减持 `#DC2626`。**用户明确要求"高级 ≠ 暗色"，且选择留在 Streamlit（不另起自定义前端）**。

---

## 6. 已完成 vs 待办（详见 todo.md）

**已完成**：Phase 0 骨架 / Phase 1 后端(runner+DB) / Phase 2 调度(批量+失败隔离) / 成本统计 / Phase 3 看板(总览卡片+详情+历史+管理，Agentic 主题)。

**待办（建议顺序）**：
- **Phase 4 通知（Telegram）**：批量跑完发 ① 价格预警（对比 price_snapshots 与 alert_configs 的 upper/lower/pct_move）② 每日摘要卡片（汇总各 run 决策+一句话+价格）。
  - 接口：本机 `hermes send --to telegram --subject "..." --file body.md`（纯 CLI，已验证可用；`hermes send --list` 看目标）。
  - 建在 `trading24/notify/`：`hermes.py`（封装 send）/ `alerts.py`（预警判定）/ `digest.py`（摘要渲染）。挂到 `scheduler.run_batch` 末尾。
- **实时进度屏**：把 runner 从 `propagate()` 改为 `graph.graph.stream()` 逐节点写 reports，看板轮询 reports 表流式展示（PRD 诉求1）。
- **"立即分析"按钮**：看板上手动触发 `scheduler.run_batch`（用户提过的 C 选项）。
- **Phase 5 迁移 Mac mini**：写 `scripts/install_launchd.sh`（uv sync + 生成 launchd plist 每日 run_time 触发 run_batch）；mini 上重配 .env + 重测 yfinance/hermes 连通性。
- **（最后、可选）上云**：腾讯云 CVM 运行 + Cloudflare Tunnel 门面；通知改直连 Telegram Bot API（hermes 不在云上）。
- **二期**：tushare 接 A 股中文新闻/深财报（项目无内置，需写 dataflows 适配 + 改 data_vendors）；微信通道（hermes 当前机器未配，且非官方桥有封号风险）。

---

## 7. 待用户提供的输入

- 真实关注的股票清单（当前是样例 NVDA/茅台/腾讯；用户说暂用这三个，已可在看板"管理"页自助增删改）
- 各股价格预警阈值（看板"管理"页可填）
- Mac mini 上 hermes 微信通道是否已配（当前开发机未配）

---

## 8. 验收 / 自检

- `uv run python scripts/run_once.py NVDA 2026-06-26` 能跑通，`runs/reports/price_snapshots` 有数据，`runs.cost_usd > 0`。
- 单家失败不影响 `run_batch` 其余（failed 记录入库、有 error）。
- 看板四视图正常：总览卡片有决策/结论/迷你图、详情页报告无溢出无乱码($正常)、管理页增删改序写回 yaml。
- 改 TradingAgents 配置或换模型后，先验证数据源 key（FRED 必需）与模型名。

---

## 9. 注意事项 / 约定

- **会花钱**：每次 run 调 DeepSeek（约 $0.08–0.10/只）。开发调试优先复用库里已有 run，别反复触发新跑。
- **可迁移优先**：不写死绝对路径（除 pyproject 的 tradingagents path 与开发期 .env）；密钥进 .env；配置进 config/。
- 后端（DB+runner+scheduler）与前端（Streamlit）**解耦**，数据库是唯一接口。改前端不碰后端，反之亦然。
- 用户偏好：中文沟通；看板要"高级"（Agentic 暖白+橙+衬线，非暗色）；主页只展示趋势图+结论，详情点击进入。
