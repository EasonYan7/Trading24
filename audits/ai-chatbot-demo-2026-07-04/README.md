# Trading24 AI Chatbot Demo

目标：把右下角 AI 小浮窗升级成类似 ChatGPT / Claude 的聊天软件式弹窗工作区。

## Demo

- 打开 `index.html` 可直接查看原型。
- `demo-desktop-wide.png` 是桌面右下大弹窗截图。
- `demo-desktop.png` 是窄屏弹窗形态截图。

## 正式改造后的页面形态

AI 仍然是页面上的弹窗，不改变底层 Trading24 仪表盘布局。弹窗内部升级成聊天软件式结构：

- 左侧：会话列表，支持新对话和历史会话切换。
- 中间：主消息流，消息区滚动，底部 composer 固定。
- 右侧：上下文面板，展示当前分析范围、引用的 run_id、决策分布和手动选择记录入口。
- 外层：仍由右下角 AI launcher 打开，弹窗覆盖在页面上方，不挤压页面。

## 建议落地方式

1. 保留 `render_ai_plugin()` 的 launcher，但把当前小面板升级为大尺寸 fixed popup。
2. 把现有 `render_ai_plugin()` 拆成：
   - `render_ai_launcher()`
   - `render_ai_popup_shell()`
   - `render_ai_thread_list()`
   - `render_ai_context_panel()`
   - `render_ai_message_stream()`
   - `render_ai_composer()`
3. `st.session_state.ai_messages` 保留，但增加 `ai_threads` / `active_ai_thread_id`，用于会话列表。
4. `ai_selected_run_ids` 从面板内隐藏高级项，升级为右侧上下文面板的主控件。
5. 输入区始终固定在弹窗底部，不再和消息内容一起滚动。

## 设计取舍

- 推荐把弹窗做大，而不是继续使用 356px 小面板。复杂问答需要更宽的消息流和稳定输入区。
- 移动端弹窗改为接近全屏，隐藏会话列表和右侧上下文，保留一个完整聊天主屏。
- AI 回复必须保留引用 chip，让用户知道答案来自哪些分析记录。
