# Trading24 Gemini HTML 组件映射与页面设计蓝图

## 结论

这两份 HTML 应作为 Trading24 新一版 UI 的主参考。当前项目旧设计里的暖白、橙色、Playfair Display 更像内容型页面，不适合股票分析工具；Gemini 版本的 Inter + Geist Mono、emerald 信号色、深色终端感、bento grid 和顶部行情条，更符合“专业交易工作台”。

新 UI 不应该继续强化大侧栏 Streamlit 感，而应拆成两类页面：

- `Dashboard`：总览、运行状态、关注列表、提醒。
- `Deep Insights`：单标的详情、图表、决策历史、报告。

## 全局组件

### 1. Market Tape 顶部行情条

来源：两份 HTML 顶部 32px 行情条。

功能：
- 展示市场指数、调度状态、下一次运行时间。
- 替代当前侧栏里的“每日选股 · 计划 08:30”。

页面归属：
- 全站固定顶部第一层。

Trading24 映射：
- 当前可先显示 `S&P 500 / NASDAQ / HSI / Scheduler` 静态或最新缓存。
- 后续接入真实行情和调度倒计时。

设计要求：
- 高度 32px。
- Geist Mono，12px。
- 深色下用 `#0e0e0f` 背景；浅色下用白底细边框。

### 2. Top Navigation 顶部主导航

来源：Dashboard HTML 与 Deep Insights HTML 的顶部导航。

功能：
- 承载主页面切换：总览、分析、关注列表、设置。
- 承载搜索、主题切换、通知、账户/配置入口。

页面归属：
- 全站固定顶部第二层。

Trading24 映射：
- `Dashboard` -> 当前总览页。
- `Analysis` -> 单标的详情页。
- `Watchlist` -> 管理关注列表页。
- `Settings` -> 运行时间、模型、通知和预算设置；当前可先并入 Watchlist 管理页，后续拆出。

设计要求：
- 不再使用左侧大导航作为全局导航。
- 当前页用 emerald 下划线 + 加粗文字。
- 图标使用同一套线框图标，替换 Material Symbols。

### 3. Theme Toggle / Search / Notification Icon Buttons

来源：顶部导航右侧 icon buttons。

功能：
- 主题切换。
- 搜索 ticker。
- 通知中心入口。

页面归属：
- 全站顶部导航。

Trading24 映射：
- 主题切换保留。
- 搜索 ticker 后进入该标的详情。
- 通知按钮打开 Recent Alerts 或未来通知历史。

设计要求：
- 图标按钮 40x40。
- 不用 emoji。
- icon stroke、尺寸、颜色统一。

## Dashboard 页面组件

### 4. Watchlist Overview Header

来源：Dashboard HTML 左侧标题区域。

功能：
- 告诉用户当前区域是关注列表总览。
- 右侧提供 `View All` 或 `管理关注列表` 入口。

页面归属：
- Dashboard 主内容左上。

Trading24 映射：
- 标题中文化为“关注列表总览”。
- 副标题：“基于每日多智能体分析排序的高优先级标的。”
- 右侧按钮：进入 Watchlist 管理页。

设计要求：
- 标题 24px / 600。
- 副标题 14px muted。
- 按钮 ghost border。

### 5. Watchlist Bento Card

来源：Dashboard HTML 的 NVDA / TSLA / AAPL / MSFT 卡片。

功能：
- 展示每个标的的价格、涨跌幅、成交量或状态、迷你趋势、决策、分析时间。
- 卡片点击进入单标的详情页。

页面归属：
- Dashboard 主内容。

Trading24 映射：
- 当前 overview 股票卡升级为此组件。
- 必填数据：名称/代码、最新价、决策、最近运行时间、迷你图、一句话结论。
- 可选数据：涨跌幅、成交量、运行中状态。

设计要求：
- 2 列 bento grid，窄屏 1 列。
- 卡片左侧 4px 语义色条：增持绿、减持红、中性灰。
- 卡片内不要长正文撑高；结论最多两行，多余折叠到详情。
- badge 固定宽度，避免 BUY/SELL/HOLD 或中文长度造成跳动。

### 6. Daily Run Status

来源：Dashboard HTML 右侧 `Daily Run Status`。

功能：
- 展示本次批量分析整体进度。
- 展示 agent 阶段状态。

页面归属：
- Dashboard 右栏。

Trading24 映射：
- 当前 KPI `今日已分析/成本/增持/减持` 应移动到此区域或拆为紧凑 stat。
- 状态行对应：技术面、基本面、情绪面、消息面、交易员、风险复核。

