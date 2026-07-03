# Trading24 — 设计规范 / 实现现状（design.md）

> 本文件是 **as-built 现状清单**：逐页记录当前 Streamlit 看板每个元素的功能、颜色、尺寸、位置。
> 数据来源：`trading24/dashboard/app.py`（+ `dashboard/charts.py`）源码逐行盘点 + 浏览器 DOM 实测（localhost:8501，视口 1572px）。
> 配套：[PRD.md](./PRD.md)（要做什么）· [ARCHITECTURE.md](./ARCHITECTURE.md)（技术怎么实现）
>
> **最近一次重写：2026-07-02**，由多 agent 分页盘点 + DOM 实测校验。旧版本里的「暖白橙色 + Playfair 衬线 + ECharts」方向**已废弃**，当前实现为 **emerald 交易信号色 + Inter/Geist Mono + Plotly + 浅/深双主题 + 顶部行情条 + bento grid + Agent Pipeline 侧栏 + 可折叠报告卡**。

| 项 | 值 |
|------|------|
| 载体 | Streamlit 本地看板（localhost），桌面浏览器为主 |
| 布局 | 顶部行情条 + 顶部主导航；主体居中 `.block-container` 宽 **`min(1600px, 94vw)`**（流式，封顶 1600）。留白经 `--t24-gutter: (100vw-shell)/2` 统一推导，固定定位的 Pipeline 左栏 / AI 面板留白均引用它，避免魔法数漂移 |
| 风格基调 | 安静、克制、信息密度高的金融工具感；扁平、细边框、极浅阴影；决策状态用颜色编码 |
| 字体 | UI/标题 **Inter**；数字/代码/标签/等宽标注 **Geist Mono** |
| 主题 | 浅色（默认）/ 深色，右上角图标切换；所有色用 `--t24-*` CSS 变量 |

---

## 1. 设计原则

1. **信息优先于装饰**：用户为「看决策、看走势、看变化」而来。
2. **状态即颜色**：增持 / 中性 / 减持三态用固定语义色，全局一致。
3. **扁平克制**：细边框（0.5–1px）+ 留白做分隔；阴影极浅（`--t24-shadow`）。
4. **同一套语言贯穿**：看板、趋势图、AI 面板、通知共用同一套色彩与措辞。
5. **明暗自适应**：每个 token 提供浅/深两套值。
6. **等宽用于数字**：所有价格、代码、时间、计数用 Geist Mono。

---

## 2. 设计 Token

### 2.1 颜色变量（`--t24-*`，浅色 / 深色）

| Token | 浅色 | 深色 | 用途 |
|------|------|------|------|
| `bg` | `#f9fafb` | `#0a0a0a` | 页面底 |
| `surface` | `#ffffff` | `#111112` | 卡片 / 面板 / 输入框底 |
| `surface_low` | `#f6f3f4` | `#0f1111` | 行情条底、报告正文底、预览块底 |
| `surface_high` | `#f3f4f6` | `#1f2020` | 进度轨、hover 底、代码块、市场徽标底 |
| `text` | `#1c1b1c` | `#f3f0f1` | 正文 / 标题 |
| `muted` | `#6b7280` | `#a3a3a3` | 辅助说明、次要文字 |
| `subtle` | `#9ca3af` | `#737373` | 占位、弱标注、待运行徽章文字 |
| `border` | `#e5e7eb` | `#262626` | 默认分隔线 / 边框 |
| `border_strong` | `#bbcabf` | `#3f3f46` | 强边框（Hero 卡、AI 面板、选择框）|
| `primary` | `#10b981` | `#4edea3` | **增持 / BUY**、主按钮、价格线、logo、进度填充 |
| `primary_text` | `#ffffff` | `#002113` | 主按钮文字 |
| `danger` | `#ef4444` | `#ffb4ab` | **减持 / SELL**、删除、失败 |
| `warning` | `#f59e0b` | `#f5b93f` | 预警（预留）|
| `tertiary` | `#6366f1` | `#9699ff` | 行情条文字（装饰）|
| `terminal` | `#0a0a0a` | `#050505` | 终端底（预留，当前报告未用）|
| `terminal_text` | `#d1d5db` | `#d1d5db` | 终端文字（预留）|
| `ai_user` | `#ecfdf5` | `#102114` | AI 用户气泡底 |
| `ai_assistant` | `#f9fafb` | `#181818` | AI 回复气泡底 |
| `shadow` | `0 1px 3px rgba(15,23,42,.08)` | `0 18px 60px rgba(0,0,0,.36)` | 卡片阴影 |

