# Trading24 AI Chatbot Demo

目标：把右下角 AI 小浮窗升级成类似 ChatGPT / Claude 的聊天软件式工作区。

## Demo

- 打开 `index.html` 可直接查看原型。
- `demo-desktop-wide.png` 是 1440px 桌面三栏截图。
- `demo-desktop.png` 是窄屏聊天形态截图。

## 正式改造后的页面形态

AI 不再只是右下角固定小面板，而是一个独立聊天模式：

- 左侧：会话列表，支持新对话和历史会话切换。
- 中间：主消息流，消息区滚动，底部 composer 固定。
- 右侧：上下文面板，展示当前分析范围、引用的 run_id、决策分布和手动选择记录入口。
- 背景：可以保留当前仪表盘作为上一层页面，也可以把 AI 做成一个独立 route/view。

## 建议落地方式

1. 在 `trading24/dashboard/app.py` 增加 `view == "AI 助手"` 或 `ai_mode_open` 的全屏聊天模式。
2. 把现有 `render_ai_plugin()` 拆成：
   - `render_ai_launcher()`
   - `render_ai_chat_view()`
   - `render_ai_context_panel()`
   - `render_ai_message_stream()`
   - `render_ai_composer()`
3. `st.session_state.ai_messages` 保留，但增加 `ai_threads` / `active_ai_thread_id`，用于会话列表。
4. `ai_selected_run_ids` 从面板内隐藏高级项，升级为右侧上下文面板的主控件。
5. 输入区始终固定在聊天视图底部，不再和消息内容一起滚动。

## 设计取舍

- 推荐先做独立 AI 页面，而不是继续扩展右下角浮窗。复杂问答需要空间，浮窗适合轻量提醒，不适合长对话。
- 移动端隐藏会话列表和右侧上下文，保留一个完整聊天主屏。
- AI 回复必须保留引用 chip，让用户知道答案来自哪些分析记录。