设计要求：
- 右栏卡片，高度不超过 260px。
- 进度条用 emerald。
- 运行中的 agent 使用脉冲点或旋转图标。

### 7. Recent Alerts

来源：Dashboard HTML 右侧 `Recent Alerts`。

功能：
- 展示价格越界、波动异常、财报/新闻提醒。
- 后续可接通知发送记录。

页面归属：
- Dashboard 右栏。

Trading24 映射：
- 当前还没有完整通知模块时，先展示最近 failed run、价格阈值触发、最新决策变化。
- 未来接 `notifications` 表。

设计要求：
- 列表项无卡片嵌套，用水平分隔线。
- 每条含语义 icon、标题、时间和原因。
- 右上 `Clear` 只有有清理功能时再显示。

## Deep Insights 页面组件

### 8. Agent Pipeline SideNav

来源：Deep Insights HTML 左侧固定 `Agent Pipeline`。

功能：
- 在单标的分析详情页展示 agent 维度导航。
- 当前选中项高亮，例如 Technicals。

页面归属：
- 只在 Analysis / 详情页出现，不作为全站导航。

Trading24 映射：
- 技术面、基本面、情绪面、消息面、风险复核、最终决策。
- 点击后滚动到对应报告段，或过滤下方报告内容。

设计要求：
- 宽度 240-256px。
- 活跃项用左侧 emerald 4px 竖条。
- 不在 Dashboard/History/Watchlist 管理页出现。

### 9. Ticker Header & Price Module

来源：Deep Insights HTML 顶部 `NVDA / price / consensus / latest decision`。

功能：
- 单标的详情页的核心信息摘要。
- 在图表前明确当前标的、市场、价格、涨跌、最新决策。

页面归属：
- Analysis / 详情页顶部。

Trading24 映射：
- 当前 detail_view 顶部标题升级为此组件。
- 最新决策 badge 统一为“增持 / 中性 / 减持”。
- 市场标签由代码后缀推断：NASDAQ / SSE / HKEX。

设计要求：
- 价格用 Geist Mono 大号。
- 决策 CTA 块用语义色，不能和普通按钮混淆。
- 详情页不再需要“返回总览”大按钮，可用顶部导航或轻量返回链接。

### 10. Chart Module

来源：Deep Insights HTML 的 8 列图表模块。

功能：
- 展示价格趋势、K 线或折线、决策点。
- 支持时间范围切换。

页面归属：
- Analysis / 详情页主区域。
- History 详情也复用简化版。

Trading24 映射：
- 当前 Plotly 趋势图继续使用，但外层卡片改为 chart module。
- 当前可以先保留折线；K 线作为后续升级。

设计要求：
- 卡片头部包含 `1D / 1W / 1M / YTD` 或更适合本项目的 `1M / 3M / 6M / 1Y`。
- 决策点用中文图例 + 形状，不只靠颜色。
- 深色下图表网格线必须低对比但可见。

### 11. Decision History Log

来源：Deep Insights HTML 右侧 `Decision History`。

功能：
- 展示该标的近 30 天/近 N 次分析记录。
- 快速比较价格、决策、置信度/来源。

页面归属：
- Analysis / 详情页右栏。
- History 页面也复用为主列表。

Trading24 映射：
- 当前 History 的运行记录列表升级为此组件。
- 当前没有 confidence 时，用 `status / run id / agent 来源` 替代。
- 支持选择多天后删除、对比、发送给 AI。

设计要求：
- 每行用 data mono 排列日期和价格。
- 决策 badge 固定宽度。
- hover 行高亮，不使用大按钮铺满整行。

### 12. Bottom Tabs

来源：Deep Insights HTML 底部 `Agent Reports / Technicals / News Feed`。

功能：
- 控制详情页下半区内容维度。

页面归属：
- Analysis / 详情页。

Trading24 映射：
- `Agent Reports`：完整报告。
- `Technicals`：价格指标和技术面段。
- `News Feed`：消息面/情绪面段。
- 后续可加 `Risk`。

设计要求：
- tab 使用下划线，不用 Streamlit radio。
- 当前 Streamlit 难以做真 tab 时，可用 segmented control 或按钮组模拟。

### 13. Agent Report Card / Terminal Block

来源：Deep Insights HTML 下方报告卡。

功能：
- 展示多智能体分析结论。
- 强化“AI pipeline 产出”的可信结构。