> **中性/中间态**大量用 CSS `color-mix()`：如徽章底=语义色 14% 透明；决策条边框=语义色 34% 混 border；已完成 chip=primary 8–9% 底。

### 2.2 决策三态（语义色，全局唯一写法）

| 语义 | 中文标签 | 主色（token） | 徽章底 | 用途 |
|------|------|------|------|------|
| **BUY** | 增持 | `primary` `#10b981`/`#4edea3` | primary 14% 透明 | 买入决策、买点 ▲ |
| **HOLD** | 中性 | `muted` `#6b7280`/`#a3a3a3` | muted 14% 透明 | 观望、持有点 ● |
| **SELL** | 减持 | `danger` `#ef4444`/`#ffb4ab` | danger 14% 透明 | 卖出决策、卖点 ▼ |
| 未知/空 | — | `muted` | muted 12% 透明 | 无决策 |

### 2.3 字号 / 圆角 / 尺寸

- **圆角**：`--t24-radius-control` = `--t24-radius-card` = **12px**（控件、报告卡、live 卡、选择卡）。**bento 卡 / Hero 卡 / pipeline 卡硬编码 8px**（与 token 不同，见 §9）。药丸/徽章 `999px`。图标按钮 `9px`。
- **控件高度**：`--t24-control-height` = 44px（导航按钮、搜索框）；行内图标按钮 **34×34px**；AI 头部图标 30×30px；操作按钮 38px。
- **常用字号**：页标题 30px/800·750；卡标题 18px/750；Hero 现价 34px/800；名称 22px/800；正文 14px；辅助 13px；标签/代码 11–12px；`.caps` 小标 11px 大写。
- **纵向节奏**：卡片内边距 16–22px；块间距 10–28px。

---

## 3. 全局 Chrome（每页共享）

渲染顺序：`render_market_tape()` → `render_top_nav()` → 页面主体 → `render_ai_plugin()`。

### 3.1 顶部行情条 `.t24-tape`
单行滚动前 4 只标的「代码: 价格 (涨跌)」+ 末尾「Scheduler: Next run <time>」；空列表显示 `No watchlist symbols`。

| 属性 | 值 |
|---|---|
| 文字 | tertiary `#6366f1`/`#9699ff`，Geist Mono 12px，line-height 32px |
| 背景 / 边框 | surface_low `#f6f3f4`/`#0f1111`；下边框 1px border |
| 布局 | 全宽普通流，`margin 0 -16px`（出血），padding `0 16px`；`nowrap` + 溢出省略号 |

### 3.2 顶部主导航 `.st-key-t24_topnav`
`st.columns([1.3,.5,.5,.5,1.95,1.3,.5,.5,.5])`，行 gap 8px，垂直居中；背景 surface + 下边框 1px border，`margin 0 -16px 28px`，padding `12px 16px`。
**DOM 顺序**：logo → 总览 → 分析 → 关注 → 弹性留白 → 搜索 → 添加 → 主题 → 设置。**CSS `order` 视觉重排右区**为：设置(1) → 主题(2) → 搜索(3) → 添加(4)。

| 元素 | 功能 | 颜色 | 尺寸/位置 |
|---|---|---|---|
| `.t24-logo` TRADING24 | 品牌字标 | primary `#10b981`/`#4edea3` | Inter 25px/800（≤900px→22px），高 44px，最左 |
| 导航按钮 总览/分析/关注 | 路由切换（当前页 `primary`）| 未选 muted；选中 primary + 底部 2px primary 下划线 | 高 44px，padding `0 10px`；文字 14px/600 |
| 搜索框 | 模糊搜代码，placeholder「搜索代码…」，可加新标的 | 底 surface，边框 border | 高 44px，radius 12px，13px；`order:3` |
| 添加按钮（条件）| 搜索有输入且未存在时出现，校验并添加 | 边框 1px primary，底 primary 9%，文字 primary | 高 44px，radius 12px，13px/750；`order:4` |
| 主题切换（图标）| 深/浅切换，`help` tooltip | 图标 text（currentColor mask）| 44×44px，radius 12px；月亮 mask 23px / 太阳 20px；`margin-left:-6px`；`order:2` |
| 设置齿轮（图标）| 打开设置弹窗 | 图标 text | 44×44px，radius 12px；齿轮 mask 22px；`order:1`（视觉最右起点）|

