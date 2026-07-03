# Trading24 — 实施 Todo

> 推进主线。文档:[PRD](./PRD.md) · [design](./design.md) · [ARCHITECTURE](./ARCHITECTURE.md)
> 原则:local 优先;后端先(schema→runner)→ 前端 → 通知 → 迁移 →(最后)云。

## Phase 0 · 项目骨架 ✅
- [x] 目录结构(trading24/ db/ notify/ dashboard/ scripts/ config/ data/)
- [x] pyproject.toml(依赖:tradingagents 本地、streamlit、plotly、pyyaml、yfinance、stockstats)
- [x] uv sync 跑通,能 import tradingagents
- [x] .env.example + config 样例(watchlist.yaml / settings.yaml)
- [x] config.py(读 yaml + .env → dataclass)

## Phase 1 · 后端:DB + Runner(核心)
- [x] schema.sql(runs / reports / price_snapshots / alert_configs / notifications)
- [x] db/database.py(连接、建表、CRUD)— 已测
- [x] db/models.py(dataclass)
- [x] prices.py(yfinance 取价 + stockstats 算指标 + 落 price_snapshots,带重试)— 已测(美股+A股)
- [x] runner.py(包装 TradingAgentsGraph,propagate→抽各段落库,抽决策,更新 runs)
- [x] scripts/run_once.py(手动跑一家)
- [x] ✅ 验收:run_once NVDA 跑通,三张表数据完整(run#3 success,10 段报告+120天价格,决策 Underweight)
- [ ] 待补:成本/token 统计(Phase 3 改 stream 时一起做)
- [ ] 待补:实时进度需把 runner 改造为逐节点 stream 落库(Phase 3)
- 注:FRED key 已接入;Reddit 偶发 429 已自动退避;决策词汇为机构评级(增持/中性/减持)已做归一化

## Phase 2 · 后端:Scheduler
- [x] scheduler.py(遍历 watchlist 串行跑 + 失败隔离续跑)
- [x] scripts/run_batch.py
- [x] 成本预算超限标记
- [x] 成本/token 统计(stats.py 回调,单价可配)— 补上 Phase 1 的 TODO
- [x] ✅ 验收:批量跑通(NVDA增持/茅台减持/腾讯中性,3/3,总成本 $0.27,真实显示)

## Phase 3 · 前端:Streamlit 看板
- [x] dashboard/app.py(侧栏 + 总览/历史视图)
- [x] 总览屏(WatchCard + DecisionBadge + StatCard)
- [x] 趋势图(charts.py:价格+MA+决策标记,plotly 交互)
- [ ] 实时进度屏(需 runner 改 stream,Phase 3.5)
- [x] 历史对比屏(逐日列表 + 详情)
- [x] 管理关注列表屏(增/删/上移下移/阈值/运行时间 → 写回 yaml,添加时校验数据源)
- [x] Agentic 主题(暖白+橙+Playfair/Mono):.streamlit/config.toml + CSS 注入
- [x] 修复:溢出、Material 图标字体被误伤、趋势图 Y 轴从 0、SQLite 跨线程
- [x] 信息架构:主页=卡片(迷你图+决策+一句话结论),点击进详情页(大图+完整报告)
- [x] 修复:$ 被当 LaTeX(转义)、报告溢出(换行)、HTML/markdown 语境分开转义
- [x] 修复:AI 助手改为页面顶层固定浮窗,打开时不再挤压主页面布局
- [x] ✅ 验收:浏览器实测——总览卡片、详情页、最终决策段全部正常,无溢出

## Phase 4 · 通知(Telegram)
- [ ] notify/hermes.py(hermes send 封装)
- [ ] notify/alerts.py(价格预警判定)
- [ ] notify/digest.py(每日摘要卡片)
- [ ] ✅ 验收:预警 + 摘要能发到 Telegram

## Phase 5 · 迁移到 Mac mini
- [ ] scripts/install_launchd.sh
- [ ] 打包 + 部署清单
- [ ] mini 上复测连通性(yfinance / hermes)

## Phase 6 · (最后、可选)上云
- [ ] 腾讯云 CVM 运行 + Cloudflare Tunnel 门面
- [ ] 通知切直连 Telegram Bot API
- [ ] 云端数据源连通性复测

## 待用户提供
- [ ] watchlist 公司清单(约 10 家)
- [ ] 每日运行时间
- [ ] 各公司价格预警阈值
- [ ] mini 上 hermes 微信通道是否已配