页面归属：
- Analysis / 详情页下半区。
- History 的“完整报告”展开后复用。

Trading24 映射：
- 当前 expander 报告段改为 report card。
- 每个报告卡显示：agent 名称、生成时间、状态/置信度、正文。

设计要求：
- 报告正文用 Geist Mono 或等宽终端块。
- 左侧 4px 竖线表示语义状态或 agent 类别。
- 不要所有段落默认展开；详情页默认展开最终决策和摘要，其他按 tab/折叠。

## History 页面设计

HTML 没有单独 History 页面，但可以由 Deep Insights 的 `Decision History Log` 扩展。

目标功能：
- 选择标的。
- 按日期范围查看运行记录。
- 选择多条记录：删除、对比、发送给 AI。
- 查看单条最终结论；完整报告按需展开。

推荐布局：
- 顶部：页面标题 + 标的选择 + 日期范围。
- 中部：Decision History Log 全宽表格化。
- 右侧或下方：所选记录摘要卡。
- 底部：Agent Report Card 折叠区。

设计要求：
- 不再把 8-10 个报告 expander 一次性铺满首屏。
- 删除按钮只在选择后进入 action bar，并保留二次确认。
- AI 助手入口放在 action bar 和右下浮窗两处。

## Watchlist / Settings 页面设计

HTML 中 Watchlist 和 Settings 只是导航项，没有完整页面。需要结合当前管理页设计。

目标功能：
- 拖拽排序关注列表。
- 编辑名称、阈值、涨跌幅。
- 删除需确认。
- 添加标的并校验数据源。
- 设置每日运行时间。

推荐布局：
- 页面标题：Watchlist Settings / 关注列表设置。
- 左侧大卡：当前标的表格/拖拽列表。
- 右侧卡：运行时间、通知状态、成本预算。
- 底部卡：添加标的表单。

设计要求：
- 拖拽条目改成 bento list row，不再像表单输入框。
- 编辑不要在每行右侧堆多个大按钮；使用 row action：编辑 / 删除。
- 表单项统一 44px 高度、4px 圆角、底边或细边框。

## AI Assistant 插件设计

HTML 没有这个组件，但当前项目需要保留。

目标功能：
- 用户选择多天记录作为长文本附件。
- 用户输入 prompt。
- AI 读取指定数据并回答。
- 可查看历史对话。

推荐布局：
- 右下固定客服式面板。
- 面板头部：AI Assistant + 附件数 badge + 关闭。
- 内容区：附件选择、prompt、发送按钮、对话历史。

设计要求：
- 颜色和 Report Terminal 保持一致。
- “发送给 AI”按钮用 primary；“清空附件/清空对话”用 ghost。
- 发送中显示小型 spinner，不使用整页 loading。

## 图标规范

Gemini HTML 使用 Material Symbols，但项目最终应替换为一致的线框图标。

建议映射：
- Dashboard：grid / dashboard。
- Analysis：chart / activity。
- Watchlist：list / rows。
- Settings：sliders / settings。
- Pipeline：cpu / circuit。
- History：clock / rotate。
- Reports：file-text。
- Technicals：candlestick / chart。
- News：newspaper。
- Risk：shield。
- AI：message / bot。

实现策略：
- Streamlit 内优先用内联 SVG，避免 Material font 泄漏成文字。
- 按 16 / 20 / 24 三档尺寸，不混用粗细。
- 不使用 emoji 作为结构图标。

## 页面重构顺序

1. 全局 Shell：Market Tape + Top Navigation + 主题切换。
2. Dashboard：Watchlist Bento Cards + Daily Run Status + Recent Alerts。
3. Analysis：Ticker Header + Chart Module + Decision History + Agent Report Cards。
4. History：复用 Decision History Log，补多选、删除确认、发送 AI。
5. Watchlist Settings：管理页重排为设置工作台。
6. AI Assistant：换成同风格浮窗。

## 当前项目需要停用的旧方向

- 停用橙色主线和暖白杂志风作为主风格。
- 停用 Playfair Display。
- 停用大侧栏作为全站主导航。
- 停用页面内大量 Streamlit 默认 expander 直接铺开。
- 停用 emoji/Material 混用图标。

## 保留的现有功能

- SQLite 数据接口不动。
- 关注列表增删改、拖拽排序保留。
- 历史记录选择删除与二次确认保留。
- AI 助手的附件选择、prompt 和历史保留。
- Plotly 图表先保留，外观和容器先按新设计系统改。