### 3.3 AI 助手面板 `render_ai_plugin()`
右下角悬浮气泡 launcher（始终在）+ 面板（仅 `ai_panel_open` 时）。面板打开时给 `.block-container` 加右侧留白 `max(1rem, calc(392px-(100vw-1260px)/2))`（≥761px）避免遮挡。

| 元素 | 功能 | 颜色 | 尺寸/位置 |
|---|---|---|---|
| launcher `toggle_ai_panel` | 开/关面板（气泡图标）| 透明底，图标 primary | fixed right/bottom 20px；52×52px；气泡 mask 30px；z 1002 |
| 头部 `t24_ai_header` | 固定头条（✕关闭·标题·选记录·新对话）| 底 surface，边框 border_strong，阴影 | fixed right20 bottom512 width356；radius `14/14/0/0`；z 1003 |
| `.t24-ai-title`「AI 助手」| 标题 | text | 15px/700 |
| 面板 `t24_ai_panel` | 滚动主体 | 底 surface，边框 border_strong，阴影 | fixed right20 bottom20 width356 height492；radius `0/0/14/14`；z 1002 |
| `.t24-ai-scope` | 上下文范围说明 | muted | 11px |
| `.t24-ai-msg.user` | 用户气泡 | 底 ai_user，边框 border_strong | radius 10px，padding 8/11，13px |
| `.t24-ai-msg.assistant` | AI 回复（富文本）| 底 ai_assistant，边框 border | radius 10px，13px |
| `.t24-ai-meta` | 发言者标（你/AI）| muted | Geist Mono 9px |
| 空态建议 `ai_sugg_0..2` | 3 条示例提问 | text/muted | 12.5px，左对齐 |
| 表单 `ai_form` | textarea「问我关于这些分析的任何问题…」+ 发送(primary) | 默认控件 | textarea 高 80px，13px |

### 3.4 Agent Pipeline 侧栏（分析页专属，非全局）
见 §5.1。`render_pipeline_scrollspy()` 仅注入 scrollspy JS（`components.html(height=0)`，绑定点击展开 + 滚动高亮）。

---

## 4. 页面：总览（Overview）— `overview()`

布局（2026-07-02 重设计，紧凑网格版）：`t24-page-head` 页头 → **顶部摘要条** `.t24-ov-summary`（3 个 tile，全宽一行）→ **公司卡片网格** `.t24-ov-grid`（响应式 `repeat(auto-fit, minmax(290px,1fr))`，在 1526px 宽下约 4 列，6 家两行即一屏）。旧版「左=一列高卡片流 / 右=bento」已废弃——太长、右列半空。
**口径**：今日运行 `done`=今天且 success 的标的数；决策分布只统计「今天」（三态全显，含 0）；近期提醒仅近 24h、最多 5 条（摘要条只显条数+最新一条）。
> 注：总览页**无 KPI metric 行**（`kpi_row()` 为死代码，`kpi_card()` 仅 history 页用）；运行中实时状态卡在**分析页**，非此页。

### 4.1 页头 `.t24-page-head`
`flex; align-items:flex-end; justify-content:space-between; margin:4px 0 22px`。

| 元素 | 功能 | 颜色 | 尺寸 |
|---|---|---|---|
| `.t24-headline`「关注列表总览」| 主标题 | text | 30px/750，line 1.12 |
| `.t24-subhead` | 副标题 | muted | 15px |
| `.caps`「Live workspace」| 装饰小标 | muted | Geist Mono 11px 大写 |

### 4.2 顶部摘要条 `.t24-ov-summary`
`grid; repeat(auto-fit, minmax(200px,1fr)); gap:12px`。3 个 `.t24-ov-tile`（底 surface_high，radius 12px，padding 14/16）：
- **今日运行**：标签「今日运行 · {状态词}」+ 大数 `done`（Geist Mono 24px）+「/ N 已完成」+ `.t24-progress` 进度条。
- **今日决策分布**：三态圆点 `● 增持 {buys} · 中性 {holds} · 减持 {sells}`（增持 primary/中性 muted/减持 danger，Geist Mono 13px）。
- **近期提醒**：bell 图标 +「{n} 条 · 最新 <ticker 决策>」，最新一条是链接跳 `?p=analysis&t=`；无则「近 24 小时暂无提醒」。

### 4.3 公司卡片网格 `.t24-ov-grid`
`for sym in reversed(symbols)`（**FILO，最新在前**）。每张卡是一个 `<a class='t24-ov-card' href='?p=analysis&t=<sym>'>`（整卡点击进详情，与近期提醒同一路由方式），固定紧凑高度（约 195px）。卡内 `text-decoration:none !important`（避免 Streamlit 给链接文字加下划线）。`accent`=决策色，用于 sparkline 描边。

| 元素 | 功能 | 颜色 | 尺寸/位置 |
|---|---|---|---|
| `.t24-ov-card` 容器 | 整卡链接 | 底 surface，边框 1px border，阴影；hover 边框 border_strong + 底 surface_high | radius 12px，padding 14/16 |
| `.t24-ov-name` | 标的名 | text | 16px/700，省略号截断，左 |
| `badge(decision)` | 决策徽章（增持/中性/减持/—）| 决策色 | Geist Mono 11px，radius 999px，右上 |
| `.t24-ov-code` | 代码 · 市场 | muted | Geist Mono 12px，左 |
| `.t24-ov-price` | 现价 | text | Geist Mono 15px，右 |
| `.t24-ov-delta` | 日涨跌 | `delta_color()` 涨绿跌红 | Geist Mono 12px，价右侧 |
| `.t24-ov-spark` | 迷你走势（内联 SVG，`_sparkline_svg` 近 120 日收盘）| **线中性灰 subtle**；决策 ▲▼● 标记按决策色（增持 primary▲/减持 danger▼/中性 muted●）| 全宽 × 30px |
| `.t24-ov-concl` | 结论摘要（success 真实结论 / failed「最近运行失败」danger / 其他「暂无分析」）| muted / danger | 13px，**2 行截断**（`-webkit-line-clamp:2`），min-height 39px |
| `.t24-ov-foot-age` | run_age（英文相对时间）| muted | Geist Mono 11px，左下 |
| `.t24-ov-foot-cta`「查看分析 →」| 进入提示 | primary | 12px，右下 |

---

## 5. 页面：分析 / 详情 — `detail_view(sym)`

布局：`st.columns([.24,.76], gap="large")`：左 24%=Agent Pipeline 导航，右 76%=主内容。≥640px 时左栏变 `position:fixed`（top 164px，left `max(22px, calc((100vw-1260px)/2+22px))`，宽 clamp 138–185px，z 20）。
右栏顺序：标的选择器 → 实时状态卡（运行/失败时）→ Ticker Hero → "Price Action · Decisions" → Plotly 趋势图 → 操作按钮行 → 决策历史。
> **无 ECharts / 无终端字体报告块 / 无 KPI 行**。图=Plotly；报告=可折叠 `t24-report-module` 卡。

### 5.1 左栏 Agent Pipeline `.t24-pipeline`
标题「Agent Pipeline」（cpu 18px）+ 10 个 `t24-pipeline-item` 锚点（顺序 `REPORT_NAV`：最终决策/技术面/基本面/情绪面/消息面/多方观点/空方观点/研究经理/交易员/风险复核）。默认「最终决策」active。

| 元素 | 颜色 | 尺寸 |
|---|---|---|
| `.t24-pipeline` 卡 | 底 surface，边框 border | radius 8px；sticky/fixed |
| `.t24-pipeline-item` 常态 | muted，左边框 3px 透明 | 13px，padding 10/14，min-h 40px |
| item hover | text，底 surface_high | — |
| item active | primary，底 surface_high，左边框 3px primary | — |

### 5.2 标的选择器 `t24_analysis_filter`
`[1.1,.9]`：`.caps`「分析标的」+ selectbox（切换即跳转）。selectbox 高 42px，边框 border_strong，13px。

### 5.3 实时状态卡 `.t24-live-run`（pending/running/failed/success）
运行中 `@st.fragment(run_every=3)` 每 3 秒局部刷新，完成后整页 rerun。

| 元素 | 颜色 | 尺寸 |
|---|---|---|
| `.t24-live-run` 卡 | 边框 primary 36% 混 border；底 primary 7% 混 surface | radius 12px，padding 14/16 |
| `.t24-live-run-head` | text/750 + 右侧 `.t24-data` 状态词 | flex |
| `.t24-live-step`（数据源/创建任务/Agent Pipeline/写入报告）| 常态 border+muted；done primary；active primary+3px 外发光 | grid 4×1fr，chip min-h 34px，radius 999px，12px |
| `.t24-stage-chip`（运行中 10 段进度）| 常态 subtle+surface；done primary（✓）；active primary+脉冲（⟳）；未开始 · | radius 999px，padding 4/11，12px |

### 5.4 Ticker Hero `.t24-ticker-hero`
grid `1.2fr 1fr auto`：左=名称+市场徽标/代码；中=现价/涨跌；右=`.caps`决策状态 + 大徽章 + tokens/成本。价格前缀 us `$`、cn `¥`、其它 `HK$`。

| 元素 | 颜色 | 尺寸 |
|---|---|---|
| `.t24-ticker-hero` | 底 surface，边框 **border_strong** | radius **8px**，padding 22/24 |
| `.t24-title` 名称 | text | Inter 30px/800 |
| `.t24-ticker-code` 市场徽标 | 底 surface_high，文字 muted | Geist Mono 11px，padding 3/6，radius 8px |
| `.t24-hero-price` 现价 | **primary** | Geist Mono 34px/800 |
| 涨跌 `.t24-data` | `delta_color()`：涨=primary 绿 / 跌=danger 红 / 无数据=muted（与决策色相互独立）| Geist Mono |
| tokens/成本 | muted | 12px |

### 5.5 决策徽章 `badge()` `.t24-badge`
文字/边框=决策色（`border:1px solid currentColor`），底=决策色 14%。big→13px 否则 11px；Geist Mono，padding 3/11，radius 999px。

### 5.6 价格趋势图（Plotly `build_trend_figure`）
上方 `.caps`「Price Action · Decisions」，图隐藏工具条。

| 元素 | 颜色 | 尺寸 |
|---|---|---|
| 收盘价线 | **中性灰 muted** + 8% 面积填充（线本身不表意）| width 2.2 |
| MA20 | subtle 虚线 | width 1.4 |
| 决策标记 | BUY▲primary / SELL▼danger / HOLD●muted；无当日快照时落到最近前一交易日 | size 13，1px border 边框 |
| 图例 | muted，横排顶部 | Geist Mono 11px |
| 布局 | 透明底 | 高 380，hovermode x unified，带 rangeslider |
| 空态 | muted 14px | 「暂无价格快照，运行一次分析后生成」|

### 5.7 操作按钮行 `t24_decision_actions`
有记录时显示。普通模式：右侧「操作」按钮（进入删除模式）；删除模式：「取消」+「删除」（未勾选 disabled）。按钮高 38px，13px。

### 5.8 决策历史（普通模式）`.t24-decision-history`（=t24-bento）
头部 history 图标 +「决策历史」+ 右「最近 30 天 · N 条」。每条 `<details class='t24-decision-item'>`（当前 success run 默认 open）。summary grid `1fr auto auto`：圆点+日期+序号/状态 · 决策 chip · chevron。展开内嵌报告段。

| 元素 | 颜色 | 尺寸 |
|---|---|---|
| 卡 | 底 surface，边框 border，阴影 | radius 8px，padding 16/18 |
| `.t24-decision-history-title` | text | 18px/750 |
| meta | muted | Geist Mono 11px/700 大写 |
| `.t24-decision-dot` | decision 色 + 14% 光晕 | 9px |
| `.t24-decision-date` | text | Geist Mono 13px/650 |
| `.t24-decision-chip` | 文字 decision；边框 decision 72%；底 decision 12% | radius 999px，12px/700 |
| chevron | muted；open 旋转 90° 变 decision | 14px |

### 5.9 决策历史（删除模式）`t24_decision_select_card`
每行 `[0.06,0.94]`：复选框 + `.t24-decision-select-summary`（结构同上但不可折叠，勾选加 `is-selected` primary 高亮）。卡 radius 12px。

### 5.10 报告段 `report_cards_html` → `.t24-report-module`
段顺序 `[final, market, fundamentals, sentiment, news, bull, bear, research_mgr, trader, risk]`，标签取 `SEC`。每段 `<details>`（final 默认 open，`include_anchors` 加锚点供 scrollspy）。

| 元素 | 颜色 | 尺寸 |
|---|---|---|
| `.t24-report-module` | 底 surface，边框 border，**左边框 3px accent**，阴影 | radius 12px |
| summary | hover/open 底 surface_high | min-h 54px，grid `1fr auto auto` |
| `.t24-report-heading` | text（图标 17）| 15px/750 |
| `.t24-report-meta`「Generated · 时长」| muted | Geist Mono 11px/700 大写 |
| `.t24-report-text` 正文 | text，底 **surface_low**，边框 border | Inter 14px，line 1.65，radius 10px，padding 14/16 |
| 正文 h1–h4 | text | 20/18/16/15px/750 |
| 正文 table | th 底 surface_high，边框 border | 13px，min-width 520px |
| 正文 code/pre | 底 surface_high，边框 border | Geist Mono，radius 8px |
| 正文 blockquote | 左边框 3px primary，文字 muted | — |

### 5.11 删除确认弹窗 `confirm_analysis_delete_dialog`（`@st.dialog`）
说明 + `.t24-delete-preview` 预览列表 + 「确认删除」(primary)/「取消」。预览块底 surface_low，边框 border，radius 10px。

---

## 6. 页面：关注（管理关注列表）— `manage()`

顺序：页头标题 → 运行时间表单 → 操作栏 → 列表容器（含行内抽屉）。列表在有 running/queued 且未编辑/删除时 `@st.fragment(run_every=5)` 每 5 秒刷新。

### 6.1 页头 `.t24-page-title`
`flex; gap:10px`：manage 图标(23，primary) +「管理关注列表」(text 30px/800，≤760px→26px) + 副标题(muted 13px)。

### 6.2 运行时间表单 `run_time_form`
`[2,1]` 底对齐：文本框「每日运行时间 (HH:MM)」+「保存时间」按钮。正则 `^([01]\d|2[0-3]):[0-5]\d$` 校验。

### 6.3 操作栏 `[2.3,1.4,1.1]` 垂直居中
| 元素 | 功能 | 颜色 | 尺寸 |
|---|---|---|---|
| `.t24-wl-summary`「今日分析 · **done** / total 已完成」| 统计 | text，`<b>` primary/750 | 14px |
| 「运行今日剩余（N）」`wl_run_remaining` | 有剩余时 primary，点击→批量确认 | primary 底 + primary_text | width stretch |
| 「今日已全部完成 ✓」| 无剩余时替代 | muted | 13px 居中 |
| 「＋ 添加标的」`wl_add` | 打开添加弹窗 | 默认次级 | width stretch |
> 空列表：`st.info("暂无标的，点「添加标的」开始。")` 并 return。

### 6.4 列表容器 + 单行 `_render_watchlist_rows`
容器 `.st-key-t24_watchlist`：`border:0.5px solid border; radius:12px; background:surface; padding:12px 20px; margin-top:12px`。
每行 `st.columns([0.5, 8.0, 0.7, 0.7], gap="small", vertical_alignment="center")`：运行状态图标 · 信息 markdown · 编辑 · 删除。信息 markdown 有 `translateY(-8px)` 拉齐按钮。
`.t24-wl-row` grid：**`120px 96px minmax(0,1fr) auto; gap:16px`** = 名称 / 代码 / 阈值 / 徽章（`justify-self:end`）。
> DOM 实测（视口 1572）：名称 x=314，代码 x=450，阈值 x=562，徽章右缘对齐——四列上下对齐 ✓。

| 元素 | 功能 | 颜色 | 尺寸/位置 |
|---|---|---|---|
| `.t24-wl-name` | 标的名（`name or symbol`）| text | 650，省略号，列 120px 左对齐 |
| `.t24-wl-code` | 代码 | muted | Geist Mono 12px，列 96px 左对齐 |
| `.t24-wl-th` | 阈值摘要 | muted | 12px，列 1fr 左对齐，省略号 |
| `.t24-today-badge` | 运行状态徽章 | 基础 border 边框 + subtle 文字 | Geist Mono 11px，padding 2/10，radius 999px，右对齐 |

徽章状态（`today_run_state`）：待运行(idle，基础)/排队中(queued，基础)/运行中(running，primary 实边)/今日已跑(done，primary 边+底 9%)/今日失败(failed，danger 边)。

#### 行内图标按钮（34×34px，radius 9px，边框 border，底 surface，子元素隐藏）
| 按钮 | 图标（`::before` mask）| 颜色 | mask 尺寸 |
|---|---|---|---|
| runstate `idle` | ▶ 播放三角 | primary | 17px |
| runstate `done` | ✓ 对勾 | primary（边 primary 38%，底 9%）| 17px |
| runstate `running` | ↻ 循环箭头（`t24spin` 旋转）| primary（边 primary 实色）| 17px |
| runstate `failed` | ↻ 同 running（不转）| danger（边 danger 40%）| 17px |
| runstate `queued` | 🕘 时钟 | muted | 17px |
| `wledit-*` | 铅笔 | muted | 16px |
| `wldel-*` | 垃圾桶 | danger | 16px |
> runstate 非 disabled 点击→`start_batch_analysis([sym])`；disabled（running/queued）时 `opacity:1` 不置灰。help 来自 `_RUN_STATE_HELP`。

### 6.5 编辑抽屉 `_render_edit_drawer`（`wl_drawer_{sym}`）
点编辑后在该行下方展开，保存即收起。容器底 surface_high，边框 0.5px border，radius 10px，padding 12/14。内含 form：文本框「名称」+ `st.columns(3)` 三个阈值 number_input（上限/下限/日涨跌幅%，min 0 step 1）+ caption「填 0 表示不启用该预警阈值。」+ `[1,1,4]`「保存」(primary)/「取消」。
删除确认（同容器）：`st.warning` +「确认删除」(primary)/「取消」。

### 6.6 添加标的弹窗 `_add_ticker_dialog`（`@st.dialog`）
`[2,2]`（代码 / 名称可选）→ `columns(3)` 三阈值 → caption → `columns(2)`「添加并校验数据源」(primary，校验 `prices.snapshot` 取得到) /「取消」。placeholder `NVDA / 600519.SS / 0700.HK`。

### 6.7 批量运行确认弹窗 `_batch_run_confirm`（`@st.dialog`）
显示「将运行 **N** 只：代码…」+ caption「研究深度 depth 轮 · 最多 3 并发 · 预计约 est_min 分钟、约 $est_cost」（per_min=5/12/18 随 depth；cost=N×0.15）。超预算显 warning。`columns(2)`「开始运行」(primary)/「取消」。

---

## 7. 设置弹窗 — `render_settings_dialog()`（`@st.dialog("设置", width="large")`）

两分区（`.t24-set-sec` 标题：muted 12px/750 大写，下边框 border）。控件均 Streamlit 原生（无专门着色）。

### 7.1 分析引擎（通用配置）
| 元素 | 控件 / 选项 |
|---|---|
| LLM 供应商 `set_provider` | selectbox，17 项（OpenAI/Google/Anthropic/xAI/DeepSeek/Qwen/GLM/MiniMax/OpenRouter/Mistral/Kimi/Groq/NVIDIA NIM/Azure/Bedrock/Ollama/OpenAI-compatible），默认 index 4（deepseek）|
| 深思考 / 快思考模型 | `columns(2)` → `_model_field`：有目录 selectbox，否则/自定义勾选→text_input；目录来自 `model_catalog.get_model_options` |
| 接口地址 `set_url_{provider}` | text_input，种子=当前或 `PROVIDER_DEFAULT_URL` |
| 研究深度 `set_depth` | radio：浅·1轮=1 / 中·3轮=3 / 深·5轮=5 |
| 报告输出语言 `set_lang` | selectbox 11 项（中文/English/日本語/한국어/Español/Português/Français/Deutsch/Русский/العربية/हिन्दी），fallback English |
| 分析师团队 `set_analysts` | multiselect：市场技术面(market)/情绪面(social)/新闻面(news)/基本面(fundamentals)，默认全选 |
| 数据源（高级）expander | 4×text_input（行情/技术指标/基本面/新闻），默认 `yfinance` |
| API key 状态 caption | 按 `PROVIDER_KEY_ENV` 检测 env：「🔑 {ENV}：已配置 ✅」/「未检测到 ⚠️」|

`PROVIDER_DEFAULT_URL`（部分）：openai `https://api.openai.com/v1`、deepseek `https://api.deepseek.com`、anthropic `https://api.anthropic.com/`、xai `https://api.x.ai/v1`、openrouter `https://openrouter.ai/api/v1`、kimi `https://api.moonshot.ai/v1`、groq `https://api.groq.com/openai/v1`、ollama `http://localhost:11434/v1`；google/azure/bedrock/openai_compatible = None。
`PROVIDER_KEY_ENV`（部分）：openai→OPENAI_API_KEY、deepseek→DEEPSEEK_API_KEY、qwen→DASHSCOPE_API_KEY、kimi→MOONSHOT_API_KEY、bedrock→AWS_ACCESS_KEY_ID；ollama/openai_compatible 无映射。

### 7.2 网站设置
`columns(2)`：左「每日运行时间 (HH:MM)」`set_run_time` +「每日成本预算 (USD)」`set_budget`；右「输入单价 ($/1M)」`set_price_in` +「输出单价 ($/1M)」`set_price_out`（`%.2f`）。
通知：「启用通知（Telegram）」`set_notify_en` toggle + `columns(2)`「通知渠道」`set_notify_ch`（默认 telegram）/「通知目标」`set_notify_tg`。

### 7.3 底部
`divider` → `columns([1,1])`：「保存」(primary，`set_save`：写 `save_settings`，depth 同步 max_debate/risk_rounds，运行时间变更则 `save_watchlist`，toast「设置已保存，下次分析生效」)/「取消」。

---

## 8. 共享组件与辅助

- **`icon(name, size=18)`**：24×24 viewBox 描边 SVG，`stroke-width:1.8`，`stroke-linecap:square`，`currentColor`。常用尺寸 15（导航）/17（pipeline、行内）/18（卡标题）/23（页标题）。未知名回退 `spark`。
- **`badge(dec, big=False)`**：见 §5.5。
- **`.caps`**：Geist Mono 11px 大写小标。
- **`.t24-data`**：Geist Mono 等宽数字/状态标签，通常 muted。
- **决策色解析**：`decision_accent()` BUY→primary / SELL→danger / 其余→muted。
- **时间文案**：`run_age()` 英文（Just now / Nm ago / Nh ago / Nd ago / Not analyzed）。

---

## 9. 已知差异 / 现状备注（非 bug 也需知晓）

1. **圆角不统一**：token 定义 card=12px，但 **bento 卡 / Ticker Hero / pipeline 卡硬编码 8px**；report-module / select-card / live-run 用 12px。
2. **死代码**：`kpi_row()` 无调用；`kpi_card()` 仅 history 页；`terminal_report_text()`/`report_sections()`/`report_sections_inline()` 定义但 detail_view 未用。
3. **遗留样式**：`open_ai_from_nav` 相关 CSS 存在但导航未用该 key（AI 入口是右下悬浮 launcher）。
4. **运行状态图标**：running 与 failed **共用循环箭头 SVG**（running 加旋转动画），无独立 ⟳ 变体；done=✓、idle=▶、queued=时钟。
5. **无 ECharts / 无终端报告块**：趋势图=Plotly，报告=可折叠 Markdown 卡。
6. **`save_settings` 写 yaml 会丢注释**（`yaml.safe_dump`）。

---

## 10. 交互状态规范

- **运行中**：节点级进度（pipeline 侧栏 + stage-chip 逐个点亮），非整体转圈；局部 fragment 刷新不整页 reload。
- **空态**：每区一句引导 + 动作（如「运行一次分析后生成」「暂无标的，点添加标的开始」）。
- **错误**：先说发生了什么（如「最近运行失败」「数据源取不到」），无 "Error:" 前缀。
- **成功**：状态本身即反馈（徽章变色 / 结论出现），仅关键写操作用 toast。
- **悬停**：卡片、导航项、图标按钮均有可感知反馈（底色/透明度变化）。
