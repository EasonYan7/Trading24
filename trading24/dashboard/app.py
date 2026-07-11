"""Trading24 看板（Agentic 主题）。启动：uv run streamlit run trading24/dashboard/app.py

信息架构：
  总览页 = 关注列表卡片（迷你趋势图 + 决策 + 一句话结论），可扫读。
  详情页 = 点击卡片进入：大趋势图 + 关键参数 + 完整各 agent 报告。
"""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.parse import quote

import bleach
import httpx
import mistune
import streamlit as st
import streamlit.components.v1 as components
from streamlit_sortables import sort_items

from trading24.config import (
    load_settings, save_settings, Settings,
    load_watchlist, save_watchlist, TickerCfg, market_of, DATA_DIR,
)
from trading24.db import database as db
from trading24.db.models import Run
from trading24 import prices, runner
from trading24.dashboard import charts as charts_mod

charts_mod = importlib.reload(charts_mod)
build_trend_figure = charts_mod.build_trend_figure
build_sparkline = charts_mod.build_sparkline
_norm_decision = charts_mod._norm_decision

st.set_page_config(page_title="Trading24", layout="wide")

# AI 输入组件：带 @ 引用菜单的 composer（原生 textarea 做不了下拉选择交互）
_AI_COMPOSER = components.declare_component(
    "t24_ai_composer",
    path=str(Path(__file__).parent / "components" / "composer"),
)

THEMES = {
    "浅色": {
        "bg": "#f9fafb",
        "surface": "#ffffff",
        "surface_low": "#f6f3f4",
        "surface_high": "#f3f4f6",
        "text": "#1c1b1c",
        "muted": "#6b7280",
        "subtle": "#9ca3af",
        "border": "#e5e7eb",
        "border_strong": "#bbcabf",
        "primary": "#10b981",
        "primary_text": "#ffffff",
        "danger": "#ef4444",
        "warning": "#f59e0b",
        "tertiary": "#6366f1",
        "terminal": "#0a0a0a",
        "terminal_text": "#d1d5db",
        "shadow": "0 1px 3px rgba(15, 23, 42, .08)",
        "ai_user": "#ecfdf5",
        "ai_assistant": "#f9fafb",
    },
    "深色": {
        "bg": "#0a0a0a",
        "surface": "#111112",
        "surface_low": "#0f1111",
        "surface_high": "#1f2020",
        "text": "#f3f0f1",
        "muted": "#a3a3a3",
        "subtle": "#737373",
        "border": "#262626",
        "border_strong": "#3f3f46",
        "primary": "#4edea3",
        "primary_text": "#002113",
        "danger": "#ffb4ab",
        "warning": "#f5b93f",
        "tertiary": "#9699ff",
        "terminal": "#050505",
        "terminal_text": "#d1d5db",
        "shadow": "0 18px 60px rgba(0, 0, 0, .36)",
        "ai_user": "#102114",
        "ai_assistant": "#181818",
    },
}
if "theme_mode" not in st.session_state:
    st.session_state.theme_mode = "浅色"
ACTIVE_THEME = THEMES.get(st.session_state.theme_mode, THEMES["浅色"])
THEME_CSS = f"""
<style>
:root {{
  --t24-bg: {ACTIVE_THEME["bg"]};
  --t24-surface: {ACTIVE_THEME["surface"]};
  --t24-surface-low: {ACTIVE_THEME["surface_low"]};
  --t24-surface-high: {ACTIVE_THEME["surface_high"]};
  --t24-text: {ACTIVE_THEME["text"]};
  --t24-muted: {ACTIVE_THEME["muted"]};
  --t24-subtle: {ACTIVE_THEME["subtle"]};
  --t24-border: {ACTIVE_THEME["border"]};
  --t24-border-strong: {ACTIVE_THEME["border_strong"]};
  --t24-primary: {ACTIVE_THEME["primary"]};
  --t24-primary-text: {ACTIVE_THEME["primary_text"]};
  --t24-danger: {ACTIVE_THEME["danger"]};
  --t24-warning: {ACTIVE_THEME["warning"]};
  --t24-tertiary: {ACTIVE_THEME["tertiary"]};
  --t24-terminal: {ACTIVE_THEME["terminal"]};
  --t24-terminal-text: {ACTIVE_THEME["terminal_text"]};
  --t24-shadow: {ACTIVE_THEME["shadow"]};
  --t24-ai-user: {ACTIVE_THEME["ai_user"]};
  --t24-ai-assistant: {ACTIVE_THEME["ai_assistant"]};
  --t24-radius-control: 12px;
  --t24-radius-card: 12px;
  --t24-control-height: 44px;
  /* 主内容区宽度：流式，跟随视口放大、封顶 1600；两侧留白由 gutter 统一推导，
     固定定位的 Pipeline 左栏 / AI 面板留白都引用它，避免魔法数 1260 漂移 */
  --t24-shell: min(1600px, 94vw);
  --t24-gutter: calc((100vw - var(--t24-shell)) / 2);
}}
</style>
"""

BADGE = {
    "BUY": ("var(--t24-primary)", "增持"),
    "HOLD": ("var(--t24-muted)", "中性"),
    "SELL": ("var(--t24-danger)", "减持"),
}
SEC = {"market": "技术面", "sentiment": "情绪面", "news": "消息面", "fundamentals": "基本面",
       "bull": "多方观点", "bear": "空方观点", "research_mgr": "研究经理",
       "trader": "交易员", "risk": "风险复核", "final": "最终决策"}

REPORT_MARKDOWN = mistune.create_markdown(escape=True, plugins=["table"])
REPORT_ALLOWED_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "code", "del", "div", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "li", "ol", "p", "pre", "strong", "table", "tbody",
    "td", "th", "thead", "tr", "ul",
]
REPORT_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "th": ["align"],
    "td": ["align"],
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600;700;800&display=swap');
.stApp, .stApp p, .stApp li, .stApp label, .stMarkdown, h1,h2,h3,h4,
[data-testid="stExpander"] summary, .stRadio, .stSelectbox {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  color:var(--t24-text);
}
.stApp div, .stApp span, .stApp button, .stApp input, .stApp textarea {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
[data-testid="stExpanderToggleIcon"], [data-testid="stIconMaterial"],
span.material-icons, .material-symbols-rounded, [class*="material-icons"], [data-testid="stIcon"] {
  font-family:'Material Symbols Rounded','Material Symbols Outlined','Material Icons' !important; }
.stApp { background:var(--t24-bg); color:var(--t24-text); }
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
header {
  background:var(--t24-bg) !important;
}
header.stAppHeader,
[data-testid="stHeader"] {
  display:none !important;
  height:0 !important;
  min-height:0 !important;
}
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], [data-testid="stSidebarCollapsedControl"] {
  display:none !important;
}
.block-container {
  max-width:var(--t24-shell);
  padding-top:0 !important;
  padding-bottom:3.2rem;
}
.stMarkdown, .stMarkdown p { overflow-wrap:anywhere; word-break:break-word; }
[data-testid="stSidebar"] {
  display:none !important;
  background:var(--t24-surface);
  border-right:1px solid var(--t24-border);
  width:300px !important;
  min-width:300px !important;
  max-width:300px !important;
}
[data-testid="stSidebar"] > div:first-child {
  width:300px !important;
  min-width:300px !important;
  max-width:300px !important;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:.75rem; }
[data-testid="stForm"],
[data-testid="stForm"] > div {
  background:transparent !important;
  color:var(--t24-text) !important;
}
[data-testid="stVerticalBlockBorderWrapper"] {
  border-color:var(--t24-border) !important;
  border-radius:var(--t24-radius-card) !important;
  background:var(--t24-surface) !important;
}
[data-testid="stVerticalBlockBorderWrapper"] > div {
  background:transparent !important;
  border-radius:var(--t24-radius-card) !important;
}
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
  background:var(--t24-surface) !important;
  border:1px solid var(--t24-border-strong) !important;
  box-shadow:0 1px 0 rgba(0,0,0,.04) !important;
  color:var(--t24-text) !important;
  border-radius:var(--t24-radius-control) !important;
  min-height:var(--t24-control-height);
}
[data-baseweb="select"],
[data-baseweb="select"] *,
[data-testid="stSelectbox"] div[data-baseweb="select"] svg,
[data-testid="stMultiSelect"] div[data-baseweb="select"] svg {
  color:var(--t24-text) !important;
}
[data-baseweb="select"] input::placeholder,
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder {
  color:var(--t24-muted) !important;
  opacity:1 !important;
}
[data-baseweb="popover"] > div,
[data-baseweb="popover"] ul {
  background:var(--t24-surface) !important;
  border:1px solid var(--t24-border) !important;
  border-radius:var(--t24-radius-control) !important;
  color:var(--t24-text) !important;
}
[data-baseweb="popover"] li,
[data-baseweb="popover"] li * {
  background:var(--t24-surface) !important;
  color:var(--t24-text) !important;
}
[data-baseweb="popover"] li:hover,
[data-baseweb="popover"] li[aria-selected="true"] {
  background:var(--t24-surface-high) !important;
}
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea {
  background:var(--t24-surface) !important;
  border-color:var(--t24-border) !important;
  color:var(--t24-text) !important;
  border-radius:var(--t24-radius-control) !important;
  min-height:var(--t24-control-height);
}
[data-testid="stTextInput"] input:disabled,
[data-testid="stNumberInput"] input:disabled,
[data-testid="stTextArea"] textarea:disabled {
  opacity:0.6 !important;
  color:var(--t24-muted) !important;
  -webkit-text-fill-color:var(--t24-muted) !important;
  background:var(--t24-surface-low) !important;
  border-color:var(--t24-border) !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
  border-color:var(--t24-tertiary) !important;
  box-shadow:0 0 0 1px var(--t24-tertiary) inset !important;
}
[data-testid="stNumberInput"] button {
  background:var(--t24-surface) !important;
  border-color:var(--t24-border) !important;
  color:var(--t24-text) !important;
  border-radius:10px !important;
}
[data-testid="stNumberInput"] button:disabled {
  opacity:1 !important;
  color:var(--t24-subtle) !important;
  background:var(--t24-surface-low) !important;
  border-color:var(--t24-border) !important;
  cursor:not-allowed;
}
.mono { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; }
.caps {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px;
  letter-spacing:0;
  text-transform:uppercase;
  color:var(--t24-muted);
}
/* 控件标签变体：与表单控件文字同号(13px)，用于和输入框/下拉框并排的标签 */
.caps.caps-field {
  font-size:13px;
}
/* 交易计划条（详情页 hero 下方）：建仓区/止损/目标价/仓位/时间视野 */
.t24-plan { display:flex; flex-wrap:wrap; gap:10px; margin:2px 0 16px; }
.t24-plan-item { flex:1 1 150px; border:1px solid var(--t24-border); border-radius:12px;
  background:var(--t24-surface); padding:10px 14px; }
.t24-plan-label { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace;
  font-size:11px; color:var(--t24-muted); text-transform:uppercase; margin-bottom:4px; }
.t24-plan-value { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace;
  font-size:15px; font-weight:600; color:var(--t24-text); white-space:nowrap; }
/* 总览卡片里的一行式交易计划 */
.t24-ov-plan { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace;
  font-size:11px; color:var(--t24-muted); margin:auto 0 0; padding-top:8px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.t24-ov-plan b { color:var(--t24-text); font-weight:600; }
/* 复盘明细表（自绘，替代 data_editor：可控配色/胶囊/交互） */
.t24-rt-wrap { overflow-x:auto; border:1px solid var(--t24-border); border-radius:12px;
  background:var(--t24-surface); box-shadow:var(--t24-shadow); }
.t24-rt { border-collapse:collapse; table-layout:fixed; width:100%;
  min-width:960px; font-size:12.5px; }
.t24-rt th {
  font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:11px;
  color:var(--t24-muted); text-transform:uppercase; letter-spacing:.03em; font-weight:600;
  text-align:right; padding:9px 10px; border-bottom:1px solid var(--t24-border-strong);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  background:var(--t24-surface-low); }
.t24-rt th:nth-child(-n+4) { text-align:left; }
.t24-rt td {
  font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:12px;
  color:var(--t24-text); text-align:right; padding:8px 10px;
  border-bottom:1px solid var(--t24-border); white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }
.t24-rt td:nth-child(-n+4) { text-align:left; }
/* 隐藏的跟踪切换按钮（由表格圆点经 JS 代理点击，走局部重跑而非整页刷新） */
.st-key-t24_review_track_btns { display:none; }
/* 表头可点：排序/筛选（飞书式表头菜单） */
.t24-rt thead th { cursor:pointer; user-select:none; }
.t24-rt thead th:first-child { cursor:default; }
.t24-rt thead th:not(:first-child):hover { color:var(--t24-text); }
.t24-rt thead th[data-sorted]::after { content:" ↑"; color:var(--t24-primary); }
.t24-rt thead th[data-sorted="desc"]::after { content:" ↓"; color:var(--t24-primary); }
.t24-rt thead th[data-filtered] { color:var(--t24-primary); }
.t24-rt-menu { position:fixed; z-index:2147483200; min-width:150px; max-height:340px;
  overflow-y:auto; background:var(--t24-surface); border:1px solid var(--t24-border-strong);
  border-radius:10px; box-shadow:0 12px 32px rgba(0,0,0,.18); padding:6px;
  font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:12.5px; }
.t24-rtm-item { display:block; width:100%; text-align:left; padding:6px 10px;
  border:0; background:none; border-radius:6px; cursor:pointer;
  color:var(--t24-text); font:inherit; }
.t24-rtm-item:hover { background:var(--t24-surface-high); }
.t24-rtm-item.on { color:var(--t24-primary); font-weight:700; }
.t24-rtm-sep { border-top:1px solid var(--t24-border); margin:6px 0; }
.t24-rtm-cap { font-size:10.5px; color:var(--t24-subtle); padding:2px 10px 4px;
  text-transform:uppercase; letter-spacing:.05em; }
.t24-rtm-check { display:flex; align-items:center; gap:7px; padding:5px 10px;
  border-radius:6px; cursor:pointer; color:var(--t24-text); font-size:12px; }
.t24-rtm-check:hover { background:var(--t24-surface-high); }
.t24-rtm-check input { accent-color:var(--t24-primary); margin:0; }
.t24-rt tbody tr:last-child td { border-bottom:0; }
.t24-rt tbody tr { transition:background .12s ease; }
.t24-rt tbody tr:hover { background:var(--t24-surface-high); }
.t24-rt tbody tr.untracked { opacity:.45; }
/* 30天窗列做浅色带，视觉分隔三个时间窗 */
.t24-rt td:nth-child(11), .t24-rt th:nth-child(11) {
  background:color-mix(in srgb, var(--t24-text) 3.5%, transparent); }
.t24-rt .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
  margin-right:7px; vertical-align:middle; }
.t24-rt .sym { font-weight:700; }
.t24-rt .pos { color:var(--t24-primary); font-weight:600; }
.t24-rt .neg { color:var(--t24-danger); font-weight:600; }
.t24-rt .dim { color:var(--t24-subtle); }
.t24-rt .chip { display:inline-flex; align-items:center; justify-content:center;
  min-width:42px; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; }
.t24-rt .chip.win { color:#16A34A; background:color-mix(in srgb, #16A34A 14%, transparent); }
.t24-rt .chip.loss { color:var(--t24-danger);
  background:color-mix(in srgb, var(--t24-danger) 12%, transparent); }
.t24-rt .chip.flat { color:var(--t24-muted);
  background:color-mix(in srgb, var(--t24-muted) 13%, transparent); }
.t24-rt .chip.pending { color:var(--t24-subtle); background:transparent;
  border:1px dashed var(--t24-border-strong); font-weight:500; }
.t24-rt .ret { margin-left:6px; }
/* 时间窗格：判定胶囊居左、收益居右 */
.t24-rt .wcell { display:flex; align-items:center; gap:6px; }
.t24-rt .wcell .ret { margin-left:auto; }
.t24-rt a.track { display:inline-flex; width:18px; height:18px; border-radius:50%;
  border:1.5px solid var(--t24-border-strong); align-items:center; justify-content:center;
  color:var(--t24-primary-text); font-size:11px; line-height:1;
  text-decoration:none !important; }
.t24-rt a.track.on { background:var(--t24-primary); border-color:var(--t24-primary); }
.t24-rt a.track:hover { border-color:var(--t24-primary); }
/* 顶部摘要 tile 的补充内容：状态胶囊 / 分布比例条 / 小注 */
.t24-ov-chiprow { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
.t24-ov-chip { display:inline-flex; align-items:center; gap:5px;
  font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:11px;
  color:var(--t24-muted); border:1px solid var(--t24-border);
  border-radius:999px; padding:2px 9px; white-space:nowrap; }
.t24-ov-chip i { width:6px; height:6px; border-radius:50%; display:inline-block;
  background:var(--t24-subtle); flex:0 0 auto; }
.t24-ov-dist-bar { display:flex; height:8px; border-radius:999px; overflow:hidden;
  margin:12px 0 10px; background:var(--t24-surface-high); }
.t24-ov-dist-bar span { display:block; height:100%; }
.t24-ov-dist-row { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace;
  font-size:11px; margin-top:4px; color:var(--t24-muted); }
.t24-ov-tile-note { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace;
  font-size:11px; color:var(--t24-muted); margin-top:10px; }
[data-testid="stMetric"] {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-radius:12px;
  padding:12px 14px;
  overflow:hidden;
  box-shadow:none;
}
[data-testid="stMetricLabel"] p {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px !important;
  letter-spacing:0;
  text-transform:uppercase;
  color:var(--t24-muted) !important;
}
[data-testid="stMetricValue"] {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  color:var(--t24-text) !important;
  font-weight:600;
}
[data-testid="stExpander"] {
  border:1px solid var(--t24-border);
  border-radius:var(--t24-radius-card);
  background:var(--t24-surface) !important;
  color:var(--t24-text) !important;
}
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary,
[data-testid="stExpander"] [data-testid="stMarkdownContainer"],
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] * {
  background:transparent !important;
  color:var(--t24-text) !important;
}
.t24-icon {
  display:inline-block;
  vertical-align:-2px;
  color:currentColor;
  flex:0 0 auto;
}
.t24-icon-label,
.t24-page-title {
  display:flex;
  align-items:center;
  gap:10px;
}
.t24-badge {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px;
  padding:3px 11px;
  border-radius:999px;
  white-space:nowrap;
  border:1px solid currentColor;
}
.t24-title {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  font-size:30px;
  font-weight:800;
  letter-spacing:0;
  line-height:1.15;
  color:var(--t24-text);
}
.t24-concl {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  font-size:14px;
  line-height:1.55;
  color:var(--t24-muted);
}
/* 总览页：顶部摘要条 + 紧凑公司卡片网格（响应式，跟随宽度 2~4 列） */
.t24-ov-summary { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin:0 0 16px; }
.t24-ov-tile { background:var(--t24-surface-high); border-radius:12px; padding:14px 16px; }
.t24-ov-tile-label { font-size:13px; color:var(--t24-muted); margin-bottom:6px; }
.t24-ov-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(290px, 1fr)); gap:12px; }
.t24-ov-card { display:flex; flex-direction:column; color:inherit; background:var(--t24-surface);
  border:1px solid var(--t24-border); border-radius:12px; padding:14px 16px; box-shadow:var(--t24-shadow);
  transition:border-color .15s ease, background .15s ease; }
.t24-ov-card, .t24-ov-card:hover, .t24-ov-card * { text-decoration:none !important; }
.t24-ov-card:hover { border-color:var(--t24-border-strong); background:var(--t24-surface-high); }
.t24-ov-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }
.t24-ov-name { font-size:16px; font-weight:700; color:var(--t24-text); line-height:1.2;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.t24-ov-meta { display:flex; justify-content:space-between; align-items:baseline; gap:8px; margin-top:4px; }
.t24-ov-code { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:12px; color:var(--t24-muted); white-space:nowrap; }
.t24-ov-price { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:15px; color:var(--t24-text); }
.t24-ov-delta { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:12px; }
.t24-ov-spark { width:100%; height:30px; margin:8px 0; display:block; }
.t24-ov-concl { font-size:13px; color:var(--t24-muted); line-height:1.5; min-height:39px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.t24-ov-foot { display:flex; justify-content:space-between; align-items:center; margin-top:auto; padding-top:10px; }
/* 有计划行时：计划行先吃掉弹性空间钉到底部，footer 紧贴其后 */
.t24-ov-plan + .t24-ov-foot { margin-top:0; }
.t24-ov-foot-age { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace; font-size:11px; color:var(--t24-muted); }
.t24-ov-foot-cta { font-size:12px; color:var(--t24-primary); white-space:nowrap; }
.t24-page-title {
  font-size:30px;
  font-weight:800;
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  line-height:1.15;
  margin:2px 0 4px;
  color:var(--t24-text);
}
.t24-page-title .t24-icon { color:var(--t24-primary); }
.t24-muted { color:var(--t24-muted); font-size:13px; line-height:1.45; }
.t24-history-row { padding-top:6px; }
.t24-kpi-card {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-radius:12px;
  padding:12px 14px;
  min-height:74px;
}
.t24-kpi-label { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:11px; color:var(--t24-muted); margin-bottom:6px; }
.t24-kpi-value { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:20px; font-weight:700; line-height:1.2; white-space:nowrap; color:var(--t24-text); }
.t24-kpi-delta { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:11px; color:var(--t24-subtle); margin-top:4px; white-space:nowrap; }
.stButton button,
[data-testid="stFormSubmitButton"] button,
button[data-testid^="stBaseButton"] {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  border-radius:var(--t24-radius-control) !important;
  border:1px solid var(--t24-border) !important;
  background:var(--t24-surface) !important;
  color:var(--t24-text) !important;
  min-height:var(--t24-control-height);
  transition:background-color 180ms ease, border-color 180ms ease, color 180ms ease, box-shadow 180ms ease;
}
.stButton button *,
[data-testid="stFormSubmitButton"] button *,
button[data-testid^="stBaseButton"] * {
  color:inherit !important;
}
.stButton button:hover,
[data-testid="stFormSubmitButton"] button:hover,
button[data-testid^="stBaseButton"]:hover {
  border-color:var(--t24-primary) !important;
  background:var(--t24-surface-high) !important;
  color:var(--t24-text) !important;
}
.stButton button:focus-visible,
[data-testid="stFormSubmitButton"] button:focus-visible,
button[data-testid^="stBaseButton"]:focus-visible {
  outline:2px solid var(--t24-primary) !important;
  outline-offset:2px;
}
[data-testid="stBaseButton-primary"] {
  background:var(--t24-primary) !important;
  border-color:var(--t24-primary) !important;
  color:var(--t24-primary-text) !important;
  font-weight:700 !important;
}
[data-testid="stBaseButton-primary"]:hover {
  background:var(--t24-primary) !important;
  color:var(--t24-primary-text) !important;
  filter:brightness(.96);
}
[data-testid="stBaseButton-secondary"]:disabled,
[data-testid="stBaseButton-primary"]:disabled,
[data-testid="stFormSubmitButton"] button:disabled,
.stButton button:disabled {
  opacity:.44;
  cursor:not-allowed;
  background:var(--t24-surface-low) !important;
  border-color:var(--t24-border) !important;
  color:var(--t24-subtle) !important;
}
.t24-nav-title { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:11px; color:var(--t24-muted); margin:18px 0 8px; }
.t24-edit-note { font-size:13px; color:var(--t24-muted); line-height:1.5; margin:4px 0 8px; }
.t24-card {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-radius:8px;
  padding:16px;
  box-shadow:var(--t24-shadow);
}
.t24-tape {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:12px;
  line-height:32px;
  min-height:32px;
  margin:0 -16px;
  padding:0;
  color:var(--t24-tertiary);
  background:var(--t24-surface-low);
  border-bottom:1px solid var(--t24-border);
  overflow:hidden;
}
.t24-tape-track {
  display:flex;
  width:max-content;
  white-space:nowrap;
  will-change:transform;
  animation:t24-marquee 48s linear infinite;
}
.t24-tape:hover .t24-tape-track { animation-play-state:paused; }
.t24-tape-seg { flex:0 0 auto; padding-left:16px; }
@keyframes t24-marquee {
  from { transform:translateX(0); }
  to   { transform:translateX(-50%); }
}
@media (prefers-reduced-motion: reduce) {
  .t24-tape-track { animation:none; }
}
.t24-topbar {
  display:flex;
  align-items:center;
  gap:28px;
  margin:0 -24px 28px;
  padding:18px 24px;
  background:var(--t24-surface);
  border-bottom:1px solid var(--t24-border);
}
.st-key-t24_topnav {
  margin:0 -16px 28px;
  /* stVerticalBlock 自带 width:100% + max-width:100%，负右边距无法把右缘拉出去，
     需显式加宽 32px（左右各 16）并解除 max-width，否则右缘停在内容宽度、与全宽行情条对不齐 */
  width:calc(100% + 32px);
  max-width:none;
  padding:12px 16px;
  background:var(--t24-surface);
  border-bottom:1px solid var(--t24-border);
}
.st-key-t24_topnav [data-testid="stHorizontalBlock"] {
  gap:8px;
  align-items:center;
}
.st-key-t24_topnav [data-testid="stColumn"] {
  display:flex;
  align-items:center;
}
.st-key-t24_topnav [data-testid="stColumn"] > div {
  width:100%;
}
.st-key-t24_topnav .stMarkdown,
.st-key-t24_topnav [data-testid="stMarkdownContainer"],
.st-key-t24_topnav .stMarkdown p {
  height:44px;
  margin:0 !important;
  line-height:1 !important;
  display:flex;
  align-items:center;
}
.st-key-t24_topnav .stButton button {
  height:44px !important;
  min-height:44px !important;
  box-sizing:border-box !important;
  padding:0 10px !important;
  border-color:transparent !important;
  background:transparent !important;
  box-shadow:none !important;
  color:var(--t24-muted) !important;
  font-weight:600 !important;
  line-height:1 !important;
  display:flex !important;
  align-items:center !important;
  justify-content:center !important;
}
.st-key-t24_topnav .stButton button p {
  margin:0 !important;
  line-height:1 !important;
  font-size:14px !important;
  padding:6px 0;
  border-bottom:2px solid transparent;
}
.st-key-t24_topnav [data-testid="stBaseButton-primary"] {
  color:var(--t24-primary) !important;
  background:transparent !important;
  border:1px solid transparent !important;
  box-shadow:none !important;
  border-radius:var(--t24-radius-control) !important;
}
.st-key-t24_topnav [data-testid="stBaseButton-primary"] p {
  border-bottom-color:var(--t24-primary) !important;
  color:var(--t24-primary) !important;
}
.st-key-t24_topnav button[data-testid="stBaseButton-primary"] [data-testid="stMarkdownContainer"] p {
  border-bottom-color:var(--t24-primary) !important;
  color:var(--t24-primary) !important;
}
.st-key-t24_topnav [data-testid="stTextInput"] input {
  height:44px !important;
  min-height:44px !important;
  background:var(--t24-surface) !important;
  border-radius:var(--t24-radius-control) !important;
}
.st-key-t24_topnav [data-testid="stSelectbox"] {
  width:100%;
}
.st-key-t24_topnav [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
  height:44px !important;
  min-height:44px !important;
  box-sizing:border-box !important;
  background:var(--t24-surface) !important;
  border-color:var(--t24-border) !important;
  border-radius:var(--t24-radius-control) !important;
}
.st-key-t24_topnav [data-testid="stSelectbox"] span,
.st-key-t24_topnav [data-testid="stSelectbox"] input {
  font-size:13px !important;
}
.st-key-search_add_ticker button {
  height:44px !important;
  min-height:44px !important;
  border:1px solid var(--t24-primary) !important;
  border-radius:var(--t24-radius-control) !important;
  background:color-mix(in srgb, var(--t24-primary) 9%, transparent) !important;
  color:var(--t24-primary) !important;
  font-weight:750 !important;
  white-space:nowrap !important;
}
.st-key-search_add_ticker button p {
  color:var(--t24-primary) !important;
  border-bottom:0 !important;
  font-size:13px !important;
}
.st-key-theme_toggle_dark button,
.st-key-theme_toggle_light button,
.st-key-open_settings button,
.st-key-open_ai_from_nav button {
  min-width:44px !important;
  width:44px !important;
  height:44px !important;
  min-height:44px !important;
  padding:0 !important;
  border:1px solid transparent !important;
  border-radius:var(--t24-radius-control) !important;
  display:flex !important;
  align-items:center !important;
  justify-content:center !important;
  color:var(--t24-muted) !important;
  font-size:0 !important;
  transition:color .15s ease, background-color .15s ease !important;
}
.st-key-theme_toggle_dark button:hover,
.st-key-theme_toggle_light button:hover,
.st-key-open_settings button:hover,
.st-key-open_ai_from_nav button:hover {
  color:var(--t24-text) !important;
  background:var(--t24-surface-high) !important;
  border:1px solid transparent !important;
  transform:none !important;
  box-shadow:none !important;
}
.st-key-theme_toggle_dark button::before,
.st-key-theme_toggle_light button::before,
.st-key-open_settings button::before,
.st-key-open_ai_from_nav button::before {
  content:"";
  display:block;
  width:20px;
  height:20px;
  background:currentColor;
  -webkit-mask-repeat:no-repeat;
  -webkit-mask-position:center;
  -webkit-mask-size:20px 20px;
  mask-repeat:no-repeat;
  mask-position:center;
  mask-size:20px 20px;
}
.st-key-theme_toggle_dark button *,
.st-key-theme_toggle_light button *,
.st-key-open_settings button *,
.st-key-open_ai_from_nav button * {
  font-size:0 !important;
  line-height:0 !important;
  width:0 !important;
  height:0 !important;
  overflow:hidden !important;
  color:transparent !important;
}
.st-key-open_settings button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z'/%3E%3Ccircle cx='12' cy='12' r='3'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z'/%3E%3Ccircle cx='12' cy='12' r='3'/%3E%3C/svg%3E");
}
.st-key-theme_toggle_dark button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 3a6 6 0 0 0 9 9a9 9 0 1 1-9-9Z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 3a6 6 0 0 0 9 9a9 9 0 1 1-9-9Z'/%3E%3C/svg%3E");
}
.st-key-theme_toggle_light button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='4'/%3E%3Cpath d='M12 2v2'/%3E%3Cpath d='M12 20v2'/%3E%3Cpath d='m4.93 4.93 1.41 1.41'/%3E%3Cpath d='m17.66 17.66 1.41 1.41'/%3E%3Cpath d='M2 12h2'/%3E%3Cpath d='M20 12h2'/%3E%3Cpath d='m6.34 17.66-1.41 1.41'/%3E%3Cpath d='m19.07 4.93-1.41 1.41'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='4'/%3E%3Cpath d='M12 2v2'/%3E%3Cpath d='M12 20v2'/%3E%3Cpath d='m4.93 4.93 1.41 1.41'/%3E%3Cpath d='m17.66 17.66 1.41 1.41'/%3E%3Cpath d='M2 12h2'/%3E%3Cpath d='M20 12h2'/%3E%3Cpath d='m6.34 17.66-1.41 1.41'/%3E%3Cpath d='m19.07 4.93-1.41 1.41'/%3E%3C/svg%3E");
}
.st-key-open_ai_from_nav button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 3h10v2h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-1.5L14 21v-4H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3V3Zm0 4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9v2l1.8-2H19a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H7Zm2 3h2v2H9v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 3h10v2h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-1.5L14 21v-4H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3V3Zm0 4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9v2l1.8-2H19a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H7Zm2 3h2v2H9v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E");
}
/* 顶栏右侧视觉重排：主题切换 → 搜索 → 添加，主题贴近搜索左侧（仅改 flex order，不动 DOM/窄屏规则） */
.st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-theme_toggle_dark),
.st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-theme_toggle_light) {
  order:2;
  flex:0 0 auto !important;
  width:44px !important;
  min-width:44px !important;
  justify-content:center;
}
.st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-open_settings) {
  order:1;
  flex:0 0 auto !important;
  width:44px !important;
  min-width:44px !important;
  justify-content:center;
}
/* 齿轮与主题两个图标之间收紧间距 */
.st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-theme_toggle_dark),
.st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-theme_toggle_light) {
  margin-left:-6px;
}
.st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has([data-testid="stSelectbox"]) {
  order:3;
}
.st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-search_add_ticker) {
  order:4;
}
.t24-set-sec {
  font-size:12px;
  font-weight:750;
  letter-spacing:.04em;
  color:var(--t24-muted);
  text-transform:uppercase;
  margin:6px 0 10px;
  padding-bottom:6px;
  border-bottom:1px solid var(--t24-border);
}
@media (max-width: 900px) {
  .st-key-t24_topnav { padding:10px 16px; }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] { gap:6px; }
  .t24-logo { font-size:22px; }
  .st-key-t24_topnav .stButton button { padding-left:6px !important; padding-right:6px !important; }
  .st-key-t24_topnav [data-testid="stSelectbox"] span,
  .st-key-t24_topnav [data-testid="stSelectbox"] input { font-size:13px !important; }
}
@media (max-width: 760px) {
  .st-key-t24_topnav { margin-left:-10px; margin-right:-10px; width:calc(100% + 20px); max-width:none; }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] {
    display:flex !important;
    flex-flow:row wrap !important;
    gap:8px !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    width:auto !important;
    min-width:0 !important;
    flex:0 0 auto !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1) {
    order:1;
    flex:1 1 calc(100% - 160px) !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2),
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3),
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(4) {
    order:2;
    flex:1 1 calc(33.333% - 8px) !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(5) {
    display:none !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(6) {
    order:3;
    flex:1 1 100% !important;
    width:100% !important;
    display:flex !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"]:has(.st-key-search_add_ticker) > [data-testid="stColumn"]:nth-child(6) {
    flex:1 1 calc(100% - 88px) !important;
    width:auto !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(7):has(.st-key-search_add_ticker) {
    display:flex !important;
    order:3;
    flex:0 0 80px !important;
    width:80px !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(7):not(:has(.st-key-search_add_ticker)) {
    display:none !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(8),
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(9) {
    order:1;
    flex:0 0 44px !important;
    width:44px !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) button,
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3) button,
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(4) button {
    width:100% !important;
  }
  .t24-icon-button { margin-left:0; }
}
.t24-logo {
  font-size:25px;
  font-weight:800;
  color:var(--t24-primary);
  letter-spacing:0;
  white-space:nowrap;
  height:44px;
  line-height:1;
  display:flex;
  align-items:center;
}
.t24-topbar-spacer { flex:1; }
.t24-icon-button {
  width:44px;
  height:44px;
  border:1px solid var(--t24-border);
  background:var(--t24-surface);
  color:var(--t24-muted);
  display:inline-flex;
  align-items:center;
  justify-content:center;
  margin-left:0;
  border-radius:var(--t24-radius-control);
  box-sizing:border-box;
}
.t24-page-head {
  display:flex;
  align-items:flex-end;
  justify-content:space-between;
  gap:16px;
  margin:4px 0 22px;
}
.st-key-t24_analysis_filter {
  margin:-2px 0 10px;
}
.st-key-t24_analysis_filter [data-testid="stHorizontalBlock"] {
  align-items:center;
}
.t24-filter-copy {
  color:var(--t24-muted);
  font-size:13px;
  line-height:1.35;
}
.st-key-t24_analysis_filter [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
  height:42px !important;
  min-height:42px !important;
  background:var(--t24-surface) !important;
  border-color:var(--t24-border-strong) !important;
  box-sizing:border-box !important;
  border-radius:var(--t24-radius-control) !important;
}
.st-key-t24_analysis_filter [data-testid="stSelectbox"] span,
.st-key-t24_analysis_filter [data-testid="stSelectbox"] input {
  font-size:13px !important;
}
.st-key-watchlist_edit_actions,
.st-key-watchlist_delete_actions {
  padding-top:18px;
}
.st-key-watchlist_edit_actions [data-testid="stVerticalBlock"],
.st-key-watchlist_delete_actions [data-testid="stVerticalBlock"] {
  gap:10px !important;
}
.st-key-watchlist_edit_actions .stButton button,
.st-key-watchlist_delete_actions .stButton button {
  height:52px !important;
  min-height:52px !important;
  font-size:13px !important;
}
.st-key-watchlist_delete_actions .stButton button {
  color:var(--t24-danger) !important;
}
.t24-headline {
  font-size:30px;
  font-weight:750;
  line-height:1.12;
  color:var(--t24-text);
  letter-spacing:0;
}
.t24-subhead {
  color:var(--t24-muted);
  font-size:15px;
  line-height:1.45;
  margin-top:4px;
}
.t24-bento {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-radius:8px;
  box-shadow:var(--t24-shadow);
  padding:20px;
}
.t24-bento-title {
  display:flex;
  align-items:center;
  gap:10px;
  color:var(--t24-text);
  font-size:18px;
  font-weight:750;
  line-height:1.2;
  margin-bottom:14px;
}
.t24-card-meta,
.t24-data {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
}
.t24-watch-card {
  min-height:252px;
  position:relative;
  overflow:hidden;
}
.t24-watch-card::before {
  content:"";
  position:absolute;
  left:0;
  top:0;
  bottom:0;
  width:4px;
  background:var(--accent, var(--t24-muted));
}
.t24-sparkline-shell {
  height:78px;
  margin:18px 0 8px;
  border-bottom:1px solid var(--t24-border);
}
.t24-overview-side-gap {
  height:18px;
}
.t24-card-footer {
  display:grid;
  grid-template-columns:minmax(0, 1fr) auto;
  align-items:end;
  gap:12px;
  margin-top:12px;
  color:var(--t24-muted);
  font-size:13px;
}
.t24-card-footer-main {
  display:flex;
  align-items:center;
  gap:8px;
  min-width:0;
  flex-wrap:wrap;
}
.t24-card-footer .caps {
  color:var(--t24-muted);
}
.t24-progress {
  height:7px;
  border-radius:999px;
  background:var(--t24-surface-high);
  overflow:hidden;
  margin:8px 0 16px;
}
.t24-progress > span {
  display:block;
  height:100%;
  width:var(--value, 0%);
  background:var(--t24-primary);
}
.t24-stage-row,
.t24-alert-row,
.t24-decision-row,
.t24-edit-row {
  display:grid;
  grid-template-columns:auto minmax(0, 1fr) auto;
  align-items:center;
  gap:12px;
  padding:12px 0;
  border-top:1px solid var(--t24-border);
}
.t24-stage-row > span:nth-child(2),
.t24-stage-row > span:nth-child(3) {
  white-space:nowrap;
  font-size:13px;
}
.t24-alert-row {
  grid-template-columns:18px minmax(0, 1fr) auto;
  font-size:13px;
}
.t24-alert-link {
  text-decoration:none !important;
  color:var(--t24-text);
  cursor:pointer;
  border-radius:8px;
  transition:background .12s ease;
}
.t24-alert-link:hover { background:var(--t24-surface-high); }
.t24-alert-link strong { color:var(--t24-text); }
.t24-stage-row:first-of-type,
.t24-alert-row:first-of-type,
.t24-decision-row:first-of-type,
.t24-edit-row:first-of-type { border-top:0; }
.t24-dot {
  width:9px;
  height:9px;
  border-radius:99px;
  background:var(--dot, var(--t24-muted));
  box-shadow:0 0 0 4px color-mix(in srgb, var(--dot, var(--t24-muted)) 13%, transparent);
}
.t24-dot.running { animation:t24pulse 1.6s ease-in-out infinite; }
@keyframes t24pulse {
  0%, 100% { transform:scale(1); opacity:1; }
  50% { transform:scale(.72); opacity:.52; }
}
.t24-analysis-shell {
  display:grid;
  grid-template-columns:220px minmax(0, 1fr);
  gap:24px;
  align-items:start;
}
[data-testid="column"]:has(.t24-pipeline) {
  align-self:stretch;
}
[data-testid="column"]:has(.t24-pipeline) > div {
  height:100%;
}
.t24-pipeline {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-radius:8px;
  overflow:hidden;
  position:sticky;
  top:16px;
}
.t24-pipeline-head {
  padding:14px 14px 12px;
  border-bottom:1px solid var(--t24-border);
}
.t24-pipeline-item,
.t24-pipeline-item:link,
.t24-pipeline-item:visited {
  display:flex;
  align-items:center;
  gap:9px;
  padding:10px 14px;
  min-height:40px;
  border-left:3px solid transparent;
  color:var(--t24-muted) !important;
  font-size:13px;
  text-decoration:none !important;
  cursor:pointer;
  transition:background-color 180ms ease, color 180ms ease, border-color 180ms ease;
}
.t24-pipeline-item *,
.t24-pipeline-item:link *,
.t24-pipeline-item:visited * {
  color:currentColor !important;
}
.t24-pipeline-item span {
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.t24-pipeline-item:hover {
  color:var(--t24-text) !important;
  background:var(--t24-surface-high);
}
.t24-pipeline-item.active,
.t24-pipeline-item.is-active {
  color:var(--t24-primary) !important;
  background:var(--t24-surface-high);
  border-left-color:var(--t24-primary);
}
.t24-live-run {
  border:1px solid color-mix(in srgb, var(--t24-primary) 36%, var(--t24-border));
  border-radius:var(--t24-radius-card);
  background:color-mix(in srgb, var(--t24-primary) 7%, var(--t24-surface));
  padding:14px 16px;
  margin:0 0 16px;
}
.t24-live-run-head {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  color:var(--t24-text);
  font-weight:750;
}
.t24-live-run-sub {
  margin-top:6px;
  color:var(--t24-muted);
  font-size:13px;
}
.t24-live-run-steps {
  display:grid;
  grid-template-columns:repeat(4, minmax(0, 1fr));
  gap:8px;
  margin-top:12px;
}
.t24-live-step {
  min-height:34px;
  border:1px solid var(--t24-border);
  border-radius:999px;
  display:flex;
  align-items:center;
  justify-content:center;
  color:var(--t24-muted);
  font-size:12px;
  font-weight:650;
}
.t24-live-step.done {
  border-color:color-mix(in srgb, var(--t24-primary) 38%, var(--t24-border));
  color:var(--t24-primary);
  background:color-mix(in srgb, var(--t24-primary) 8%, transparent);
}
.t24-live-step.active {
  border-color:var(--t24-primary);
  color:var(--t24-primary);
  box-shadow:0 0 0 3px color-mix(in srgb, var(--t24-primary) 12%, transparent);
}
.t24-stage-grid {
  display:flex;
  flex-wrap:wrap;
  gap:6px;
  margin-top:12px;
}
.t24-stage-chip {
  border:1px solid var(--t24-border);
  border-radius:999px;
  padding:4px 11px;
  font-size:12px;
  font-weight:600;
  color:var(--t24-subtle);
  background:var(--t24-surface);
  white-space:nowrap;
}
.t24-stage-chip.done {
  border-color:color-mix(in srgb, var(--t24-primary) 38%, var(--t24-border));
  color:var(--t24-primary);
  background:color-mix(in srgb, var(--t24-primary) 9%, transparent);
}
.t24-stage-chip.active {
  border-color:var(--t24-primary);
  color:var(--t24-primary);
  background:color-mix(in srgb, var(--t24-primary) 5%, transparent);
  animation:t24pulse 1.6s ease-in-out infinite;
}
/* 今日分析面板 */
.st-key-t24_today_panel {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-radius:12px;
  padding:16px 18px;
  margin:16px 0 4px;
}
.t24-today-row { display:flex; align-items:center; gap:10px; padding:6px 0; }
.t24-today-name { font-weight:650; color:var(--t24-text); }
.t24-today-badge {
  margin-left:auto;
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px; padding:2px 10px; border-radius:999px;
  border:1px solid var(--t24-border); color:var(--t24-subtle);
}
.t24-today-badge.done {
  color:var(--t24-primary);
  border-color:color-mix(in srgb, var(--t24-primary) 38%, var(--t24-border));
  background:color-mix(in srgb, var(--t24-primary) 9%, transparent);
}
.t24-today-badge.running { color:var(--t24-primary); border-color:var(--t24-primary); }
.t24-today-badge.failed {
  color:var(--t24-danger);
  border-color:color-mix(in srgb, var(--t24-danger) 40%, var(--t24-border));
}
/* 每行的运行状态图标按钮（随状态变形） */
[class*="st-key-runstate-"] button {
  width:34px !important; min-width:34px !important;
  height:34px !important; min-height:34px !important;
  padding:0 !important; border-radius:9px !important;
  border:1px solid var(--t24-border) !important;
  background:var(--t24-surface) !important;
  box-shadow:none !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  color:var(--t24-primary) !important; font-size:0 !important;
}
[class*="st-key-runstate-"] button:disabled { opacity:1 !important; cursor:default !important; }
[class*="st-key-runstate-"] button * {
  font-size:0 !important; width:0 !important; height:0 !important;
  overflow:hidden !important; color:transparent !important;
}
[class*="st-key-runstate-"] button::before {
  content:""; display:block; width:17px; height:17px; background:currentColor;
  -webkit-mask-repeat:no-repeat; -webkit-mask-position:center; -webkit-mask-size:17px 17px;
  mask-repeat:no-repeat; mask-position:center; mask-size:17px 17px;
}
[class*="st-key-runstate-idle-"] button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M8 5v14l11-7z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M8 5v14l11-7z'/%3E%3C/svg%3E");
}
[class*="st-key-runstate-done-"] button {
  color:var(--t24-primary) !important;
  border-color:color-mix(in srgb, var(--t24-primary) 38%, var(--t24-border)) !important;
  background:color-mix(in srgb, var(--t24-primary) 9%, transparent) !important;
}
[class*="st-key-runstate-done-"] button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z'/%3E%3C/svg%3E");
}
[class*="st-key-runstate-running-"] button::before,
[class*="st-key-runstate-failed-"] button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 5V2L8 6l4 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 5V2L8 6l4 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z'/%3E%3C/svg%3E");
}
[class*="st-key-runstate-running-"] button {
  color:var(--t24-primary) !important; border-color:var(--t24-primary) !important;
}
[class*="st-key-runstate-running-"] button::before { animation:t24spin 1.1s linear infinite; }
[class*="st-key-runstate-queued-"] button { color:var(--t24-muted) !important; }
[class*="st-key-runstate-queued-"] button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2a10 10 0 1 0 0 20a10 10 0 0 0 0-20zm1 5h-2v6l5 3l1-1.7l-4-2.4z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2a10 10 0 1 0 0 20a10 10 0 0 0 0-20zm1 5h-2v6l5 3l1-1.7l-4-2.4z'/%3E%3C/svg%3E");
}
[class*="st-key-runstate-failed-"] button {
  color:var(--t24-danger) !important;
  border-color:color-mix(in srgb, var(--t24-danger) 40%, var(--t24-border)) !important;
}
@keyframes t24spin { to { transform:rotate(360deg); } }
/* 关注页统一列表 */
.t24-wl-summary { font-size:14px; color:var(--t24-text); }
.t24-wl-summary b { color:var(--t24-primary); font-weight:750; }
.st-key-t24_watchlist {
  border:0.5px solid var(--t24-border);
  border-radius:12px;
  background:var(--t24-surface);
  padding:12px 20px;
  margin-top:12px;
}
.st-key-t24_watchlist [data-testid="stMarkdownContainer"]:has(.t24-wl-row) {
  transform:translateY(-8px);
}
.t24-wl-row {
  display:grid;
  grid-template-columns:120px 96px minmax(0,1fr) auto;
  align-items:center;
  gap:16px;
  min-width:0;
}
.t24-wl-name { font-weight:650; color:var(--t24-text); text-align:left; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.t24-wl-code { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace !important; font-size:12px; color:var(--t24-muted); text-align:left; white-space:nowrap; }
.t24-wl-th { font-size:12px; color:var(--t24-muted); text-align:left; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.t24-wl-row .t24-today-badge { justify-self:end; }
[class*="st-key-wl_drawer_"] {
  background:var(--t24-surface-high);
  border:0.5px solid var(--t24-border);
  border-radius:10px;
  padding:12px 14px;
  margin:2px 0 10px;
}
[class*="st-key-wledit-"] button,
[class*="st-key-wldel-"] button {
  width:34px !important; min-width:34px !important;
  height:34px !important; min-height:34px !important;
  padding:0 !important; border-radius:9px !important;
  border:1px solid var(--t24-border) !important;
  background:var(--t24-surface) !important; box-shadow:none !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  color:var(--t24-muted) !important; font-size:0 !important;
}
[class*="st-key-wldel-"] button { color:var(--t24-danger) !important; }
[class*="st-key-wledit-"] button *,
[class*="st-key-wldel-"] button * {
  font-size:0 !important; width:0 !important; height:0 !important;
  overflow:hidden !important; color:transparent !important;
}
[class*="st-key-wledit-"] button::before,
[class*="st-key-wldel-"] button::before {
  content:""; display:block; width:16px; height:16px; background:currentColor;
  -webkit-mask-repeat:no-repeat; -webkit-mask-position:center; -webkit-mask-size:16px 16px;
  mask-repeat:no-repeat; mask-position:center; mask-size:16px 16px;
}
[class*="st-key-wledit-"] button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83l3.75 3.75l1.83-1.83z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83l3.75 3.75l1.83-1.83z'/%3E%3C/svg%3E");
}
[class*="st-key-wldel-"] button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z'/%3E%3C/svg%3E");
}
.t24-section-anchor,
.t24-report-anchor {
  scroll-margin-top:132px;
}
.t24-ticker-hero {
  display:grid;
  grid-template-columns:1.2fr 1fr auto;
  gap:22px;
  align-items:center;
  background:var(--t24-surface);
  border:1px solid var(--t24-border-strong);
  border-radius:8px;
  padding:22px 24px;
  margin-bottom:24px;
}
.t24-ticker-code {
  display:inline-flex;
  align-items:center;
  margin-left:8px;
  padding:3px 6px;
  border-radius:8px;
  background:var(--t24-surface-high);
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px;
  color:var(--t24-muted);
}
.t24-hero-price {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:34px;
  line-height:1;
  font-weight:800;
  color:var(--t24-text);
  white-space:nowrap;
}
.stTabs [data-baseweb="tab-highlight"] { background-color:var(--t24-primary) !important; }
.stTabs [role="tab"] { color:var(--t24-muted) !important; }
.stTabs [role="tab"] * { color:inherit !important; }
.stTabs [role="tab"][aria-selected="true"] { color:var(--t24-primary) !important; }
.t24-report-module {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-left:3px solid var(--accent, var(--t24-primary));
  border-radius:var(--t24-radius-card);
  margin:10px 0;
  box-shadow:var(--t24-shadow);
  overflow:hidden;
}
.t24-report-module summary {
  list-style:none;
  cursor:pointer;
  min-height:54px;
  display:grid;
  grid-template-columns:minmax(0, 1fr) auto auto;
  align-items:center;
  gap:12px;
  padding:0 14px 0 22px;
  transition:background 180ms ease;
}
.t24-report-module summary::-webkit-details-marker {
  display:none;
}
.t24-report-module summary:hover,
.t24-report-module[open] summary {
  background:var(--t24-surface-high);
}
.t24-report-module summary:focus {
  outline:none;
}
.t24-report-module summary:focus-visible {
  outline:2px solid color-mix(in srgb, var(--t24-primary) 34%, transparent);
  outline-offset:-2px;
}
.t24-report-heading {
  display:flex;
  align-items:center;
  gap:10px;
  min-width:0;
  color:var(--t24-text);
  font-size:15px;
  font-weight:750;
}
.t24-report-heading .t24-icon {
  flex:0 0 auto;
}
.t24-report-heading span {
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.t24-report-meta {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px;
  font-weight:700;
  color:var(--t24-muted);
  text-transform:uppercase;
  white-space:nowrap;
}
.t24-report-chevron {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  color:var(--t24-muted);
  transition:transform 180ms ease, color 180ms ease;
}
.t24-report-module[open] .t24-report-chevron {
  transform:rotate(90deg);
  color:var(--t24-primary);
}
.t24-report-body {
  border-top:1px solid var(--t24-border);
  padding:14px;
}
.t24-report-text {
  color:var(--t24-text);
  background:var(--t24-surface-low);
  border:1px solid var(--t24-border);
  border-radius:10px;
  padding:14px 16px;
  font-size:14px;
  line-height:1.65;
  white-space:normal;
  overflow-wrap:anywhere;
  overflow-x:auto;
  margin:0;
  text-decoration:none !important;
}
.t24-report-text,
.t24-report-text * {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  text-decoration:none !important;
}
.t24-report-text > *:first-child {
  margin-top:0 !important;
}
.t24-report-text > *:last-child {
  margin-bottom:0 !important;
}
.t24-report-text h1,
.t24-report-text h2,
.t24-report-text h3,
.t24-report-text h4 {
  color:var(--t24-text);
  font-weight:750;
  line-height:1.25;
  margin:16px 0 8px;
}
.t24-report-text h1 { font-size:20px; }
.t24-report-text h2 { font-size:18px; }
.t24-report-text h3 { font-size:16px; }
.t24-report-text h4 { font-size:15px; }
.t24-report-text p,
.t24-report-text ul,
.t24-report-text ol,
.t24-report-text blockquote {
  margin:0 0 12px;
}
.t24-report-text ul,
.t24-report-text ol {
  padding-left:22px;
}
.t24-report-text table {
  width:100%;
  border-collapse:collapse;
  margin:12px 0 16px;
  font-size:13px;
  min-width:520px;
}
.t24-report-text th,
.t24-report-text td {
  border:1px solid var(--t24-border);
  padding:9px 10px;
  text-align:left;
  vertical-align:top;
}
.t24-report-text th {
  background:var(--t24-surface-high);
  font-weight:750;
}
.t24-report-text code,
.t24-report-text pre {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  background:var(--t24-surface-high);
  border:1px solid var(--t24-border);
  border-radius:8px;
}
.t24-report-text code {
  padding:1px 5px;
}
.t24-report-text pre {
  padding:12px;
  overflow:auto;
}
.t24-report-text blockquote {
  border-left:3px solid var(--t24-primary);
  padding-left:12px;
  color:var(--t24-muted);
}
.t24-decision-history {
  min-height:0;
  margin:10px 0 20px;
  padding:16px 18px;
}
.t24-decision-history-head {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-bottom:8px;
}
.t24-decision-history-title {
  display:flex;
  align-items:center;
  gap:10px;
  color:var(--t24-text);
  font-size:18px;
  font-weight:750;
  line-height:1.2;
}
.t24-decision-history-meta {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px;
  font-weight:700;
  color:var(--t24-muted);
  text-transform:uppercase;
  white-space:nowrap;
}
.st-key-t24_decision_actions {
  margin:0 0 10px;
}
.st-key-t24_decision_actions [data-testid="stHorizontalBlock"] {
  align-items:center;
}
.st-key-t24_decision_actions .stButton button {
  min-height:38px !important;
  height:38px !important;
  min-width:64px !important;
  padding:0 14px !important;
  font-size:13px !important;
  white-space:nowrap !important;
}
.st-key-t24_decision_actions .stButton button p {
  white-space:nowrap !important;
}
.st-key-t24_decision_select_card {
  border:1px solid var(--t24-border);
  border-radius:var(--t24-radius-card);
  background:var(--t24-surface);
  box-shadow:var(--t24-shadow);
  padding:16px 18px;
  margin:10px 0 20px;
}
.st-key-t24_decision_select_card [data-testid="stHorizontalBlock"] {
  border-top:1px solid var(--t24-border);
  align-items:center;
  min-height:56px;
}
.st-key-t24_decision_select_card [data-testid="stHorizontalBlock"]:first-of-type {
  border-top:0;
}
.st-key-t24_decision_select_card [data-testid="stCheckbox"] {
  display:flex;
  justify-content:center;
  margin:0 !important;
}
.st-key-t24_decision_select_card [data-testid="stCheckbox"] label {
  align-items:center;
  justify-content:center;
  min-height:44px;
  padding:0 !important;
}
.st-key-t24_decision_select_card [data-testid="stCheckbox"] input {
  accent-color:var(--t24-primary);
}
.t24-decision-select-summary {
  border-radius:10px;
  margin:0 -8px 0 0;
  padding:0 8px;
  background:var(--t24-surface-high);
}
.t24-decision-select-summary.is-selected {
  outline:1px solid color-mix(in srgb, var(--t24-primary) 42%, transparent);
  background:color-mix(in srgb, var(--t24-primary) 8%, var(--t24-surface-high));
}
.t24-decision-select-summary .t24-decision-row {
  display:grid;
  grid-template-columns:minmax(0, 1fr) auto auto;
  align-items:center;
  gap:14px;
  min-height:56px;
  padding:10px 0;
}
.t24-delete-preview {
  border:1px solid var(--t24-border);
  border-radius:10px;
  background:var(--t24-surface-low);
  padding:10px 12px;
  margin:10px 0;
}
.t24-delete-preview-row {
  display:flex;
  justify-content:space-between;
  gap:12px;
  padding:6px 0;
  border-bottom:1px solid var(--t24-border);
  color:var(--t24-text);
}
.t24-delete-preview-row:last-child {
  border-bottom:0;
}
.t24-delete-preview-row span:last-child {
  color:var(--t24-muted);
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:12px;
}
.t24-decision-list {
  border-top:1px solid var(--t24-border);
}
.t24-decision-item {
  border-bottom:1px solid var(--t24-border);
}
.t24-decision-item:last-child {
  border-bottom:0;
}
.t24-decision-item > summary {
  list-style:none;
  cursor:pointer;
  border-radius:10px;
  margin:0 -8px;
  padding:0 8px;
  transition:background 180ms ease, color 180ms ease;
}
.t24-decision-item > summary::-webkit-details-marker {
  display:none;
}
.t24-decision-item > summary:hover {
  background:var(--t24-surface-high);
}
.t24-decision-item > summary:focus {
  outline:none;
}
.t24-decision-item > summary:focus-visible {
  outline:2px solid color-mix(in srgb, var(--t24-primary) 34%, transparent);
  outline-offset:2px;
}
.t24-decision-item[open] > summary {
  background:var(--t24-surface-high);
}
.t24-decision-item .t24-decision-row {
  display:grid;
  grid-template-columns:minmax(0, 1fr) auto auto;
  align-items:center;
  gap:14px;
  min-height:56px;
  padding:10px 0;
  border-top:0;
}
.t24-decision-main {
  display:flex;
  align-items:center;
  gap:12px;
  min-width:0;
}
.t24-decision-dot {
  width:9px;
  height:9px;
  border-radius:99px;
  background:var(--decision, var(--t24-muted));
  box-shadow:0 0 0 4px color-mix(in srgb, var(--decision, var(--t24-muted)) 14%, transparent);
  flex:0 0 auto;
}
.t24-decision-copy {
  min-width:0;
}
.t24-decision-date {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:13px;
  font-weight:650;
  color:var(--t24-text);
  line-height:1.25;
}
.t24-decision-sub {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  margin-top:3px;
  font-size:11px;
  color:var(--t24-muted);
  line-height:1.25;
}
/* 失败记录的「重跑」chip：可点击，主色描边 */
a.t24-rerun-chip, a.t24-rerun-chip:visited {
  color:var(--t24-primary) !important;
  border-color:color-mix(in srgb, var(--t24-primary) 60%, transparent) !important;
  background:color-mix(in srgb, var(--t24-primary) 10%, transparent);
  text-decoration:none !important;
  cursor:pointer;
}
a.t24-rerun-chip:hover {
  background:color-mix(in srgb, var(--t24-primary) 18%, transparent);
}
.t24-decision-chip {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-height:26px;
  padding:0 10px;
  border:1px solid color-mix(in srgb, var(--decision, var(--t24-muted)) 72%, transparent);
  border-radius:999px;
  background:color-mix(in srgb, var(--decision, var(--t24-muted)) 12%, transparent);
  color:var(--decision, var(--t24-muted));
  font-size:12px;
  font-weight:700;
  white-space:nowrap;
}
.t24-decision-chevron {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  color:var(--t24-muted);
  transition:transform 180ms ease, color 180ms ease;
}
.t24-decision-item[open] .t24-decision-chevron {
  transform:rotate(90deg);
  color:var(--decision, var(--t24-primary));
}
.t24-report-drawer {
  padding:8px 0 28px;
}
@media (min-width: 640px) {
  .t24-pipeline {
    position:fixed;
    top:164px;
    left:max(22px, calc(var(--t24-gutter) + 22px));
    width:clamp(138px, 19vw, 185px);
    max-height:calc(100vh - 190px);
    overflow:auto;
    z-index:20;
  }
}
.t24-table-card {
  background:var(--t24-surface);
  border:1px solid var(--t24-border);
  border-radius:8px;
  padding:14px 16px;
}
.t24-brand {
  display:flex;
  align-items:center;
  gap:10px;
  margin:4px 0 2px;
}
.t24-theme-switch { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:12px 0 10px; }
/* 消息气泡：与站内卡片同语言（白卡+细边+12px 圆角），用户消息用主题色左侧竖条区分，
   不再整块刷色——和总览/详情卡片保持一致的克制感 */
.t24-ai-msg { border:1px solid var(--t24-border); border-radius:12px; padding:9px 12px;
  margin:6px 0; background:var(--t24-surface); font-size:13px; line-height:1.55;
  box-shadow:var(--t24-shadow); }
.t24-ai-msg.user { border-left:3px solid var(--t24-primary);
  background:color-mix(in srgb, var(--t24-primary) 4%, var(--t24-surface));
  width:fit-content; max-width:90%; margin-left:auto; }   /* 短消息随内容收缩、靠右 */
.t24-ai-msg.assistant { background:var(--t24-ai-assistant); border-color:var(--t24-border); }
.t24-ai-msg { overflow-wrap:anywhere; }                    /* 长串不许顶破卡片 */
.t24-ai-msg :is(ul,ol) { padding-left:1.3em !important; margin:.3em 0 .4em !important; }
.t24-ai-msg pre { white-space:pre-wrap; word-break:break-word; max-width:100%; }
.t24-ai-msg :is(p,li) { font-size:13px !important; line-height:1.55 !important; margin:.25em 0 !important; }
/* 回答内小标题(依据/操作建议)：站内 kicker 语言，等宽大写小字，与正文加粗拉开层级 */
.t24-ai-msg :is(h1,h2,h3,h4) {
  font-family:'Geist Mono','SFMono-Regular',Menlo,monospace !important;
  font-size:11px !important; font-weight:700 !important;
  text-transform:uppercase; letter-spacing:.05em;
  color:var(--t24-muted) !important; margin:.75em 0 .3em !important; }
.t24-ai-msg table { font-size:12px !important; display:block; max-width:100%; overflow-x:auto; }
/* 元信息行：角色·时间 左，复制按钮 右（悬停显现） */
.t24-ai-meta { display:flex; justify-content:space-between; align-items:center; }
.t24-ai-copy { font-family:'Geist Mono','SFMono-Regular',Menlo,monospace;
  font-size:10px; color:var(--t24-muted); background:none; border:0; padding:0 2px;
  cursor:pointer; opacity:0; transition:opacity .15s ease; }
.t24-ai-msg:hover .t24-ai-copy { opacity:1; }
.t24-ai-copy:hover { color:var(--t24-primary); }
/* 头部行钉在面板顶部、输入框钉在底部；消息在中间滚动。
   注意：sticky 必须挂在面板的“直接子级”包装层上——挂在更深的列容器上时，
   其父包装只有一行高，sticky 没有活动空间（HANDOVER 坑#12 的机理）。 */
.st-key-t24_ai_panel > div:has(.st-key-ai_threads_btn) {
  position:sticky; top:0; z-index:6;
  background:var(--t24-surface);
  padding:10px 0 8px;
  border-bottom:1px solid var(--t24-border);
}
.st-key-t24_ai_panel .st-key-ai_composer {
  position:sticky; bottom:-12px; z-index:6;
  background:var(--t24-surface);
  padding:8px 0 12px;
}
.t24-ai-meta { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:10px; color:var(--t24-muted); margin-bottom:3px; }
.t24-ai-title { font-size:15px; font-weight:700; color:var(--t24-text); height:30px; display:flex; align-items:center; margin:0; transform:translateY(-8px); }
.t24-ai-scope { font-size:11px; margin:4px 0 6px; }
.t24-ai-stat { border:1px solid var(--t24-border); border-radius:8px; padding:10px 12px; background:var(--t24-surface); min-height:58px; }
.t24-ai-stat-label { font-size:11px; color:var(--t24-muted); margin-bottom:5px; }
.t24-ai-stat-value { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:18px; font-weight:700; line-height:1.15; white-space:nowrap; color:var(--t24-text); }
        .st-key-t24_ai_launcher {
            position: fixed;
            right: 20px;
            bottom: 20px;
            z-index: 2147482000;
            width: 52px;
        }
        .st-key-t24_ai_launcher button {
            background:transparent !important;
            border:none !important;
            box-shadow:none !important;
            min-height: 52px;
            width: 52px;
            padding:0 !important;
            color:var(--t24-primary) !important;
            font-size:0 !important;
            display:flex !important;
            align-items:center !important;
            justify-content:center !important;
        }
        .st-key-t24_ai_launcher button:hover,
        .st-key-t24_ai_launcher button:active,
        .st-key-t24_ai_launcher button:focus,
        .st-key-t24_ai_launcher button:focus-visible {
            background:transparent !important; border:none !important;
            box-shadow:none !important; transform:none !important; outline:none !important;
        }
        .st-key-t24_ai_launcher button:hover { opacity:.72; }
        .st-key-t24_ai_launcher button::before {
            content:""; display:block; width:30px; height:30px; background:currentColor;
            -webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 3h10v2h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-1.5L14 21v-4H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3V3Zm0 4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9v2l1.8-2H19a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H7Zm2 3h2v2H9v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E") center / 30px 30px no-repeat;
            mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 3h10v2h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-1.5L14 21v-4H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3V3Zm0 4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9v2l1.8-2H19a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H7Zm2 3h2v2H9v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E") center / 30px 30px no-repeat;
        }
        .st-key-t24_ai_launcher button * {
            font-size:0 !important; line-height:0 !important; color:transparent !important;
        }
        .st-key-t24_ai_panel {
            position: fixed;
            right: 20px;
            bottom: 84px;
            width: 392px;
            height: auto;
            min-height: 180px;
            max-height: min(820px, calc(100vh - 110px));
            overflow: auto;
            z-index: 2147483000;
            background:var(--t24-surface);
            border:1px solid var(--t24-border-strong);
            border-radius:14px;
            padding:0 12px 12px;
            box-shadow: var(--t24-shadow);
        }
        .st-key-t24_ai_panel [data-testid="stVerticalBlock"] { gap: .4rem; }
        /* sticky 头部：随面板滚动始终可见 */
        .st-key-t24_ai_header {
            position: sticky; top: 0; z-index: 6;
            background: var(--t24-surface);
            margin: 0 -12px 4px;
            padding: 10px 12px 6px;
            border-bottom: 1px solid var(--t24-border);
        }
        /* 圆形 × 关闭按钮（头部最右；头部已 sticky 常驻，随时可点） */
        .st-key-ai_close button {
            border-radius: 50% !important;
            width: 30px !important; min-width: 30px !important;
            height: 30px !important; min-height: 30px !important;
            padding: 0 !important; font-size: 14px !important;
            border: none !important; box-shadow: none !important;
            background: color-mix(in srgb, currentColor 9%, transparent) !important;
            color: var(--t24-text) !important;
            display: flex !important; align-items: center !important; justify-content: center !important;
        }
        .st-key-ai_close button:hover {
            background: color-mix(in srgb, currentColor 18%, transparent) !important;
            border: none !important; box-shadow: none !important;
            transform: none !important;
        }
        /* 头部图标按钮：新对话(加号) + 选择记录(列表) */
        .st-key-ai_new_chat button, .st-key-ai_threads_btn button {
            width:30px !important; min-width:30px !important; height:30px !important; min-height:30px !important;
            padding:0 !important; font-size:0 !important;
            border:none !important; background:transparent !important; box-shadow:none !important;
            border-radius:8px !important; color:var(--t24-text-muted, var(--t24-text)) !important;
            display:flex !important; align-items:center !important; justify-content:center !important;
        }
        .st-key-ai_new_chat button:hover, .st-key-ai_threads_btn button:hover {
            background:color-mix(in srgb, currentColor 12%, transparent) !important;
            border:none !important; box-shadow:none !important;
        }
        .st-key-ai_new_chat button *, .st-key-ai_threads_btn button * {
            position: absolute !important;
            width: 1px !important;
            height: 1px !important;
            padding: 0 !important;
            margin: -1px !important;
            overflow: hidden !important;
            clip: rect(0, 0, 0, 0) !important;
            white-space: nowrap !important;
            border: 0 !important;
        }
        .st-key-ai_new_chat button::before, .st-key-ai_threads_btn button::before {
            content:""; display:block; width:17px; height:17px; background:currentColor;
            -webkit-mask-repeat:no-repeat; -webkit-mask-position:center; -webkit-mask-size:17px;
            mask-repeat:no-repeat; mask-position:center; mask-size:17px;
        }
        .st-key-ai_new_chat button::before {
            -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z'/%3E%3C/svg%3E");
            mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z'/%3E%3C/svg%3E");
        }
        .st-key-ai_threads_btn button::before {
            -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M4 6h16v2H4zM4 11h16v2H4zM4 16h16v2H4z'/%3E%3C/svg%3E");
            mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M4 6h16v2H4zM4 11h16v2H4zM4 16h16v2H4z'/%3E%3C/svg%3E");
        }
        /* 范围 chip（展开/收起上下文清单） */
        .st-key-ai_scope_chip button {
            font-size: 11px !important; min-height: 26px !important; padding: 2px 10px !important;
            width: 100% !important;
            border-radius: 999px !important;
            color: var(--t24-primary) !important;
            background: color-mix(in srgb, var(--t24-primary) 10%, transparent) !important;
            border: 1px solid color-mix(in srgb, var(--t24-primary) 30%, transparent) !important;
        }
        /* 上下文清单（内联展开） */
        .st-key-t24_ai_scope_panel {
            border: 1px solid var(--t24-border);
            border-radius: 12px;
            padding: 8px 10px;
            background: var(--t24-surface);
        }
        .st-key-t24_ai_scope_panel [data-testid="stVerticalBlock"] { gap: .15rem; }
        .t24-ai-ctx-row { font-size: 12px; color: var(--t24-muted); line-height: 1.4; }
        .t24-ai-ctx-row b { color: var(--t24-text); font-weight: 600; }
        [class*="st-key-ai_ctx_rm_"] button {
            justify-content: center !important; text-align: center !important;
            font-size: 11px !important; padding: 2px 0 !important;
            min-height: 24px !important; width: 100% !important;
            color: var(--t24-muted) !important; border: none !important; background: transparent !important;
        }
        [class*="st-key-ai_ctx_rm_"] button:hover { color: var(--t24-danger) !important; }
        /* 会话列表条目 */
        [class*="st-key-ai_thread_"] button {
            text-align: left !important; justify-content: flex-start !important;
            font-size: 12px !important; white-space: normal !important;
            padding: 8px 11px !important; line-height: 1.35 !important; border-radius: 10px !important;
        }
        [class*="st-key-ai_thread_del_"] button {
            justify-content: center !important; text-align: center !important;
            font-size: 12px !important; padding: 4px 0 !important; min-height: 30px !important;
            color: var(--t24-muted) !important; border: none !important; background: transparent !important;
        }
        [class*="st-key-ai_thread_del_"] button:hover { color: var(--t24-danger) !important; }
        /* 流式回答容器：按 assistant 气泡样式 */
        .st-key-t24_ai_streaming {
            border: 1px solid var(--t24-border); border-radius: 10px;
            padding: 8px 11px; margin: 6px 0; background: var(--t24-ai-assistant);
        }
        .st-key-t24_ai_streaming :is(p, li) {
            font-size: 13px !important; line-height: 1.55 !important; margin: .25em 0 !important;
        }
        /* 生成期间的输入框占位壳 */
        .t24-ai-composer-ghost {
            border: 1px solid var(--t24-border-strong);
            border-radius: 12px;
            padding: 15px 12px;
            color: var(--t24-subtle);
            font-size: 13px;
            background: var(--t24-surface);
        }
        /* 系统提示(上下文切换等) */
        .t24-ai-sysnote {
            align-self: center;
            text-align: center;
            color: var(--t24-muted);
            font-size: 11px;
            border: 1px solid var(--t24-border);
            background: color-mix(in srgb, var(--t24-primary) 6%, transparent);
            border-radius: 999px;
            padding: 4px 12px;
            margin: 4px auto;
            width: fit-content;
        }
        /* 消息里的引用 chips */
        .t24-ai-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }
        .t24-ai-chip {
            border: 1px solid var(--t24-border); border-radius: 999px; padding: 2px 8px;
            color: var(--t24-muted); font-size: 11px; font-weight: 600;
            font-family: 'Geist Mono', 'SFMono-Regular', Menlo, monospace;
        }
        .st-key-t24_ai_panel iframe { display: block; }
        [class*="st-key-ai_sugg_"] button {
            font-size: 12px !important; white-space: normal !important; text-align: left !important;
            padding: 6px 11px !important; min-height: 0 !important; line-height: 1.35 !important;
            border-radius: 8px !important; color: var(--t24-text-muted, var(--t24-text)) !important;
        }
        @media (max-width: 760px) {
            [data-testid="stSidebar"],
            [data-testid="stSidebar"] > div:first-child {
                width: 100% !important;
                min-width: 0 !important;
                max-width: none !important;
            }
            .t24-page-title { font-size: 26px; }
            .st-key-t24_ai_launcher { right: 12px; bottom: 14px; width: 52px; }
            /* 小屏：面板几乎全屏（盖住 launcher，用头部 ✕ 关闭） */
            .st-key-t24_ai_panel {
                left: 12px; right: 12px; width: auto;
                bottom: 12px; max-height: calc(100vh - 24px);
            }
            /* 小屏禁止面板内 columns 堆叠：头部按钮保持一行 */
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has(.st-key-ai_threads_btn) {
                flex-wrap: nowrap !important;
            }
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has(.st-key-ai_threads_btn)
                > [data-testid="stColumn"] {
                flex: 0 0 40px !important; width: 40px !important; min-width: 0 !important;
            }
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has(.st-key-ai_threads_btn)
                > [data-testid="stColumn"]:nth-child(2) {
                flex: 1 1 auto !important; width: auto !important;
            }
            /* 上下文清单行 / 会话列表行：内容 + ✕ 保持一行 */
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has([class*="st-key-ai_ctx_rm_"]),
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has([class*="st-key-ai_thread_del_"]) {
                flex-wrap: nowrap !important;
            }
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has([class*="st-key-ai_ctx_rm_"])
                > [data-testid="stColumn"]:first-child,
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has([class*="st-key-ai_thread_del_"])
                > [data-testid="stColumn"]:first-child {
                flex: 1 1 auto !important; width: auto !important; min-width: 0 !important;
            }
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has([class*="st-key-ai_ctx_rm_"])
                > [data-testid="stColumn"]:last-child,
            .st-key-t24_ai_panel [data-testid="stHorizontalBlock"]:has([class*="st-key-ai_thread_del_"])
                > [data-testid="stColumn"]:last-child {
                flex: 0 0 34px !important; width: 34px !important; min-width: 0 !important;
            }
        }
hr { border-color:var(--t24-border); }
@media (prefers-reduced-motion: reduce) {
  * { transition:none !important; animation:none !important; }
}
		</style>
		"""


def md(s: str | None) -> str:
    """markdown 语境：转义 $，避免 Streamlit 把价格 $195 当成 LaTeX 公式（吃空格、斜体、溢出）。"""
    return (s or "").replace("$", "\\$")


def html_safe(s: str | None) -> str:
    """HTML 语境：只转义 < > &，$ 原样（HTML 里不会触发 LaTeX）。"""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def terminal_report_text(s: str | None) -> str:
    """终端卡片只展示正文，不让 Markdown 控制符影响可读性。"""
    text = s or ""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = text.replace("~~", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def report_text_html(s: str | None) -> str:
    """报告正文按受控 Markdown 显示，同时避免近似价格符号触发删除线。"""
    text = s or "暂无内容"
    tilde_placeholder = "T24_TILDE_PLACEHOLDER"
    text = text.replace("~", tilde_placeholder)
    rendered = REPORT_MARKDOWN(text)
    cleaned = bleach.clean(
        rendered,
        tags=REPORT_ALLOWED_TAGS,
        attributes=REPORT_ALLOWED_ATTRS,
        protocols=["http", "https", "mailto"],
        strip=True,
    )
    return cleaned.replace(tilde_placeholder, "&#126;")


REPORT_ANCHORS = {
    "final": "final",
    "market": "technicals",
    "fundamentals": "fundamentals",
    "sentiment": "sentiment",
    "news": "news",
    "bull": "bull",
    "bear": "bear",
    "research_mgr": "research-mgr",
    "trader": "trader",
    "risk": "risk",
}

REPORT_NAV = [
    ("final", "最终决策", "terminal"),
    ("market", "技术面", "trend"),
    ("fundamentals", "基本面", "file"),
    ("sentiment", "情绪面", "ai"),
    ("news", "消息面", "news"),
    ("bull", "多方观点", "trend"),
    ("bear", "空方观点", "shield"),
    ("research_mgr", "研究经理", "user"),
    ("trader", "交易员", "terminal"),
    ("risk", "风险复核", "shield"),
]


def report_cards_html(run: Run, include_anchors: bool = False) -> str:
    reports = db.get_reports(conn, run.id or 0)
    order = ["final", "market", "fundamentals", "sentiment", "news", "bull", "bear",
             "research_mgr", "trader", "risk"]
    reports.sort(key=lambda rep: order.index(rep.section) if rep.section in order else 99)
    if not reports:
        return "<div class='t24-muted' style='padding:14px 0;'>暂无完整报告。</div>"

    html = "<div class='t24-report-drawer'>"
    for rep in reports:
        label = SEC.get(rep.section, rep.section)
        raw_report = (rep.content or "").strip()
        anchor_name = REPORT_ANCHORS.get(rep.section)
        anchor_attr = f" id='t24-report-{anchor_name}'" if include_anchors and anchor_name else ""
        card_class = "t24-report-module t24-report-anchor" if anchor_attr else "t24-report-module"
        icon_name = next((item[2] for item in REPORT_NAV if item[0] == rep.section), "terminal")
        open_attr = " open" if rep.section == "final" else ""
        html += (
            f"<details class='{card_class}' data-report-section='{html_safe(anchor_name or rep.section)}'"
            f"{anchor_attr}{open_attr}>"
            f"<summary><div class='t24-report-heading'>{icon(icon_name, 17)}"
            f"<span>{html_safe(label)}</span></div>"
            f"<span class='t24-report-meta'>Generated · {html_safe(run_age(run))}</span>"
            f"<span class='t24-report-chevron'>{icon('chevron', 14)}</span></summary>"
            f"<div class='t24-report-body'><div class='t24-report-text'>{report_text_html(raw_report)}</div></div>"
            f"</details>"
        )
    html += "</div>"
    return html


ICON_PATHS = {
    "overview": (
        "<rect x='4' y='4' width='6' height='6' rx='1'/>"
        "<rect x='14' y='4' width='6' height='6' rx='1'/>"
        "<rect x='4' y='14' width='6' height='6' rx='1'/>"
        "<rect x='14' y='14' width='6' height='6' rx='1'/>"
    ),
    "analysis": "<path d='M4 19V5'/><path d='M4 19h16'/><path d='M7 15l3-4 3 2 5-7'/>",
    "history": "<path d='M4 12a8 8 0 1 0 2.2-5.5'/><path d='M4 5v5h5'/><path d='M12 8v5l3 2'/>",
    "manage": "<path d='M5 7h8'/><path d='M17 7h2'/><path d='M5 17h2'/><path d='M11 17h8'/><rect x='13' y='5' width='4' height='4' rx='1'/><rect x='7' y='15' width='4' height='4' rx='1'/>",
    "watch": "<path d='M5 6h14'/><path d='M5 12h14'/><path d='M5 18h14'/>",
    "ai": "<rect x='4' y='5' width='16' height='14' rx='3'/><path d='M8 10h8'/><path d='M8 14h5'/><path d='M9 2v3'/><path d='M15 2v3'/>",
    "trend": "<path d='M4 17h16'/><path d='M5 15l4-4 4 2 6-7'/><path d='M16 6h3v3'/>",
    "terminal": "<path d='M5 7l4 5-4 5'/><path d='M12 17h7'/>",
    "cpu": "<rect x='7' y='7' width='10' height='10' rx='1'/><path d='M9 2v3'/><path d='M15 2v3'/><path d='M9 19v3'/><path d='M15 19v3'/><path d='M2 9h3'/><path d='M2 15h3'/><path d='M19 9h3'/><path d='M19 15h3'/>",
    "bell": "<path d='M6 17h12'/><path d='M8 17V10a4 4 0 0 1 8 0v7'/><path d='M10 20h4'/>",
    "search": "<circle cx='10' cy='10' r='5'/><path d='M14 14l5 5'/>",
    "moon": "<path d='M20 14.5A7 7 0 0 1 9.5 4 8 8 0 1 0 20 14.5z'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2v2'/><path d='M12 20v2'/><path d='M4.9 4.9l1.4 1.4'/><path d='M17.7 17.7l1.4 1.4'/><path d='M2 12h2'/><path d='M20 12h2'/><path d='M4.9 19.1l1.4-1.4'/><path d='M17.7 6.3l1.4-1.4'/>",
    "user": "<circle cx='12' cy='8' r='4'/><path d='M4 21a8 8 0 0 1 16 0'/>",
    "file": "<path d='M6 3h8l4 4v14H6z'/><path d='M14 3v5h5'/><path d='M9 13h6'/><path d='M9 17h6'/>",
    "shield": "<path d='M12 3l7 3v5c0 5-3.5 8-7 10-3.5-2-7-5-7-10V6z'/>",
    "news": "<path d='M5 5h14v14H5z'/><path d='M8 9h8'/><path d='M8 13h8'/><path d='M8 17h5'/>",
    "trash": "<path d='M5 7h14'/><path d='M9 7V4h6v3'/><path d='M8 10v9'/><path d='M16 10v9'/><path d='M6 7l1 14h10l1-14'/>",
    "edit": "<path d='M4 20h4l11-11-4-4L4 16z'/><path d='M13 7l4 4'/>",
    "send": "<path d='M3 11l18-8-8 18-2-7z'/><path d='M11 14l4-4'/>",
    "chevron": "<path d='M9 5l7 7-7 7'/>",
    "spark": "<path d='M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z'/>",
}


def icon(name: str, size: int = 18) -> str:
    path = ICON_PATHS.get(name, ICON_PATHS["spark"])
    return (
        f"<svg class='t24-icon' width='{size}' height='{size}' viewBox='0 0 24 24' "
        "fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='square' "
        f"stroke-linejoin='miter' aria-hidden='true'>{path}</svg>"
    )


def badge(dec, big=False):
    d = _norm_decision(dec)
    color, label = BADGE.get(d, ("var(--t24-muted)", "—"))
    bg = {
        "BUY": "color-mix(in srgb, var(--t24-primary) 14%, transparent)",
        "SELL": "color-mix(in srgb, var(--t24-danger) 14%, transparent)",
        "HOLD": "color-mix(in srgb, var(--t24-muted) 14%, transparent)",
    }.get(d, "color-mix(in srgb, var(--t24-muted) 12%, transparent)")
    fs = "13px" if big else "11px"
    return f"<span class='t24-badge' style='background:{bg};color:{color};font-size:{fs};'>{label}</span>"


def decision_label(decision: str | None) -> str:
    return BADGE.get(_norm_decision(decision), ("", "—"))[1]


def _plan_num(v) -> str:
    """120.0 → 120，1.75 → 1.75（去掉无意义的小数尾巴）。"""
    return f"{float(v):.10g}"


def plan_items(run) -> list[tuple[str, str, str]]:
    """从 decision_params 取该次决策的交易计划 → [(标签, 值, 颜色)]。没有则 []。"""
    if not run or run.id is None or run.status != "success":
        return []
    p = db.get_decision_params(conn, int(run.id))
    if not p:
        return []
    items = []
    lo, hi = p.get("entry_low"), p.get("entry_high")
    if lo is not None or hi is not None:
        lo = lo if lo is not None else hi
        hi = hi if hi is not None else lo
        v = _plan_num(lo) if lo == hi else f"{_plan_num(lo)}–{_plan_num(hi)}"
        zone_label = "减仓区间" if _norm_decision(run.decision) == "SELL" else "建仓区间"
        items.append((zone_label, v, "var(--t24-primary)"))
    if p.get("stop_loss") is not None:
        items.append(("止损", _plan_num(p["stop_loss"]), "var(--t24-danger)"))
    if p.get("price_target") is not None:
        items.append(("目标价", _plan_num(p["price_target"]), "var(--t24-text)"))
    ip, mp = p.get("initial_position_pct"), p.get("max_position_pct")
    if ip is not None or mp is not None:
        seg = ([f"初始 {_plan_num(ip)}%"] if ip is not None else []) + \
              ([f"上限 {_plan_num(mp)}%"] if mp is not None else [])
        items.append(("建议仓位", " · ".join(seg), "var(--t24-text)"))
    if p.get("time_horizon"):
        items.append(("时间视野", str(p["time_horizon"]), "var(--t24-text)"))
    return items


def plan_strip_html(run) -> str:
    """详情页交易计划条；无参数时返回空串。"""
    items = plan_items(run)
    if not items:
        return ""
    tiles = "".join(
        f"<div class='t24-plan-item'><div class='t24-plan-label'>{html_safe(lb)}</div>"
        f"<div class='t24-plan-value' style='color:{color};'>{html_safe(v)}</div></div>"
        for lb, v, color in items)
    return ("<div class='caps' style='margin:2px 0 6px;'>交易计划 · Trade Plan</div>"
            f"<div class='t24-plan'>{tiles}</div>")


def plan_line_html(run) -> str:
    """总览卡片里的一行式计划（只留价格三件套，避免撑爆卡片）。"""
    keep = {"建仓区间": "建仓", "减仓区间": "减仓", "止损": "止损", "目标价": "目标"}
    segs = [f"{keep[lb]} <b>{html_safe(v)}</b>"
            for lb, v, _ in plan_items(run) if lb in keep]
    return f"<div class='t24-ov-plan'>{' · '.join(segs)}</div>" if segs else ""


def fmt_num(v: float | int | None) -> str:
    if v is None:
        return "未设置"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def ticker_label(t: TickerCfg) -> str:
    return (
        f"{t.name or t.symbol} · {t.symbol} · "
        f"上限 {fmt_num(t.upper)} · 下限 {fmt_num(t.lower)} · 涨跌幅 {fmt_num(t.pct_move)}%"
    )


def status_label(status: str | None) -> str:
    return {
        "success": "完成",
        "failed": "失败",
        "running": "运行中",
        "pending": "准备中",
    }.get(status or "", status or "未知")


def run_display_no(run: Run | None) -> str:
    """用户看到的是标的内序号，数据库全局 id 只保留作内部定位。"""
    if not run or run.id is None:
        return "#—"
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE ticker=? AND id<=?",
        (run.ticker, int(run.id)),
    ).fetchone()
    return f"#{int(row['n'] or 0)}"


def run_label(run) -> str:
    d = _norm_decision(run.decision)
    decision = BADGE.get(d, ("", "—"))[1]
    return f"{run.trade_date} · {decision} · {run_display_no(run)}"


def report_text(run_id: int, section: str) -> str:
    for rep in db.get_reports(conn, run_id):
        if rep.section == section:
            return rep.content or ""
    return ""


@st.cache_resource
def _init_once():
    c = db.connect(check_same_thread=False)
    db.init_db(c)
    return True


def get_conn():
    _init_once()
    return db.connect(check_same_thread=False)


_REFRESH_MARK = DATA_DIR / ".last_price_refresh"  # 落盘：重启进程不重刷


@st.cache_resource
def _price_refresh_state() -> dict:
    """进程级共享的行情刷新状态（多标签页/多访客共用一个闸门）。
    启动时从磁盘水合，避免每次重启看板都触发一次全量刷新。
    规则：每个自然日首次进入页面时刷新一次，当天内不再刷。"""
    try:
        raw = json.loads(_REFRESH_MARK.read_text())
        return {"date": raw["date"], "key": tuple(raw["key"])}
    except Exception:  # noqa: BLE001  首次运行/旧格式/文件损坏 → 视为今天还没刷过
        return {"date": None, "key": None}


def _save_refresh_mark(state: dict) -> None:
    try:
        _REFRESH_MARK.write_text(json.dumps(
            {"date": state["date"], "key": list(state["key"] or [])}))
    except Exception:  # noqa: BLE001  写盘失败不影响功能（只是重启后会多刷一次）
        pass


def _loader_html(symbols: tuple[str, ...]) -> str:
    """全屏居中 loading：毛玻璃蒙层 + K线柱跳动动画。颜色走 --t24-* 主题变量。"""
    tape = " · ".join(symbols[:6]) + (" …" if len(symbols) > 6 else "")
    return f"""
<style>
.t24-quote-loader{{position:fixed;inset:0;z-index:999999;display:flex;align-items:center;
  justify-content:center;background:color-mix(in srgb, var(--t24-bg) 74%, transparent);
  backdrop-filter:blur(10px) saturate(1.15);-webkit-backdrop-filter:blur(10px) saturate(1.15);
  animation:t24qlFade .35s ease}}
.t24-ql-card{{display:flex;flex-direction:column;align-items:center;gap:16px;
  padding:44px 64px;background:var(--t24-surface);border:1px solid var(--t24-border);
  border-radius:20px;box-shadow:var(--t24-shadow), 0 28px 64px -20px rgba(0,0,0,.22)}}
.t24-ql-candles{{display:flex;align-items:center;gap:11px;height:72px}}
.t24-ql-candles i{{position:relative;display:block;width:13px;height:52px;border-radius:3.5px;
  transform-origin:center;animation:t24qlBar 1.15s ease-in-out infinite}}
.t24-ql-candles i::before{{content:"";position:absolute;left:50%;top:-11px;bottom:-11px;
  width:2px;transform:translateX(-50%);background:inherit;opacity:.5;border-radius:1px}}
.t24-ql-candles i:nth-child(1){{background:#16A34A;animation-delay:0s;height:38px}}
.t24-ql-candles i:nth-child(2){{background:#DC2626;animation-delay:.14s;height:56px}}
.t24-ql-candles i:nth-child(3){{background:var(--t24-primary);animation-delay:.28s;height:44px}}
.t24-ql-candles i:nth-child(4){{background:#16A34A;animation-delay:.42s;height:60px}}
.t24-ql-candles i:nth-child(5){{background:#DC2626;animation-delay:.56s;height:40px}}
.t24-ql-candles i:nth-child(6){{background:#16A34A;animation-delay:.70s;height:52px}}
@keyframes t24qlBar{{0%,100%{{transform:scaleY(.5)}}50%{{transform:scaleY(1.12)}}}}
@keyframes t24qlFade{{from{{opacity:0}}to{{opacity:1}}}}
.t24-ql-title{{font-size:21px;font-weight:600;color:var(--t24-text);letter-spacing:.5px;
  display:flex;align-items:baseline;gap:2px}}
.t24-ql-title u{{display:inline-flex;gap:3px;text-decoration:none;padding-left:2px}}
.t24-ql-title u b{{width:4px;height:4px;border-radius:50%;background:var(--t24-primary);
  animation:t24qlDot 1.2s ease-in-out infinite}}
.t24-ql-title u b:nth-child(2){{animation-delay:.18s}}
.t24-ql-title u b:nth-child(3){{animation-delay:.36s}}
@keyframes t24qlDot{{0%,100%{{opacity:.25;transform:translateY(0)}}
  50%{{opacity:1;transform:translateY(-4px)}}}}
.t24-ql-sub{{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:11px;
  letter-spacing:1.6px;text-transform:uppercase;color:var(--t24-muted)}}
.t24-ql-tape{{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:12px;
  color:var(--t24-subtle);letter-spacing:.6px}}
@media (prefers-reduced-motion: reduce){{
  .t24-ql-candles i,.t24-ql-title u b{{animation:none}}}}
</style>
<div class="t24-quote-loader"><div class="t24-ql-card">
  <div class="t24-ql-candles"><i></i><i></i><i></i><i></i><i></i><i></i></div>
  <div class="t24-ql-title">正在刷新行情<u><b></b><b></b><b></b></u></div>
  <div class="t24-ql-sub">Pulling live quotes · MA · RSI · MACD</div>
  <div class="t24-ql-tape">{html_safe(tape)}</div>
</div></div>"""


def _auto_refresh_prices(symbols: tuple[str, ...]) -> None:
    """打开看板时顺带刷新行情（不调 LLM，近零成本）。

    闸门规则：每个自然日首次进入页面时全量刷新一次；当天已刷过则跳过
    （跨重启也有效，标记落盘）。真抓数时显示全屏 loading 蒙层。"""
    state = _price_refresh_state()
    today = date.today().isoformat()
    if state["date"] == today:
        if state["key"] != symbols:
            # 关注列表变了但今天已刷过：不全量重刷（新增标的在添加时已抓过
            # 价格），只更新指纹。
            state["key"] = symbols
            _save_refresh_mark(state)
        return
    overlay = st.empty()
    overlay.markdown(_loader_html(symbols), unsafe_allow_html=True)
    try:
        c = db.connect(check_same_thread=False)
        for sym in symbols:
            try:
                prices.snapshot(c, sym)
            except Exception:  # noqa: BLE001  失败隔离：某只抓不到不挡开页
                pass
    finally:
        state["date"] = today
        state["key"] = symbols
        _save_refresh_mark(state)
        overlay.empty()


conn = get_conn()
st.markdown(THEME_CSS + CSS, unsafe_allow_html=True)
run_time, watch = load_watchlist()
symbols = [t.symbol for t in watch]
names = {t.symbol: t.name for t in watch}
if symbols:
    _auto_refresh_prices(tuple(symbols))  # 每个自然日首次进入时刷新一次
latest = {s: (db.get_runs(conn, ticker=s, limit=1) or [None])[0] for s in symbols}

# 路由从 URL query params 水合：自动刷新走整页 reload 会清空 session_state，
# 靠 ?p=/&t= 把当前页面恢复回来，避免运行中“闪回总览”。
if "view" not in st.session_state or "detail" not in st.session_state:
    _qp = st.query_params
    _p = _qp.get("p", "overview")
    _t = _qp.get("t")
    if _p == "analysis":
        st.session_state.view = "分析"
        st.session_state.detail = _t if (_t and _t in symbols) else (symbols[0] if symbols else None)
        if st.session_state.detail:
            st.session_state.last_detail = st.session_state.detail
    elif _p == "watchlist":
        st.session_state.view = "管理关注列表"
        st.session_state.detail = None
    elif _p == "review":
        st.session_state.view = "复盘"
        st.session_state.detail = None
    else:
        st.session_state.view = "总览"
        st.session_state.detail = None
if "editing_symbol" not in st.session_state:
    st.session_state.editing_symbol = None
if "pending_delete" not in st.session_state:
    st.session_state.pending_delete = None
if "pending_history_delete" not in st.session_state:
    st.session_state.pending_history_delete = []
if "history_delete_ids" not in st.session_state:
    st.session_state.history_delete_ids = []
if "analysis_delete_pending_ids" not in st.session_state:
    st.session_state.analysis_delete_pending_ids = []
if "analysis_delete_nonce" not in st.session_state:
    st.session_state.analysis_delete_nonce = 0
if "analysis_delete_mode" not in st.session_state:
    st.session_state.analysis_delete_mode = False
if "analysis_delete_symbol" not in st.session_state:
    st.session_state.analysis_delete_symbol = None
if "ai_panel_open" not in st.session_state:
    st.session_state.ai_panel_open = False
if "ai_selected_run_ids" not in st.session_state:
    # 「发给 AI」入口的交接位：面板打开时转成一条带固定记录的会话
    st.session_state.ai_selected_run_ids = []
if "ai_threads" not in st.session_state:
    # 会话模型：[{id, title, pinned_syms, excluded_syms, run_override, messages}]
    # 持久化在 SQLite ai_threads 表,整页刷新/重开浏览器不丢
    try:
        st.session_state.ai_threads = db.get_ai_threads(conn)
    except Exception:  # noqa: BLE001
        st.session_state.ai_threads = []
if "ai_active_thread" not in st.session_state:
    st.session_state.ai_active_thread = None
if "ai_view" not in st.session_state:
    st.session_state.ai_view = "chat"  # chat | threads
if "ai_pending" not in st.session_state:
    st.session_state.ai_pending = None  # 待流式回答的提问
if "ai_composer_ts" not in st.session_state:
    st.session_state.ai_composer_ts = 0  # composer 组件提交去重
if "ai_scope_open" not in st.session_state:
    st.session_state.ai_scope_open = False
if "search_nonce" not in st.session_state:
    st.session_state.search_nonce = 0
if "pending_analysis_symbols" not in st.session_state:
    st.session_state.pending_analysis_symbols = {}


def price_str(sym):
    return price_display(sym)


def run_by_id(run_id: int):
    row = conn.execute("SELECT * FROM runs WHERE id=?", (int(run_id),)).fetchone()
    return Run(**dict(row)) if row else None


def delete_runs_by_ids(run_ids: list[int]) -> None:
    ids = [int(rid) for rid in run_ids]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM reports WHERE run_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM notifications WHERE run_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", ids)
    conn.commit()


@st.dialog("确认删除决策记录")
def confirm_analysis_delete_dialog() -> None:
    ids = [int(rid) for rid in st.session_state.analysis_delete_pending_ids]
    runs_to_delete = [run for run in (run_by_id(rid) for rid in ids) if run]
    if not runs_to_delete:
        st.warning("没有可删除的记录。")
        if st.button("关闭", width="stretch"):
            st.session_state.analysis_delete_pending_ids = []
            st.rerun()
        return

    st.markdown(
        f"<div class='t24-muted'>将删除 {len(runs_to_delete)} 条决策记录，关联报告会一起删除。</div>",
        unsafe_allow_html=True,
    )
    preview_html = "<div class='t24-delete-preview'>"
    for run in runs_to_delete:
        label = BADGE.get(_norm_decision(run.decision), ("", "—"))[1]
        preview_html += (
            "<div class='t24-delete-preview-row'>"
            f"<span>{html_safe(names.get(run.ticker, run.ticker))} · {html_safe(run.ticker)}</span>"
            f"<span>{html_safe(run.trade_date)} · {html_safe(run_display_no(run))} · {html_safe(label)}</span>"
            "</div>"
        )
    preview_html += "</div>"
    st.markdown(preview_html, unsafe_allow_html=True)

    confirm_cols = st.columns(2)
    if confirm_cols[0].button("确认删除", type="primary", width="stretch"):
        delete_runs_by_ids(ids)
        st.session_state.analysis_delete_pending_ids = []
        st.session_state.analysis_delete_nonce += 1
        st.session_state.analysis_delete_mode = False
        for key in list(st.session_state.keys()):
            if str(key).startswith("analysis_pick_"):
                st.session_state[key] = False
        st.toast("已删除选中的决策记录")
        st.rerun()
    if confirm_cols[1].button("取消", width="stretch"):
        st.session_state.analysis_delete_pending_ids = []
        st.rerun()


def conclusion(run_id) -> str:
    """从最终决策段抽执行摘要（完整一段，不截断）。"""
    reps = {r.section: r.content for r in db.get_reports(conn, run_id)}
    txt = reps.get("final") or reps.get("trader") or ""
    # 优先取「Executive Summary」标记后的完整一段（到下一个空行为止）
    m = re.search(r"Executive Summary\**[:：]\s*(.+?)(?:\n\s*\n|\Z)", txt, re.S)
    body = m.group(1) if m else re.split(r"\n\s*\n", txt.strip(), 1)[0]
    body = re.sub(r"\s+", " ", body.strip().lstrip("#*> ").strip())
    return body or "（无摘要）"


def full_run_label(run) -> str:
    name = names.get(run.ticker, run.ticker)
    decision = BADGE.get(_norm_decision(run.decision), ("", "—"))[1]
    return f"{name} · {run.ticker} · {run.trade_date} · {decision} · {run_display_no(run)}"


def selected_ai_runs(run_ids: list[int]):
    runs = []
    for rid in run_ids:
        run = run_by_id(rid)
        if run:
            runs.append(run)
    return runs


def report_context_for_run(run, full: bool = True) -> str:
    lines = [
        f"## {names.get(run.ticker, run.ticker)} · {run.ticker} · {run.trade_date} · {run_display_no(run)}",
        f"- 状态：{status_label(run.status)}",
        f"- 决策：{BADGE.get(_norm_decision(run.decision), ('', '—'))[1]}",
        f"- 模型：{run.model or '未知'}",
        f"- 摘要：{conclusion(run.id or 0)}",
        "",
    ]
    if not full:
        # 精简版：只带最终决策段，控制多标的场景的 token 体积
        reps = {r.section: (r.content or "").strip() for r in db.get_reports(conn, run.id or 0)}
        final = reps.get("final") or reps.get("trader") or ""
        if final:
            lines.extend([f"### {SEC['final']}", final, ""])
        return "\n".join(lines).strip()
    reports = db.get_reports(conn, run.id or 0)
    section_order = ["final", "market", "sentiment", "news", "fundamentals", "bull", "bear",
                     "research_mgr", "trader", "risk"]
    reports.sort(key=lambda r: section_order.index(r.section) if r.section in section_order else 99)
    for rep in reports:
        label = SEC.get(rep.section, rep.section)
        content = (rep.content or "").strip()
        if content:
            lines.extend([f"### {label}", content, ""])
    return "\n".join(lines).strip()


def build_ai_context(run_ids: list[int], focus_syms: list[str] | None = None) -> str:
    """拼接上下文。≤2 条记录时全文；更多时只有 focus（本页/@ 引用）标的给全文，其余给精简版。"""
    runs = selected_ai_runs(run_ids)
    if not runs:
        return ""
    focus = set(focus_syms or [])
    give_full_to_all = len(runs) <= 2
    return "\n\n---\n\n".join(
        report_context_for_run(run, full=give_full_to_all or run.ticker in focus)
        for run in runs
    )


def _ai_chat_request(prompt: str, context_text: str, history: list | None):
    """构造 chat/completions 请求；未配置密钥时抛 RuntimeError(用户可读文案)。"""
    settings = load_settings()
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("还没有配置模型密钥。配置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY 后即可对话。")
    base_url = (settings.backend_url or "https://api.deepseek.com/v1").rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    # 系统消息只带一次分析记录，随后附上多轮对话历史 → 支持连续追问
    messages = [{
        "role": "system",
        "content": (
            "你是 Trading24 的投资分析助手。基于下面提供的历史分析记录和与用户的对话回答；"
            "如果记录不足以支持结论，要明确说明缺口。回答使用中文，先给结论，再给依据和风险。\n"
            "【硬性规则】上下文可能同时包含多只标的、多个日期的报告：任何价位、区间、"
            "止损、目标价等数字只能用于它所属的那只标的，严禁把 A 标的报告里的点位套到 "
            "B 标的上；引用数字前先核对它与该标的当前价格的量级是否一致；"
            "如需引用非当前询问标的的内容，必须明确写出该标的名称。"
            f"\n\n可参考的分析记录：\n{context_text}"
        ),
    }]
    for m in (history or []):
        role = m.get("role")
        if role == "user":
            messages.append({"role": "user", "content": m.get("content", "")})
        elif role == "assistant" and m.get("ok", True):
            messages.append({"role": "assistant", "content": m.get("content", "")})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": settings.quick_think_llm or settings.deep_think_llm,
        "messages": messages,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    return url, headers, payload


def ask_ai(prompt: str, context_text: str, history: list | None = None) -> tuple[bool, str]:
    try:
        url, headers, payload = _ai_chat_request(prompt, context_text, history)
    except RuntimeError as exc:
        return False, str(exc)
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return True, content.strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"AI 请求失败：{type(exc).__name__}。请检查模型密钥、网络和 settings.yaml 的模型配置。"


def stream_ai(prompt: str, context_text: str, history: list | None = None):
    """流式回答生成器。未配置密钥抛 RuntimeError，网络/接口错误按原样抛出。"""
    url, headers, payload = _ai_chat_request(prompt, context_text, history)
    payload["stream"] = True
    with httpx.stream("POST", url, headers=headers, json=payload, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
            except (KeyError, IndexError, ValueError):
                continue
            if delta:
                yield delta


def resolve_ticker_name(symbol: str) -> str:
    """加标的时自动取默认名：用 yfinance 英文名兜底，取不到就用代码；中文名由用户自行编辑。"""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        en = (info.get("shortName") or info.get("longName") or "").strip()
        if en:
            return en
    except Exception:  # noqa: BLE001
        pass
    return symbol


def report_sections(run_id):
    reps = db.get_reports(conn, run_id)
    order = ["market", "sentiment", "news", "fundamentals", "bull", "bear",
             "research_mgr", "trader", "risk", "final"]
    reps.sort(key=lambda r: order.index(r.section) if r.section in order else 99)
    for rep in reps:
        with st.expander(SEC.get(rep.section, rep.section), expanded=(rep.section == "final")):
            st.markdown(md(rep.content) or "_(空)_")


def report_sections_inline(run_id):
    reps = db.get_reports(conn, run_id)
    order = ["final", "market", "sentiment", "news", "fundamentals", "bull", "bear",
             "research_mgr", "trader", "risk"]
    reps.sort(key=lambda r: order.index(r.section) if r.section in order else 99)
    for rep in reps:
        st.markdown(f"#### {SEC.get(rep.section, rep.section)}")
        st.markdown(md(rep.content) or "_(空)_")
        st.divider()


def kpi_card(slot, label: str, value: str, delta: str | None = None) -> None:
    delta_html = f"<div class='t24-kpi-delta'>{html_safe(delta)}</div>" if delta else ""
    slot.markdown(
        f"<div class='t24-kpi-card'>"
        f"<div class='t24-kpi-label'>{html_safe(label)}</div>"
        f"<div class='t24-kpi-value'>{html_safe(value)}</div>"
        f"{delta_html}</div>",
        unsafe_allow_html=True,
    )


def _write_route_query() -> None:
    """把当前路由写进 URL query params，让整页刷新/直接访问能恢复到同一页面。"""
    if st.session_state.detail:
        st.query_params["p"] = "analysis"
        st.query_params["t"] = st.session_state.detail
    else:
        _v = st.session_state.view
        st.query_params["p"] = ("watchlist" if _v == "管理关注列表"
                                else "review" if _v == "复盘" else "overview")
        if "t" in st.query_params:
            del st.query_params["t"]


def goto_dashboard() -> None:
    st.session_state.view = "总览"
    st.session_state.detail = None
    _write_route_query()


def goto_analysis(sym: str | None = None) -> None:
    st.session_state.view = "分析"
    # 无指定标的时，默认打开上一次查看的标的，否则退回第一个
    target = sym or st.session_state.get("last_detail") or (symbols[0] if symbols else None)
    st.session_state.detail = target
    if target:
        st.session_state.last_detail = target
    _write_route_query()


def goto_history() -> None:
    goto_analysis()


def goto_watchlist() -> None:
    st.session_state.view = "管理关注列表"
    st.session_state.detail = None
    _write_route_query()


def goto_review() -> None:
    st.session_state.view = "复盘"
    st.session_state.detail = None
    _write_route_query()


def route_key() -> str:
    if st.session_state.detail:
        return "Analysis"
    if st.session_state.view == "管理关注列表":
        return "Watchlist"
    if st.session_state.view == "复盘":
        return "Review"
    return "Dashboard"


def market_label(sym: str) -> str:
    return {"us": "NASDAQ", "cn": "CN", "hk": "HKEX"}.get(market_of(sym), "GLOBAL")


def latest_price_value(sym: str) -> float | None:
    rows = db.get_prices(conn, sym, limit=1)
    return float(rows[-1]["close"]) if rows and rows[-1].get("close") is not None else None


def price_delta(sym: str) -> tuple[float | None, float | None]:
    rows = db.get_prices(conn, sym, limit=2)
    if len(rows) < 2:
        return None, None
    prev = rows[-2].get("close")
    cur = rows[-1].get("close")
    if prev in (None, 0) or cur is None:
        return None, None
    diff = float(cur) - float(prev)
    return diff, diff / float(prev) * 100


def price_display(sym: str) -> str:
    value = latest_price_value(sym)
    if value is None:
        return "—"
    prefix = "$" if market_of(sym) == "us" else "¥" if market_of(sym) == "cn" else "HK$"
    return f"{prefix}{value:,.2f}"


def delta_display(sym: str) -> str:
    diff, pct = price_delta(sym)
    if diff is None or pct is None:
        return "—"
    sign = "+" if diff >= 0 else ""
    return f"{sign}{pct:.2f}%"


def delta_color(sym: str) -> str:
    """日涨跌颜色随涨跌方向：涨=绿、跌=红、无数据=灰（与决策色相互独立）。"""
    diff, _ = price_delta(sym)
    if diff is None:
        return "var(--t24-muted)"
    return "var(--t24-primary)" if diff >= 0 else "var(--t24-danger)"


def decision_accent(decision: str | None) -> str:
    d = _norm_decision(decision)
    if d == "BUY":
        return "var(--t24-primary)"
    if d == "SELL":
        return "var(--t24-danger)"
    return "var(--t24-muted)"


def run_age(run) -> str:
    if not run:
        return "Not analyzed"
    raw = run.finished_at or run.started_at
    if not raw:
        return "Analyzed"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(dt.tzinfo) - dt).total_seconds()))
    except ValueError:
        return "Analyzed"
    if seconds < 90:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def run_timestamp(run):
    """返回运行的完成/开始时间（aware datetime，naive 视为 UTC），无则 None。"""
    if not run:
        return None
    raw = run.finished_at or run.started_at
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def run_is_today(run) -> bool:
    """运行是否发生在本地日历意义上的“今天”。"""
    dt = run_timestamp(run)
    if not dt:
        return False
    return dt.astimezone().date() == datetime.now().astimezone().date()


def run_within_hours(run, hours: float) -> bool:
    """运行是否落在最近 hours 小时内。"""
    dt = run_timestamp(run)
    if not dt:
        return False
    return (datetime.now(dt.tzinfo) - dt).total_seconds() <= hours * 3600


def token_cost_text(run) -> str:
    if not run:
        return "No run"
    parts = []
    if run.tokens:
        parts.append(f"{run.tokens:,} tok")
    if run.cost_usd:
        parts.append(f"${run.cost_usd:.3f}")
    return " · ".join(parts) or status_label(run.status)


def search_norm(value: str | None) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value or "").lower()


def search_option_label(sym: str) -> str:
    return f"{names.get(sym, sym) or sym} · {sym} · {market_label(sym)}"


def resolve_ticker_search(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw in symbols:
        return raw
    q = search_norm(raw)
    if not q:
        return None
    scored: list[tuple[int, str]] = []
    for sym in symbols:
        fields = [
            sym,
            sym.replace(".", ""),
            names.get(sym, ""),
            market_label(sym),
            search_option_label(sym),
        ]
        normalized = [search_norm(field) for field in fields]
        if q in normalized[:2]:
            scored.append((0, sym))
        elif any(field.startswith(q) for field in normalized):
            scored.append((1, sym))
        elif any(q in field for field in normalized):
            scored.append((2, sym))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], symbols.index(item[1])))
    return scored[0][1]


def normalize_new_ticker(value: str | None) -> str:
    raw = (value or "").strip().upper()
    raw = raw.split("·", 1)[0].strip()
    raw = re.sub(r"\s+", "", raw)
    # 港股归一化成 Yahoo 的 4 位格式：09988.HK→9988.HK，700.HK→0700.HK
    m = re.fullmatch(r"(\d{1,5})\.HK", raw)
    if m and int(m.group(1)) < 10000:
        raw = f"{int(m.group(1)):04d}.HK"
    return raw


def is_ticker_like(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Z][0-9A-Z.]{0,18}", value))


def analysis_worker(symbol: str, trade_date: str, run_id: int | None = None) -> None:
    try:
        runner.run_one(symbol, trade_date, run_id=run_id)
    except Exception:
        # runner.run_one already records the failed run in SQLite.
        pass


def start_background_analysis(symbol: str, run_id: int | None = None,
                              trade_date: str | None = None) -> None:
    """run_id 传入时=复用该记录重跑（失败重试），不新建决策记录。"""
    pending = st.session_state.pending_analysis_symbols
    if symbol in pending:
        return
    pending[symbol] = datetime.now(timezone.utc).isoformat()
    thread = threading.Thread(
        target=analysis_worker,
        args=(symbol, trade_date or date.today().isoformat(), run_id),
        daemon=True,
    )
    thread.start()


BATCH_MAX_CONCURRENCY = 3


def _batch_analysis_worker(syms: list[str], trade_date: str) -> None:
    """后台批量：最多 3 个并发（<3 全并行，>3 按 3 个一组），失败隔离。"""
    with ThreadPoolExecutor(max_workers=BATCH_MAX_CONCURRENCY) as ex:
        list(ex.map(lambda s: analysis_worker(s, trade_date), syms))


def start_batch_analysis(syms: list[str]) -> None:
    """标记排队并起一个协调线程按 3 并发跑给定标的（跳过已在跑的）。"""
    pending = st.session_state.pending_analysis_symbols
    targets = [s for s in syms if s not in pending]
    if not targets:
        return
    for s in targets:
        pending[s] = datetime.now(timezone.utc).isoformat()
    threading.Thread(
        target=_batch_analysis_worker,
        args=(targets, date.today().isoformat()),
        daemon=True,
    ).start()


def today_run_state(sym: str) -> str:
    """某标的今天的运行态：running / done / failed / queued / idle。"""
    r = (db.get_runs(conn, ticker=sym, limit=1) or [None])[0]
    if r and r.status == "running":
        return "running"
    if r and run_is_today(r):
        if r.status == "success":
            return "done"
        if r.status == "failed":
            return "failed"
    if sym in st.session_state.pending_analysis_symbols:
        return "queued"
    return "idle"


PIPELINE_STAGES = [
    ("market", "技术面"), ("sentiment", "情绪面"), ("news", "消息面"),
    ("fundamentals", "基本面"), ("bull", "多方"), ("bear", "空方"),
    ("research_mgr", "研究经理"), ("trader", "交易员"), ("risk", "风险复核"),
    ("final", "最终决策"),
]


def _pipeline_progress_html(run_id: int | None) -> str:
    """按数据库里已落库的报告段，渲染实时阶段清单（✓ 已完成 / ⟳ 进行中 / · 待运行）。"""
    done = set()
    if run_id:
        done = {r.section for r in db.get_reports(conn, run_id)}
    chips = []
    current_marked = False
    for key, label in PIPELINE_STAGES:
        if key in done:
            cls, mark = "done", "✓"
        elif not current_marked:
            cls, mark = "active", "⟳"
            current_marked = True
        else:
            cls, mark = "", "·"
        chips.append(f"<div class='t24-stage-chip {cls}'>{mark} {html_safe(label)}</div>")
    n = len(done & {k for k, _ in PIPELINE_STAGES})
    return (
        f"<div class='t24-stage-grid'>{''.join(chips)}</div>"
        f"<div class='t24-live-run-sub' style='margin-top:8px;'>已完成 {n} / {len(PIPELINE_STAGES)} 段</div>"
    )


def _render_live_status_card(sym: str, run: Run | None) -> str | None:
    """渲染运行状态卡，返回当前 status（pending/running/success/failed/None）。"""
    pending = sym in st.session_state.pending_analysis_symbols
    status = run.status if run else ("pending" if pending else None)
    if status not in {"pending", "running", "success", "failed"}:
        return status
    if status == "pending":
        title = f"{sym} 正在创建分析任务"
        sub = "已完成价格数据校验，正在启动单只股票分析。"
        step_classes = ["done", "active", "", ""]
    elif status == "running":
        title = f"{sym} Agent Pipeline 正在运行"
        sub = "运行中，各环节完成即刻显示，无需等到全部跑完。"
        step_classes = ["done", "done", "active", ""]
    elif status == "success":
        title = f"{sym} 分析完成"
        sub = "最新决策和报告已写入，可以继续查看下方模块。"
        step_classes = ["done", "done", "done", "done"]
    else:
        title = f"{sym} 分析失败"
        sub = html_safe(run.error if run and run.error else "运行失败，请检查 API Key、数据源或限流状态。")
        step_classes = ["done", "done", "active", ""]
    steps = ("数据源", "创建任务", "Agent Pipeline", "写入报告")
    step_html = "".join(
        f"<div class='t24-live-step {css}'>{html_safe(label)}</div>"
        for label, css in zip(steps, step_classes)
    )
    progress_html = ""
    if status in {"pending", "running"}:
        progress_html = _pipeline_progress_html(run.id if run else None)
    st.markdown(
        f"<div class='t24-live-run'>"
        f"<div class='t24-live-run-head'><span>{html_safe(title)}</span>"
        f"<span class='t24-data'>{html_safe(status_label(status))}</span></div>"
        f"<div class='t24-live-run-sub'>{sub}</div>"
        f"<div class='t24-live-run-steps'>{step_html}</div>"
        f"{progress_html}"
        f"</div>",
        unsafe_allow_html=True,
    )
    return status


@st.fragment(run_every=3)
def _live_analysis_fragment(sym: str) -> None:
    """运行中每 3 秒只局部重跑这块（不整页刷新、不闪）；完成后整页 rerun 出完整报告。"""
    run = (db.get_runs(conn, ticker=sym, limit=1) or [None])[0]
    status = _render_live_status_card(sym, run)
    if status in {"success", "failed"}:
        st.session_state.pending_analysis_symbols.pop(sym, None)
        st.rerun()  # 跳出轮询，触发整页 rerun 渲染决策历史和报告


def render_live_analysis_status(sym: str, run: Run | None) -> None:
    pending = sym in st.session_state.pending_analysis_symbols
    status = run.status if run else ("pending" if pending else None)
    if status in {"pending", "running"}:
        _live_analysis_fragment(sym)
    elif status in {"success", "failed"} and pending:
        # 只在本会话刚看着它跑完的这一次显示收尾卡；平时访问不出现
        st.session_state.pending_analysis_symbols.pop(sym, None)
        _render_live_status_card(sym, run)


LLM_PROVIDERS = [
    ("OpenAI", "openai", "https://api.openai.com/v1"),
    ("Google", "google", None),
    ("Anthropic", "anthropic", "https://api.anthropic.com/"),
    ("xAI", "xai", "https://api.x.ai/v1"),
    ("DeepSeek", "deepseek", "https://api.deepseek.com"),
    ("Qwen", "qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ("GLM", "glm", "https://open.bigmodel.cn/api/paas/v4/"),
    ("MiniMax", "minimax", "https://api.minimax.io/v1"),
    ("OpenRouter", "openrouter", "https://openrouter.ai/api/v1"),
    ("Mistral", "mistral", "https://api.mistral.ai/v1"),
    ("Kimi (Moonshot)", "kimi", "https://api.moonshot.ai/v1"),
    ("Groq", "groq", "https://api.groq.com/openai/v1"),
    ("NVIDIA NIM", "nvidia", "https://integrate.api.nvidia.com/v1"),
    ("Azure OpenAI", "azure", None),
    ("Amazon Bedrock", "bedrock", None),
    ("Ollama", "ollama", "http://localhost:11434/v1"),
    ("OpenAI-compatible", "openai_compatible", None),
]
PROVIDER_KEYS = [k for _, k, _ in LLM_PROVIDERS]
PROVIDER_LABEL = {k: d for d, k, _ in LLM_PROVIDERS}
PROVIDER_DEFAULT_URL = {k: u for _, k, u in LLM_PROVIDERS}
ANALYST_OPTIONS = [
    ("market", "市场 / 技术面"),
    ("social", "情绪面"),
    ("news", "新闻面"),
    ("fundamentals", "基本面"),
]
ANALYST_LABEL = {k: v for k, v in ANALYST_OPTIONS}
DEPTH_OPTIONS = [("浅 · 快速（1 轮辩论）", 1), ("中 · 均衡（3 轮）", 3), ("深 · 充分（5 轮）", 5)]
LANGUAGE_OPTIONS = [
    ("Chinese", "中文"), ("English", "English"), ("Japanese", "日本語"),
    ("Korean", "한국어"), ("Spanish", "Español"), ("Portuguese", "Português"),
    ("French", "Français"), ("German", "Deutsch"), ("Russian", "Русский"),
    ("Arabic", "العربية"), ("Hindi", "हिन्दी"),
]
VENDOR_FIELDS = [
    ("core_stock_apis", "行情"),
    ("technical_indicators", "技术指标"),
    ("fundamental_data", "基本面数据"),
    ("news_data", "新闻数据"),
]
PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY", "xai": "XAI_API_KEY", "qwen": "DASHSCOPE_API_KEY",
    "glm": "GLM_API_KEY", "minimax": "MINIMAX_API_KEY", "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY", "kimi": "MOONSHOT_API_KEY", "groq": "GROQ_API_KEY",
    "nvidia": "NVIDIA_API_KEY", "azure": "AZURE_OPENAI_API_KEY", "bedrock": "AWS_ACCESS_KEY_ID",
}


def _model_choices(provider: str, mode: str, current: str | None) -> list[str]:
    """按供应商+模式取模型目录；current 不在列表里则置顶。openrouter/异常时回退空表。"""
    opts: list[str] = []
    try:
        from tradingagents.llm_clients.model_catalog import get_model_options
        opts = [v for _, v in get_model_options(provider, mode) if v and v != "custom"]
    except Exception:
        opts = []
    if current and current not in opts:
        opts = [current] + opts
    return opts


def _launchd_installed() -> bool:
    """检测每日批量的定时任务(launchd)是否已装在本机。"""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=3).stdout
        return "ai.trading24.batch" in out
    except Exception:  # noqa: BLE001  非 macOS / 命令失败 → 视为未安装
        return False


@st.dialog("设置", width="large")
def render_settings_dialog() -> None:
    s = load_settings()
    tab_engine, tab_ops = st.tabs(["分析引擎", "运行与成本"])

    # ── Tab 1：分析引擎 ─────────────────────────────────────
    with tab_engine:
        prov_idx = PROVIDER_KEYS.index(s.llm_provider) if s.llm_provider in PROVIDER_KEYS else 4
        provider = st.selectbox(
            "LLM 供应商", PROVIDER_KEYS, index=prov_idx,
            format_func=lambda k: PROVIDER_LABEL.get(k, k), key="set_provider",
        )
        mc = st.columns(2)
        with mc[0]:
            deep = _model_field("深思考模型", provider, "deep", s.deep_think_llm, "deep")
        with mc[1]:
            quick = _model_field("快思考模型", provider, "quick", s.quick_think_llm, "quick")

        dc = st.columns(2)
        with dc[0]:
            depth_vals = [v for _, v in DEPTH_OPTIONS]
            depth_idx = depth_vals.index(s.max_debate_rounds) if s.max_debate_rounds in depth_vals else 0
            depth = st.radio(
                "研究深度", DEPTH_OPTIONS, index=depth_idx,
                format_func=lambda o: o[0], key="set_depth",
            )[1]
        with dc[1]:
            lang_keys = [k for k, _ in LANGUAGE_OPTIONS]
            lang_idx = lang_keys.index(s.output_language) if s.output_language in lang_keys else 1
            output_language = st.selectbox(
                "报告输出语言", lang_keys, index=lang_idx,
                format_func=lambda k: dict(LANGUAGE_OPTIONS).get(k, k), key="set_lang",
            )

        analysts = st.multiselect(
            "分析师团队", [k for k, _ in ANALYST_OPTIONS],
            default=[a for a in s.analysts if a in ANALYST_LABEL] or [k for k, _ in ANALYST_OPTIONS],
            format_func=lambda k: ANALYST_LABEL.get(k, k), key="set_analysts",
        )

        # 密钥状态：当前供应商 + FRED（隐藏硬依赖，缺了新闻分析师会崩整次运行）
        env_var = PROVIDER_KEY_ENV.get(provider)
        key_ok = bool(env_var and os.getenv(env_var))
        fred_ok = bool(os.getenv("FRED_API_KEY"))
        prov_txt = (f"{env_var}：{'已配置 ✅' if key_ok else '未检测到 ⚠️'}"
                    if env_var else "该供应商的密钥请在 .env 配置")
        st.caption(f"🔑 {prov_txt} · FRED_API_KEY：{'已配置 ✅' if fred_ok else '未检测到 ⚠️'}")
        if "news" in analysts and not fred_ok:
            st.warning("未检测到 FRED_API_KEY：新闻分析师会调用宏观数据（FRED），"
                       "整次分析将失败。请在 .env 配置，或暂时移除上方「新闻面」。")

        with st.expander("高级：接口地址与数据源"):
            default_url = PROVIDER_DEFAULT_URL.get(provider) or ""
            url_seed = s.backend_url if provider == s.llm_provider else default_url
            backend_url = st.text_input(
                "接口地址 backend_url", value=url_seed or "",
                key=f"set_url_{provider}", placeholder=default_url or "该供应商无需自定义地址",
            ) or None
            vendors = {}
            vc = st.columns(2)
            for i, (vk, vlabel) in enumerate(VENDOR_FIELDS):
                cur = (s.data_vendors or {}).get(vk, "yfinance")
                opts = ["yfinance", "alpha_vantage"]
                vendors[vk] = vc[i % 2].selectbox(
                    vlabel, opts, index=opts.index(cur) if cur in opts else 0,
                    key=f"set_vendor2_{vk}",
                )
            st.caption("宏观数据固定走 FRED（必需 FRED_API_KEY）；"
                       "选 alpha_vantage 需在 .env 配 ALPHA_VANTAGE_API_KEY。")

    # ── Tab 2：运行与成本 ───────────────────────────────────
    with tab_ops:
        installed = _launchd_installed()
        st.markdown("<div class='t24-set-sec'>每日批量分析</div>", unsafe_allow_html=True)
        st.caption("定时任务：" + ("已安装 ✅ 将在下方时间自动运行"
                   if installed else
                   "本机未安装 ⚠️ 时间仅作配置——部署到常驻机器后由 "
                   "scripts/install_launchd.sh 生效"))
        oc = st.columns(2)
        with oc[0]:
            new_run_time = st.text_input("每日运行时间 (HH:MM)", value=run_time, key="set_run_time")
        with oc[1]:
            cost_budget = st.number_input(
                "每日成本预算 (USD)", min_value=0.0, value=float(s.cost_budget_usd_per_day),
                step=1.0, key="set_budget",
            )
        today_cost = sum((r.cost_usd or 0) for r in db.get_runs(conn, limit=50)
                         if r.status == "success" and run_is_today(r))
        st.caption(f"今日成本 ${today_cost:.2f} / 预算 ${s.cost_budget_usd_per_day:.0f}"
                   f"（约 ${0.1:.2f}/只/次，超预算仅提醒不拦截）")

        st.markdown("<div class='t24-set-sec'>行情数据</div>", unsafe_allow_html=True)
        st.caption("每天首次打开页面时自动全量刷新一次；盘中需要最新价时手动刷新（免费，不调用 LLM）。")
        if st.button("立即刷新行情", key="set_refresh_now", width="stretch"):
            with st.spinner("正在拉取最新行情…"):
                _c = db.connect(check_same_thread=False)
                for _sym in symbols:
                    try:
                        prices.snapshot(_c, _sym)
                    except Exception:  # noqa: BLE001
                        pass
                _state = _price_refresh_state()
                _state["date"] = date.today().isoformat()
                _state["key"] = tuple(symbols)
                _save_refresh_mark(_state)
            st.toast(f"已刷新 {len(symbols)} 只标的的行情")

        with st.expander("成本核算单价（高级）"):
            pc = st.columns(2)
            price_in = pc[0].number_input(
                "输入单价 ($/1M tok)", min_value=0.0, value=float(s.price_per_1m_input),
                step=0.01, format="%.2f", key="set_price_in",
            )
            price_out = pc[1].number_input(
                "输出单价 ($/1M tok)", min_value=0.0, value=float(s.price_per_1m_output),
                step=0.01, format="%.2f", key="set_price_out",
            )
            st.caption("仅影响成本估算的换算，按 LLM 供应商实际账单调整。")

    st.divider()
    st.caption("通知（Telegram 每日摘要/价格预警）规划中，尚未上线。")
    bc = st.columns([1, 1])
    if bc[0].button("保存", type="primary", width="stretch", key="set_save"):
        clean_time = new_run_time.strip()
        if clean_time and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", clean_time):
            st.error("运行时间格式请使用 HH:MM，例如 08:30。")
            return
        new = Settings(
            llm_provider=provider,
            deep_think_llm=deep.strip() or s.deep_think_llm,
            quick_think_llm=quick.strip() or s.quick_think_llm,
            backend_url=backend_url,
            max_debate_rounds=depth,
            max_risk_discuss_rounds=depth,
            output_language=output_language,
            data_vendors={k: v for k, v in vendors.items() if v},
            cost_budget_usd_per_day=cost_budget,
            price_per_1m_input=price_in,
            price_per_1m_output=price_out,
            notify=dict(s.notify or {}),   # 通知未上线：原样保留 yaml 配置
            analysts=analysts or ["market", "social", "news", "fundamentals"],
        )
        save_settings(new)
        if clean_time and clean_time != run_time:
            save_watchlist(clean_time, watch)
        st.toast("设置已保存，下次分析生效")
        st.rerun()
    if bc[1].button("取消", width="stretch", key="set_cancel"):
        st.rerun()


def _model_field(label: str, provider: str, mode: str, current: str | None, slot: str) -> str:
    """模型选择：优先目录下拉，勾选“自定义”改为手填。"""
    choices = _model_choices(provider, mode, current)
    custom = st.checkbox("自定义", key=f"set_model_custom_{slot}_{provider}",
                         value=not choices)
    if custom or not choices:
        return st.text_input(label, value=current or "", key=f"set_model_txt_{slot}_{provider}")
    idx = choices.index(current) if current in choices else 0
    return st.selectbox(label, choices, index=idx, key=f"set_model_sel_{slot}_{provider}")


def render_market_tape() -> None:
    tape_items = []
    for sym in symbols:  # 全部标的滚动展示，新增标的自动同步
        tape_items.append(
            f"{html_safe(sym)}: {html_safe(price_display(sym))} "
            f"({html_safe(delta_display(sym))})"
        )
    if not tape_items:
        tape_items.append("No watchlist symbols")
    tape_items.append(f"Scheduler: Next run {html_safe(run_time)}")
    sep = " &nbsp; | &nbsp; "
    seq = sep.join(tape_items) + sep
    # 轨道内容复制两份 + translateX(-50%) 实现无缝循环滚动
    seg = f"<span class='t24-tape-seg'>{seq}</span>"
    st.markdown(
        f"<div class='t24-tape'><div class='t24-tape-track'>{seg}"
        f"<span class='t24-tape-seg' aria-hidden='true'>{seq}</span></div></div>",
        unsafe_allow_html=True)


def render_top_nav() -> None:
    active = route_key()
    with st.container(key="t24_topnav"):
        nav_cols = st.columns(
            [1.3, .5, .5, .5, .5, 1.6, 1.3, .5, .5, .5],
            gap="small",
            vertical_alignment="center",
        )
        nav_cols[0].markdown("<div class='t24-logo'>TRADING24</div>", unsafe_allow_html=True)
        nav_items = [
            ("Dashboard", "总览", goto_dashboard, icon("overview", 15)),
            ("Analysis", "分析", goto_analysis, icon("analysis", 15)),
            ("Review", "复盘", goto_review, None),
            ("Watchlist", "关注", goto_watchlist, icon("watch", 15)),
        ]
        for col, (key, label, handler, _) in zip(nav_cols[1:5], nav_items):
            if col.button(label, key=f"topnav_{key}", width="stretch",
                          type="primary" if active == key else "secondary"):
                handler()
                st.rerun()
        nav_cols[5].markdown("")  # 弹性留白，把搜索栏推到右侧
        search = nav_cols[6].selectbox(
            "Search ticker",
            symbols,
            index=None,
            format_func=search_option_label,
            placeholder="搜索代码…",
            label_visibility="collapsed",
            key=f"global_ticker_search_{st.session_state.search_nonce}",
            accept_new_options=True,
            filter_mode="fuzzy",
        )
        if search:
            matched = resolve_ticker_search(str(search))
            if matched:
                st.session_state.search_nonce += 1
                goto_analysis(matched)
                st.rerun()
            candidate = normalize_new_ticker(str(search))
            if nav_cols[7].button("添加", key="search_add_ticker", width="stretch"):
                if not is_ticker_like(candidate):
                    st.toast("请输入股票代码，例如 AAPL / 600519.SS / 0700.HK")
                    st.rerun()
                with st.spinner(f"校验并添加 {candidate} ..."):
                    try:
                        got = prices.snapshot(conn, candidate)
                    except Exception:
                        got = 0
                if not got:
                    st.toast(f"取不到 {candidate} 的价格数据，请检查代码格式"
                             "（美股直接代码 / A股 .SS/.SZ / 港股 4位数.HK）", icon="⚠️")
                    st.rerun()
                rt, current_watch = load_watchlist()
                current_symbols = [t.symbol for t in current_watch]
                if candidate not in current_symbols:
                    current_watch.append(TickerCfg(symbol=candidate, name=resolve_ticker_name(candidate)))
                    save_watchlist(rt, current_watch)
                start_background_analysis(candidate)
                st.session_state.search_nonce += 1
                goto_analysis(candidate)
                st.toast(f"已添加 {candidate}，正在运行分析")
                st.rerun()
        else:
            nav_cols[7].markdown("")
        next_theme = "深色" if st.session_state.theme_mode == "浅色" else "浅色"
        theme_key = "theme_toggle_dark" if next_theme == "深色" else "theme_toggle_light"
        if nav_cols[8].button("切换主题", key=theme_key, width="stretch", help="切换深浅主题"):
            st.session_state.theme_mode = next_theme
            st.rerun()
        if nav_cols[9].button("设置", key="open_settings", width="stretch"):
            render_settings_dialog()


def render_pipeline_scrollspy() -> None:
    components.html(
        """
        <script>
        (() => {
          try {
            const doc = window.parent.document;
            const links = Array.from(doc.querySelectorAll(".t24-pipeline-item[data-target]"));
            if (!links.length) return;
            let manualActiveUntil = 0;
            const activate = (link) => {
              links.forEach((item) => item.classList.remove("active", "is-active"));
              if (link) link.classList.add("is-active");
            };
            const openAndScroll = (targetId) => {
              const target = doc.getElementById(targetId);
              if (!target) return;
              let drawer = target.closest("details");
              while (drawer) {
                drawer.open = true;
                drawer = drawer.parentElement ? drawer.parentElement.closest("details") : null;
              }
              window.setTimeout(() => target.scrollIntoView({ block: "start", behavior: "smooth" }), 40);
            };
            links.forEach((link) => {
              if (link.dataset.bound === "1") return;
              link.dataset.bound = "1";
              link.addEventListener("click", (event) => {
                event.preventDefault();
                manualActiveUntil = Date.now() + 2200;
                activate(link);
                openAndScroll(link.dataset.target);
              });
            });
            const sync = () => {
              if (Date.now() < manualActiveUntil) return;
              let best = null;
              let bestScore = Infinity;
              const viewportHeight = window.parent.innerHeight || doc.documentElement.clientHeight || 900;
              links.forEach((link) => {
                const target = doc.getElementById(link.dataset.target);
                if (!target) return;
                const rect = target.getBoundingClientRect();
                if (rect.top === 0 && rect.height === 0) return;
                if (rect.bottom < 100 || rect.top > viewportHeight - 80) return;
                const score = Math.abs(rect.top - 180);
                if (score < bestScore) {
                  best = link;
                  bestScore = score;
                }
              });
              if (!best) {
                best = links
                  .map((link) => [link, doc.getElementById(link.dataset.target)])
                  .filter((pair) => pair[1])
                  .sort((a, b) => Math.abs(a[1].getBoundingClientRect().top - 170) - Math.abs(b[1].getBoundingClientRect().top - 170))[0]?.[0];
              }
              if (best) activate(best);
            };
            const scroller = doc.querySelector('[data-testid="stMain"]');
            window.parent.addEventListener("scroll", sync, { passive: true });
            if (scroller) scroller.addEventListener("scroll", sync, { passive: true });
            doc.addEventListener("scroll", sync, { passive: true });
            window.setInterval(sync, 600);
            sync();
          } catch (error) {}
        })();
        </script>
        """,
        height=0,
    )


render_market_tape()
render_top_nav()


def kpi_row():
    done = sum(1 for s in symbols if latest[s] and latest[s].status == "success")
    cost = sum((latest[s].cost_usd or 0) for s in symbols if latest[s])
    buys = sum(1 for s in symbols if latest[s] and _norm_decision(latest[s].decision) == "BUY")
    sells = sum(1 for s in symbols if latest[s] and _norm_decision(latest[s].decision) == "SELL")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("今日已分析", f"{done}/{len(symbols)}")
    c2.metric("今日成本", f"${cost:.2f}")
    c3.metric("增持信号", buys)
    c4.metric("减持信号", sells)


def detail_view(sym):
    # 失败记录重跑：?rerun=<run_id> 触发，复用原记录、不新建决策
    _rq = st.query_params.get("rerun")
    if _rq:
        del st.query_params["rerun"]
        try:
            _rid = int(_rq)
        except (TypeError, ValueError):
            _rid = None
        _rr = db.get_run(conn, _rid) if _rid else None
        if (_rr and _rr.ticker == sym and _rr.status == "failed"
                and sym not in st.session_state.pending_analysis_symbols):
            db.reset_run(conn, _rid)  # 先同步置回 running，避免渲染竞态闪"失败"卡
            start_background_analysis(sym, run_id=_rid, trade_date=_rr.trade_date)
            st.toast(f"正在重跑 {sym} · {_rr.trade_date}（复用原记录，不新增决策）")
        st.rerun()

    r = latest[sym]
    rows = db.get_runs(conn, ticker=sym, limit=200)
    if st.session_state.analysis_delete_symbol != sym:
        st.session_state.analysis_delete_symbol = sym
        st.session_state.analysis_delete_mode = False
        st.session_state.analysis_delete_pending_ids = []
        for key in list(st.session_state.keys()):
            if str(key).startswith("analysis_pick_"):
                st.session_state[key] = False
    shell_left, shell_main = st.columns([.24, .76], gap="large")
    with shell_left:
        pipeline_html = (
            "<div class='t24-pipeline'>"
            f"<div class='t24-pipeline-head'><div class='t24-bento-title' style='margin:0;'>"
            f"{icon('cpu', 18)}<span>Agent Pipeline</span></div></div>"
        )
        for section, label, icn in REPORT_NAV:
            target = f"t24-report-{REPORT_ANCHORS[section]}"
            state = "active" if section == "final" else ""
            pipeline_html += (
                f"<a class='t24-pipeline-item {state}' href='#{target}' data-target='{target}'>"
                f"{icon(icn, 17)}<span>{label}</span></a>"
            )
        pipeline_html += "</div>"
        shell_left.markdown(pipeline_html, unsafe_allow_html=True)

    with shell_main:
        if symbols:
            with st.container(key="t24_analysis_filter"):
                filter_left, filter_right = st.columns([1.1, .9], vertical_alignment="center")
                filter_left.markdown(
                    "<div class='caps caps-field'>分析标的</div>",
                    unsafe_allow_html=True,
                )
                selected_sym = filter_right.selectbox(
                    "分析标的",
                    symbols,
                    index=symbols.index(sym) if sym in symbols else 0,
                    format_func=search_option_label,
                    label_visibility="collapsed",
                    key=f"analysis_symbol_filter_{sym}",
                )
                if selected_sym != sym:
                    goto_analysis(selected_sym)
                    st.rerun()

        render_live_analysis_status(sym, r)

        diff, pct = price_delta(sym)
        price_delta_text = ""
        if diff is not None and pct is not None:
            sign = "+" if diff >= 0 else ""
            price_delta_text = f"{sign}{diff:.2f} ({sign}{pct:.2f}%)"
        hero = (
            f"<div class='t24-ticker-hero'>"
            f"<div><div><span class='t24-title'>{html_safe(names.get(sym, sym) or sym)}</span>"
            f"<span class='t24-ticker-code'>{html_safe(market_label(sym))}</span></div>"
            f"<div class='t24-muted'>{html_safe(sym)}</div></div>"
            f"<div><div class='t24-hero-price'>{html_safe(price_display(sym))}</div>"
            f"<div class='t24-data' style='color:{delta_color(sym)};'>{html_safe(price_delta_text)}</div></div>"
            f"<div style='text-align:right;'><div class='caps'>决策状态</div>"
            f"{badge(r.decision if r else None, big=True)}"
            f"<div class='t24-data' style='font-size:12px;color:var(--t24-muted);margin-top:8px;'>"
            f"{html_safe(token_cost_text(r))}</div></div></div>"
        )
        st.markdown(hero, unsafe_allow_html=True)

        plan_html = plan_strip_html(r)
        if plan_html:
            st.markdown(plan_html, unsafe_allow_html=True)

        st.markdown("<div class='caps' style='margin-bottom:8px;'>Price Action · Decisions</div>",
                    unsafe_allow_html=True)
        st.plotly_chart(
            build_trend_figure(db.get_prices(conn, sym), rows, ACTIVE_THEME),
            width="stretch",
            config={"displayModeBar": False},
        )
        shown_rows = rows[:6]
        shown_by_id = {int(item.id): item for item in shown_rows if item.id is not None}
        if shown_by_id:
            with st.container(key="t24_decision_actions"):
                if st.session_state.analysis_delete_mode:
                    selected_delete_ids = [
                        int(item.id) for item in shown_rows
                        if item.id is not None and st.session_state.get(f"analysis_pick_{sym}_{item.id}", False)
                    ]
                    mode_cols = st.columns([1, 0.16, 0.18, 0.16], vertical_alignment="center")
                    if mode_cols[1].button("取消", width="stretch", key=f"analysis_cancel_delete_{sym}"):
                        st.session_state.analysis_delete_mode = False
                        st.session_state.analysis_delete_pending_ids = []
                        for key in list(st.session_state.keys()):
                            if str(key).startswith("analysis_pick_"):
                                st.session_state[key] = False
                        st.rerun()
                    if mode_cols[2].button("发给 AI", disabled=not selected_delete_ids, width="stretch",
                                            key=f"analysis_send_ai_{sym}"):
                        st.session_state.ai_selected_run_ids = selected_delete_ids
                        st.session_state.ai_panel_open = True
                        st.session_state.analysis_delete_mode = False
                        for key in list(st.session_state.keys()):
                            if str(key).startswith("analysis_pick_"):
                                st.session_state[key] = False
                        st.rerun()
                    if mode_cols[3].button("删除", disabled=not selected_delete_ids, width="stretch",
                                            key=f"analysis_delete_button_{sym}"):
                        st.session_state.analysis_delete_pending_ids = selected_delete_ids
                        confirm_analysis_delete_dialog()
                else:
                    normal_cols = st.columns([1, 0.18], vertical_alignment="center")
                    if normal_cols[1].button("操作", width="stretch", key=f"analysis_action_button_{sym}"):
                        st.session_state.analysis_delete_mode = True
                        st.session_state.analysis_delete_pending_ids = []
                        for key in list(st.session_state.keys()):
                            if str(key).startswith("analysis_pick_"):
                                st.session_state[key] = False
                        st.rerun()

        if st.session_state.analysis_delete_mode:
            with st.container(key="t24_decision_select_card"):
                st.markdown(
                    f"<div id='t24-section-decisions' class='t24-decision-history-head'>"
                    f"<div class='t24-decision-history-title'>{icon('history', 18)}<span>决策历史</span></div>"
                    f"<div class='t24-decision-history-meta'>最近 30 天 · {len(shown_rows)} 条</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                for item in shown_rows:
                    if item.id is None:
                        continue
                    pick_key = f"analysis_pick_{sym}_{item.id}"
                    accent = decision_accent(item.decision)
                    selected_class = " is-selected" if st.session_state.get(pick_key, False) else ""
                    select_cols = st.columns([0.06, 0.94], vertical_alignment="center")
                    select_cols[0].checkbox("选择", key=pick_key, label_visibility="collapsed")
                    select_cols[1].markdown(
                        f"<div class='t24-decision-select-summary{selected_class}' style='--decision:{accent};'>"
                        f"<div class='t24-decision-row'>"
                        f"<div class='t24-decision-main'><span class='t24-decision-dot'></span>"
                        f"<div class='t24-decision-copy'>"
                        f"<div class='t24-decision-date'>{html_safe(item.trade_date)}</div>"
                        f"<div class='t24-decision-sub'>{html_safe(run_display_no(item))} · "
                        f"{html_safe(status_label(item.status))}</div>"
                        f"</div></div>"
                        f"<span class='t24-decision-chip'>{html_safe(decision_label(item.decision))}</span>"
                        f"<span class='t24-decision-chevron'>{icon('chevron', 14)}</span>"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
        else:
            history_html = (
                f"<div id='t24-section-decisions' class='t24-bento t24-decision-history'>"
                f"<div class='t24-decision-history-head'>"
                f"<div class='t24-decision-history-title'>{icon('history', 18)}<span>决策历史</span></div>"
                f"<div class='t24-decision-history-meta'>最近 30 天 · {len(shown_rows)} 条</div>"
                f"</div><div class='t24-decision-list'>"
            )
            for item in shown_rows:
                accent = decision_accent(item.decision)
                can_expand = item.status == "success"
                decision_open_attr = " open" if r and item.id == r.id and can_expand else ""
                if item.status == "failed" and item.id is not None:
                    # 失败记录：灰色决策位换成「重跑」，复用原记录、不新增决策
                    chip = (f"<a class='t24-decision-chip t24-rerun-chip' "
                            f"href='?p=analysis&t={quote(str(sym), safe='')}&rerun={int(item.id)}' "
                            f"target='_self'>重跑</a>")
                else:
                    chip = (f"<span class='t24-decision-chip'>"
                            f"{html_safe(decision_label(item.decision))}</span>")
                history_html += f"<details class='t24-decision-item'{decision_open_attr}>"
                history_html += (
                    f"<summary style='--decision:{accent};'><div class='t24-decision-row'>"
                    f"<div class='t24-decision-main'><span class='t24-decision-dot'></span>"
                    f"<div class='t24-decision-copy'>"
                    f"<div class='t24-decision-date'>{html_safe(item.trade_date)}</div>"
                    f"<div class='t24-decision-sub'>{html_safe(run_display_no(item))} · "
                    f"{html_safe(status_label(item.status))}</div>"
                    f"</div></div>"
                    f"{chip}"
                    f"<span class='t24-decision-chevron'>{icon('chevron', 14)}</span>"
                    f"</div></summary>"
                )
                if can_expand:
                    history_html += report_cards_html(item, include_anchors=bool(r and item.id == r.id))
                else:
                    history_html += "<div class='t24-muted' style='padding:14px 0 18px;'>该次运行暂无完整报告。</div>"
                history_html += "</details>"
            history_html += "</div></div>"
            st.markdown(history_html, unsafe_allow_html=True)

    render_pipeline_scrollspy()


def _sparkline_svg(sym: str, w: int = 300, h: int = 30) -> str:
    """近 120 日收盘价：中性灰走势线 + 决策 ▲▼● 标记（形状/颜色随决策）。"""
    rows = db.get_prices(conn, sym, limit=120)
    data = [(x.get("date"), float(x["close"])) for x in rows
            if x.get("close") is not None and x.get("date")]
    if len(data) < 2:
        return f"<svg class='t24-ov-spark' viewBox='0 0 {w} {h}' preserveAspectRatio='none'></svg>"
    closes = [c for _, c in data]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    pad, n = 5, len(data)

    def _xy(i, c):
        return i / (n - 1) * w, pad + (1 - (c - lo) / span) * (h - 2 * pad)

    poly = " ".join(f"{x:.1f},{y:.1f}" for i, (_, c) in enumerate(data) for x, y in [_xy(i, c)])
    line = (f"<polyline points='{poly}' fill='none' stroke='var(--t24-subtle)' "
            f"stroke-width='1.4' vector-effect='non-scaling-stroke'/>")
    # 决策标记落在对应日期的收盘点上；sx 略窄以抵消横向拉伸
    idx = {d: i for i, (d, _) in enumerate(data)}
    marks = ""
    colors = {"BUY": "var(--t24-primary)", "SELL": "var(--t24-danger)", "HOLD": "var(--t24-muted)"}
    sx, sy = 2.8, 3.8
    for dec, pts in charts_mod._decision_points(rows, db.get_runs(conn, ticker=sym, limit=200)).items():
        col = colors.get(dec, "var(--t24-muted)")
        for pdate, pclose in pts:
            if pdate not in idx:
                continue
            x, y = _xy(idx[pdate], pclose)
            if dec == "SELL":
                marks += f"<path d='M{x:.1f},{y + sy:.1f} L{x - sx:.1f},{y - sy:.1f} L{x + sx:.1f},{y - sy:.1f} Z' fill='{col}'/>"
            elif dec == "HOLD":
                marks += f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{sx:.1f}' fill='{col}'/>"
            else:
                marks += f"<path d='M{x:.1f},{y - sy:.1f} L{x - sx:.1f},{y + sy:.1f} L{x + sx:.1f},{y + sy:.1f} Z' fill='{col}'/>"
    return (f"<svg class='t24-ov-spark' viewBox='0 0 {w} {h}' preserveAspectRatio='none'>"
            f"{line}{marks}</svg>")


def overview():
    # “今日运行状态”只统计今天实际跑成功的标的，避免拿历史成功记录凑满进度
    done = sum(1 for s in symbols
               if latest[s] and latest[s].status == "success" and run_is_today(latest[s]))
    running = sum(1 for s in symbols if latest[s] and latest[s].status == "running")
    total = max(len(symbols), 1)
    pct = int(done / total * 100)

    # 决策分布只统计今天实际跑出的结果
    def _today_decision_count(kind: str) -> int:
        return sum(1 for s in symbols
                   if latest[s] and run_is_today(latest[s])
                   and _norm_decision(latest[s].decision) == kind)

    buys = _today_decision_count("BUY")
    holds = _today_decision_count("HOLD")
    sells = _today_decision_count("SELL")
    # Recent Alerts 只展示近 24 小时的有效分析，避免 running/failed 占位记录显示成 “TSLA —”
    recent_runs = [
        r for r in db.get_runs(conn, limit=50)
        if r.status == "success"
        and _norm_decision(r.decision) in {"BUY", "HOLD", "SELL"}
        and run_within_hours(r, 24)
    ][:5]
    run_word = "分析进行中" if running else ("今日已完成" if done >= len(symbols) else "待运行")

    # 今日运行 tile 的补充：每只标的的状态胶囊 + 今日成本
    _state_color = {"done": "var(--t24-primary)", "running": "var(--t24-warning)",
                    "failed": "var(--t24-danger)", "idle": "var(--t24-subtle)"}

    def _sym_state(s: str) -> str:
        r = latest[s]
        if r and r.status == "running":
            return "running"
        if r and run_is_today(r) and r.status == "success":
            return "done"
        if r and run_is_today(r) and r.status == "failed":
            return "failed"
        return "idle"

    run_chips = "".join(
        f"<span class='t24-ov-chip'><i style='background:{_state_color[_sym_state(s)]};'></i>"
        f"{html_safe(s)}</span>"
        for s in symbols)
    today_cost = sum((r.cost_usd or 0) for r in db.get_runs(conn, limit=50)
                     if r.status == "success" and run_is_today(r))
    cost_note = (f"<div class='t24-ov-tile-note'>今日成本 ${today_cost:.2f}</div>"
                 if today_cost > 0 else "")

    # 今日决策分布 tile 的补充：比例条 + 各方向标的
    dec_syms = {"BUY": [], "HOLD": [], "SELL": []}
    for s in symbols:
        r = latest[s]
        if r and run_is_today(r) and r.status == "success":
            d = _norm_decision(r.decision)
            if d in dec_syms:
                dec_syms[d].append(s)
    total_dec = buys + holds + sells
    if total_dec:
        _seg = "".join(
            f"<span style='width:{n / total_dec * 100:.1f}%;background:{c};'></span>"
            for n, c in ((buys, "var(--t24-primary)"), (holds, "var(--t24-subtle)"),
                         (sells, "var(--t24-danger)")) if n)
        dist_bar = f"<div class='t24-ov-dist-bar'>{_seg}</div>"
        _rows = []
        for key, lab, color in (("BUY", "增持", "var(--t24-primary)"),
                                ("SELL", "减持", "var(--t24-danger)"),
                                ("HOLD", "中性", "var(--t24-muted)")):
            if dec_syms[key]:
                _rows.append(f"<div class='t24-ov-dist-row'><span style='color:{color};'>"
                             f"{lab}</span> · {html_safe('、'.join(dec_syms[key]))}</div>")
        dist_detail = "".join(_rows)
    else:
        dist_bar = "<div class='t24-ov-dist-bar'></div>"
        dist_detail = "<div class='t24-ov-dist-row'>今日暂未产出决策，跑完批量后在此汇总</div>"

    st.markdown(
        "<div class='t24-page-head'>"
        "<div><div class='t24-headline'>关注列表总览</div>"
        "<div class='t24-subhead'>基于每日智能体分析的高优先级标的。</div></div>"
        "<div class='caps'>Live workspace</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ---- 顶部摘要条：今日运行 / 决策分布 / 近期提醒 ----
    if recent_runs:
        alert_rows = []
        for a in recent_runs:
            a_href = f"?p=analysis&t={quote(str(a.ticker), safe='')}"
            alert_rows.append(
                f"<a class='t24-alert-link t24-alert-row' href='{a_href}' target='_self'>"
                f"<span style='color:{decision_accent(a.decision)};'>{icon('bell', 15)}</span>"
                f"<span><strong style='color:{decision_accent(a.decision)};'>"
                f"{html_safe(a.ticker)} {html_safe(decision_label(a.decision))}</strong>"
                f"<span style='color:var(--t24-muted);'> · "
                f"{html_safe(names.get(a.ticker, a.ticker))}</span></span>"
                f"<span style='color:var(--t24-muted);'>{html_safe(run_age(a))}</span>"
                f"</a>"
            )
        alert_body = (
            f"<div style='font-size:13px;font-weight:750;margin:0 0 2px;'>"
            f"{len(recent_runs)} 条</div>"
            f"<div>{''.join(alert_rows)}</div>"
        )
    else:
        alert_body = "<div style='font-size:13px;color:var(--t24-muted);'>近 24 小时暂无提醒</div>"

    st.markdown(
        "<div class='t24-ov-summary'>"
        f"<div class='t24-ov-tile'><div class='t24-ov-tile-label'>今日运行 · {run_word}</div>"
        f"<div style='display:flex;align-items:baseline;gap:6px;'>"
        f"<span class='t24-data' style='font-size:24px;color:var(--t24-text);'>{done}</span>"
        f"<span class='t24-data' style='font-size:14px;color:var(--t24-muted);'>/ {len(symbols)} 已完成</span></div>"
        f"<div class='t24-progress' style='--value:{pct}%;margin:8px 0 0;'><span></span></div>"
        f"<div class='t24-ov-chiprow'>{run_chips}</div>{cost_note}</div>"
        f"<div class='t24-ov-tile'><div class='t24-ov-tile-label'>今日决策分布</div>"
        f"<div class='t24-data' style='display:flex;gap:16px;font-size:13px;'>"
        f"<span style='color:var(--t24-primary);'>● 增持 {buys}</span>"
        f"<span style='color:var(--t24-muted);'>● 中性 {holds}</span>"
        f"<span style='color:var(--t24-danger);'>● 减持 {sells}</span></div>"
        f"{dist_bar}{dist_detail}</div>"
        f"<div class='t24-ov-tile'><div class='t24-ov-tile-label'>近期提醒</div>{alert_body}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ---- 公司卡片网格：紧凑、响应式，整卡点进详情（FILO：最新在前）----
    cards = ""
    for sym in reversed(symbols):
        r = latest[sym]
        href = f"?p=analysis&t={quote(str(sym), safe='')}"
        if r and r.status == "success":
            concl = f"<div class='t24-ov-concl'>{html_safe(conclusion(r.id))}</div>"
        elif r and r.status == "failed":
            concl = "<div class='t24-ov-concl' style='color:var(--t24-danger);'>最近运行失败</div>"
        else:
            concl = "<div class='t24-ov-concl'>暂无分析</div>"
        cards += (
            f"<a class='t24-ov-card' href='{href}' target='_self'>"
            f"<div class='t24-ov-head'><span class='t24-ov-name'>{html_safe(names.get(sym, sym) or sym)}</span>"
            f"{badge(r.decision if r else None)}</div>"
            f"<div class='t24-ov-meta'>"
            f"<span class='t24-ov-code'>{html_safe(sym)} · {html_safe(market_label(sym))}</span>"
            f"<span><span class='t24-ov-price'>{html_safe(price_display(sym))}</span> "
            f"<span class='t24-ov-delta' style='color:{delta_color(sym)};'>{html_safe(delta_display(sym))}</span></span>"
            f"</div>"
            f"{_sparkline_svg(sym)}"
            f"{concl}"
            f"{plan_line_html(r)}"
            f"<div class='t24-ov-foot'><span class='t24-ov-foot-age'>{html_safe(run_age(r))}</span>"
            f"<span class='t24-ov-foot-cta'>查看分析 →</span></div>"
            f"</a>"
        )
    st.markdown(f"<div class='t24-ov-grid'>{cards}</div>", unsafe_allow_html=True)


def history():
    st.markdown(
        f"<div class='t24-page-title'>{icon('history', 23)}<span>历史记录</span></div>"
        "<div class='t24-muted'>按标的查看每次分析结果。默认只展示运行列表和最终结论，完整报告按需展开。</div>",
        unsafe_allow_html=True,
    )
    sym = st.selectbox("标的", symbols, format_func=lambda s: f"{names.get(s,'')} · {s}",
                       label_visibility="collapsed", key="history_symbol")
    runs = db.get_runs(conn, ticker=sym, limit=50)
    if not runs:
        st.info("暂无历史记录。")
        return

    succ = [r for r in runs if r.status == "success"]
    failed = [r for r in runs if r.status != "success"]
    latest_success = succ[0] if succ else None
    m1, m2, m3 = st.columns(3)
    latest_decision = BADGE.get(_norm_decision(latest_success.decision), ("", "—"))[1] if latest_success else "—"
    latest_date = latest_success.trade_date if latest_success else None
    kpi_card(m1, "最新有效", latest_decision, latest_date)
    kpi_card(m2, "成功记录", str(len(succ)))
    kpi_card(m3, "待清理", str(len(failed)))

    st.markdown("<div class='caps' style='margin:16px 0 6px;'>运行记录 · 选择后删除</div>",
                unsafe_allow_html=True)
    selected_ids: list[int] = [int(rid) for rid in st.session_state.history_delete_ids]
    for r in runs:
        selected = r.id in selected_ids
        cols = st.columns([1.15, 0.8, 0.75, 0.6, 0.9], vertical_alignment="center")
        row_bg = "color-mix(in srgb, var(--t24-primary) 10%, transparent)" if selected else "transparent"
        cols[0].markdown(f"<div style='background:{row_bg};border-radius:8px;padding:8px 10px;'>"
                         f"<span class='mono' style='font-size:14px;'>{r.trade_date}</span></div>",
                         unsafe_allow_html=True)
        cols[1].markdown(badge(r.decision), unsafe_allow_html=True)
        cols[2].markdown(f"<span class='mono' style='font-size:12px;color:var(--t24-muted);'>{status_label(r.status)}</span>",
                         unsafe_allow_html=True)
        cols[3].markdown(
            f"<span class='mono' style='font-size:12px;color:var(--t24-muted);'>"
            f"{html_safe(run_display_no(r))}</span>",
                         unsafe_allow_html=True)
        if cols[4].button("取消" if selected else "选择", key=f"hist_pick_{r.id}", width="stretch"):
            if selected:
                st.session_state.history_delete_ids = [rid for rid in selected_ids if rid != r.id]
            else:
                st.session_state.history_delete_ids = selected_ids + [r.id]
            st.rerun()

    action_cols = st.columns([1, 1, 1.3, 3])
    if action_cols[0].button("删除", disabled=not selected_ids, width="stretch"):
        st.session_state.pending_history_delete = selected_ids
        st.rerun()
    if action_cols[1].button("清空", disabled=not selected_ids, width="stretch"):
        st.session_state.history_delete_ids = []
        st.session_state.pending_history_delete = []
        st.rerun()
    if action_cols[2].button("发给 AI", disabled=not selected_ids, width="stretch"):
        st.session_state.ai_selected_run_ids = selected_ids
        st.session_state.ai_panel_open = True
        st.rerun()

    pending_ids = st.session_state.pending_history_delete
    if pending_ids:
        st.warning(f"确认删除 {len(pending_ids)} 条历史记录？关联报告也会一起删除。")
        confirm_cols = st.columns([1.5, 1.5, 3])
        if confirm_cols[0].button("确认删除", type="primary", width="stretch"):
            delete_runs_by_ids(pending_ids)
            st.session_state.pending_history_delete = []
            st.session_state.history_delete_ids = []
            st.toast("已删除选中的历史记录")
            st.rerun()
        if confirm_cols[1].button("取消删除", width="stretch"):
            st.session_state.pending_history_delete = []
            st.rerun()

    succ = [r for r in runs if r.status == "success"]
    if succ:
        st.markdown("<div class='caps' style='margin:16px 0 6px;'>查看详情</div>", unsafe_allow_html=True)
        pick = st.selectbox("详情", succ, label_visibility="collapsed", key="history_detail",
                            format_func=run_label)
        st.markdown(
            f"<div class='t24-card' style='margin-bottom:10px;'>"
            f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;'>"
            f"<span class='mono' style='font-size:12px;color:var(--t24-muted);'>"
            f"{pick.trade_date} · {html_safe(run_display_no(pick))}</span>"
            f"<span style='margin-left:auto;'>{badge(pick.decision)}</span></div>"
            f"<div class='t24-concl'>{html_safe(conclusion(pick.id))}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        with st.expander("完整报告", expanded=False):
            report_sections_inline(pick.id)


_RUN_STATE_LABEL = {
    "idle": "待运行", "queued": "排队中", "running": "运行中",
    "done": "今日已跑", "failed": "今日失败",
}
_RUN_STATE_HELP = {
    "idle": "运行今日分析", "queued": "排队中…", "running": "运行中…",
    "done": "今日已完成，点击重跑", "failed": "运行失败，点击重跑",
}


def _fmt_num(v) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)


def _threshold_summary(t) -> str:
    parts = []
    if t.upper:
        parts.append(f"上限 {_fmt_num(t.upper)}")
    if t.lower:
        parts.append(f"下限 {_fmt_num(t.lower)}")
    if t.pct_move:
        parts.append(f"涨跌 {_fmt_num(t.pct_move)}%")
    return " · ".join(parts) if parts else "未设阈值"


@st.dialog("运行今日分析")
def _batch_run_confirm(target_syms: list[str]) -> None:
    s = load_settings()
    depth = s.max_debate_rounds
    per_min = 5 if depth <= 1 else (12 if depth <= 3 else 18)
    est_min = len(target_syms) * per_min
    est_cost = len(target_syms) * 0.15
    st.markdown(f"将运行 **{len(target_syms)}** 只：{html_safe('、'.join(target_syms))}")
    st.caption(
        f"研究深度={depth} 轮 · 最多 {BATCH_MAX_CONCURRENCY} 并发 · "
        f"预计约 {est_min} 分钟、约 ${est_cost:.2f}"
    )
    if est_cost > s.cost_budget_usd_per_day:
        st.warning(f"预计成本 ${est_cost:.2f} 超过每日预算 ${s.cost_budget_usd_per_day}")
    c = st.columns(2)
    if c[0].button("开始运行", type="primary", width="stretch", key="batch_confirm_go"):
        start_batch_analysis(target_syms)
        st.toast(f"已开始运行 {len(target_syms)} 只，可在此页/总览查看进度")
        st.rerun()
    if c[1].button("取消", width="stretch", key="batch_confirm_cancel"):
        st.rerun()


@st.dialog("添加标的")
def _add_ticker_dialog(rt: str, tickers: list) -> None:
    a = st.columns([2, 2])
    sym = a[0].text_input("代码", placeholder="NVDA / 600519.SS / 0700.HK")
    name = a[1].text_input("名称（可选）")
    b = st.columns(3)
    upper = b[0].number_input("上限预警", min_value=0.0, value=0.0, step=1.0)
    lower = b[1].number_input("下限预警", min_value=0.0, value=0.0, step=1.0)
    pct = b[2].number_input("日涨跌幅% 预警", min_value=0.0, value=0.0, step=1.0)
    st.caption("填 0 表示不启用该预警阈值。")
    c = st.columns(2)
    if c[0].button("添加并校验数据源", type="primary", width="stretch", key="add_dlg_go"):
        s = normalize_new_ticker(sym)
        if not s:
            st.error("请填写代码")
            return
        if s in [t.symbol for t in tickers]:
            st.warning(f"{s} 已在列表中")
            return
        with st.spinner(f"校验 {s} 数据源…"):
            try:
                got = prices.snapshot(conn, s)
            except Exception:  # noqa: BLE001
                got = 0
        if not got:
            st.error(f"取不到 {s} 的数据，请检查代码格式（美股直接代码 / A股加 .SS/.SZ / 港股加 .HK）")
            return
        final_name = name.strip() or resolve_ticker_name(s)
        tickers.append(TickerCfg(symbol=s, name=final_name,
                                 upper=upper or None, lower=lower or None, pct_move=pct or None))
        save_watchlist(rt, tickers)
        st.toast(f"已添加 {final_name}")
        st.rerun()
    if c[1].button("取消", width="stretch", key="add_dlg_cancel"):
        st.rerun()


def _render_edit_drawer(t, rt: str, tickers: list) -> None:
    with st.container(key=f"wl_drawer_{t.symbol}"):
        with st.form(f"editform_{t.symbol}", border=False):
            name = st.text_input("名称", t.name, key=f"edn_{t.symbol}")
            b = st.columns(3)
            upper = b[0].number_input("上限预警", min_value=0.0, value=float(t.upper or 0),
                                      step=1.0, key=f"edu_{t.symbol}")
            lower = b[1].number_input("下限预警", min_value=0.0, value=float(t.lower or 0),
                                      step=1.0, key=f"edl_{t.symbol}")
            pct = b[2].number_input("日涨跌幅% 预警", min_value=0.0, value=float(t.pct_move or 0),
                                    step=1.0, key=f"edp_{t.symbol}")
            st.caption("填 0 表示不启用该预警阈值。")
            fc = st.columns([1, 1, 4])
            saved = fc[0].form_submit_button("保存", width="stretch", type="primary")
            canceled = fc[1].form_submit_button("取消", width="stretch")
        if saved:
            updated = [
                TickerCfg(
                    symbol=x.symbol,
                    name=name.strip() if x.symbol == t.symbol else x.name,
                    upper=(upper or None) if x.symbol == t.symbol else x.upper,
                    lower=(lower or None) if x.symbol == t.symbol else x.lower,
                    pct_move=(pct or None) if x.symbol == t.symbol else x.pct_move,
                )
                for x in tickers
            ]
            save_watchlist(rt, updated)
            st.session_state.editing_symbol = None
            st.toast(f"已更新 {t.symbol}")
            st.rerun()
        if canceled:
            st.session_state.editing_symbol = None
            st.rerun()


def _render_watchlist_rows(rt: str, tickers: list) -> None:
    with st.container(key="t24_watchlist"):
        for t in reversed(tickers):  # 最新加入的标的排最上（FILO），与总览一致
            stt = today_run_state(t.symbol)
            cols = st.columns([0.5, 8.0, 0.7, 0.7], gap="small", vertical_alignment="center")
            if cols[0].button("", key=f"runstate-{stt}-{t.symbol}",
                              disabled=stt in {"running", "queued"},
                              help=_RUN_STATE_HELP.get(stt)):
                start_batch_analysis([t.symbol])
                st.rerun()
            cols[1].markdown(
                f"<div class='t24-wl-row'>"
                f"<span class='t24-wl-name'>{html_safe(t.name or t.symbol)}</span>"
                f"<span class='t24-wl-code'>{html_safe(t.symbol)}</span>"
                f"<span class='t24-wl-th'>{html_safe(_threshold_summary(t))}</span>"
                f"<span class='t24-today-badge {stt}'>{_RUN_STATE_LABEL.get(stt, stt)}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if cols[2].button("", key=f"wledit-{t.symbol}", help="编辑"):
                st.session_state.editing_symbol = (
                    None if st.session_state.editing_symbol == t.symbol else t.symbol
                )
                st.session_state.pending_delete = None
                st.rerun()
            if cols[3].button("", key=f"wldel-{t.symbol}", help="删除"):
                st.session_state.pending_delete = (
                    None if st.session_state.pending_delete == t.symbol else t.symbol
                )
                st.session_state.editing_symbol = None
                st.rerun()
            if st.session_state.editing_symbol == t.symbol:
                _render_edit_drawer(t, rt, tickers)
            if st.session_state.pending_delete == t.symbol:
                with st.container(key=f"wl_drawer_{t.symbol}"):
                    st.warning(f"确认删除 {t.name or t.symbol}？此操作不可撤销。")
                    dc = st.columns([1, 1, 4])
                    if dc[0].button("确认删除", type="primary", width="stretch",
                                    key=f"wldelok-{t.symbol}"):
                        save_watchlist(rt, [x for x in tickers if x.symbol != t.symbol])
                        st.session_state.pending_delete = None
                        st.toast(f"已删除 {t.symbol}")
                        st.rerun()
                    if dc[1].button("取消", width="stretch", key=f"wldelcancel-{t.symbol}"):
                        st.session_state.pending_delete = None
                        st.rerun()


@st.fragment(run_every=5)
def _watchlist_fragment(rt: str, tickers: list) -> None:
    _render_watchlist_rows(rt, tickers)
    still = any(today_run_state(t.symbol) in {"running", "queued"} for t in tickers)
    if not still:
        st.rerun()


def manage():
    rt, tickers = load_watchlist()
    st.markdown(
        f"<div class='t24-page-title'>{icon('manage', 23)}<span>管理关注列表</span></div>"
        "<div class='t24-muted'>维护每日分析标的、运行时间和预警阈值。</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f"<div class='t24-muted'>每日运行时间 <b>{html_safe(rt)}</b>"
        " · 在右上角「设置 → 运行与成本」修改。</div>",
        unsafe_allow_html=True,
    )

    if tickers:
        states = [today_run_state(t.symbol) for t in tickers]
        done = sum(1 for s in states if s == "done")
        remaining = [t.symbol for t, s in zip(tickers, states) if s in {"idle", "failed"}]
    else:
        states, done, remaining = [], 0, []

    bar = st.columns([2.3, 1.4, 1.1], vertical_alignment="center")
    bar[0].markdown(
        f"<div class='t24-wl-summary'>今日分析 · <b>{done}</b> / {len(tickers)} 已完成</div>",
        unsafe_allow_html=True,
    )
    if remaining:
        if bar[1].button(f"运行今日剩余（{len(remaining)}）", type="primary",
                         width="stretch", key="wl_run_remaining"):
            _batch_run_confirm(remaining)
    else:
        bar[1].markdown(
            "<div class='t24-muted' style='text-align:center;font-size:13px;'>今日已全部完成 ✓</div>",
            unsafe_allow_html=True,
        )
    if bar[2].button("＋ 添加标的", width="stretch", key="wl_add"):
        _add_ticker_dialog(rt, tickers)

    if not tickers:
        st.info("暂无标的，点「添加标的」开始。")
        return

    busy = bool(st.session_state.editing_symbol or st.session_state.pending_delete)
    active = any(s in {"running", "queued"} for s in states)
    if active and not busy:
        _watchlist_fragment(rt, tickers)
    else:
        _render_watchlist_rows(rt, tickers)

def _save_ai_thread(thread) -> None:
    """落库；失败不打断对话（下次成功保存会补上）。"""
    try:
        thread["id"] = db.upsert_ai_thread(conn, thread)
    except Exception:  # noqa: BLE001
        pass


def _new_ai_thread(title: str = "新对话", run_override: list[int] | None = None):
    thread = {
        "title": title,
        "pinned_syms": [],          # @ 引用后钉进会话的标的，对后续轮次持续生效
        "pinned_run_ids": [],       # @ 引用的具体决策（run 级），对后续轮次持续生效
        "excluded_syms": [],        # 从跟随页面基础范围移除的标的
        "run_override": [int(x) for x in (run_override or [])],  # 「发给 AI」固定记录
        "messages": [],
    }
    thread["id"] = db.upsert_ai_thread(conn, thread)
    st.session_state.ai_threads.insert(0, thread)
    st.session_state.ai_active_thread = thread["id"]
    st.session_state.ai_view = "chat"
    return thread


def _normalize_ai_thread(t):
    t.setdefault("pinned_syms", [])
    t.setdefault("pinned_run_ids", [])
    t.setdefault("excluded_syms", [])
    t.setdefault("run_override", [])
    return t


def active_ai_thread():
    for t in st.session_state.ai_threads:
        if t["id"] == st.session_state.ai_active_thread:
            return _normalize_ai_thread(t)
    if st.session_state.ai_threads:
        st.session_state.ai_active_thread = st.session_state.ai_threads[0]["id"]
        return _normalize_ai_thread(st.session_state.ai_threads[0])
    return _new_ai_thread()


def _latest_success_run_id(sym: str) -> int | None:
    r = latest.get(sym)
    if r and r.status == "success" and r.id:
        return int(r.id)
    return None


def thread_scope_syms(thread) -> list[str]:
    """当前生效的上下文标的 = (跟随页面基础范围 − 移除项) + @ 钉住项。"""
    detail = st.session_state.get("detail")
    base = [detail] if detail and detail in symbols else list(symbols)
    excluded = set(thread.get("excluded_syms") or [])
    effective = [s for s in base if s not in excluded]
    for s in (thread.get("pinned_syms") or []):
        if s in symbols and s not in effective:
            effective.append(s)
    return effective


def thread_run_ids(thread) -> list[int]:
    if thread.get("run_override"):
        return list(thread["run_override"])
    ids = []
    for s in thread_scope_syms(thread):
        rid = _latest_success_run_id(s)
        if rid is not None:
            ids.append(rid)
    # @ 引用的具体决策（run 级）追加进上下文
    for rid in (thread.get("pinned_run_ids") or []):
        if rid not in ids and run_by_id(rid):
            ids.append(rid)
    return ids


def thread_scope_desc(thread) -> str:
    if thread.get("run_override"):
        return f"固定记录 · {len(thread['run_override'])} 条"
    detail = st.session_state.get("detail")
    base = [detail] if detail and detail in symbols else list(symbols)
    excluded = [s for s in (thread.get("excluded_syms") or []) if s in base]
    pinned = [s for s in (thread.get("pinned_syms") or []) if s in symbols and s not in base]
    label = (names.get(detail, detail) if detail and detail in symbols
             else f"全部 {len(symbols)} 只")
    if pinned:
        label += f" +{len(pinned)}"
    if excluded:
        label += f" −{len(excluded)}"
    return label


def _ai_mention_options(thread) -> list[dict]:
    """@ 菜单数据：本页标的 > 范围内 > 其余；组内按分析时间从新到旧。"""
    detail = st.session_state.get("detail")
    scope_now = set(thread_scope_syms(thread)) if not thread.get("run_override") else set()
    rows = []
    for s in symbols:
        r = latest.get(s)
        if not (r and r.status == "success" and r.id):
            continue
        rank = 0 if s == detail else (1 if s in scope_now else 2)
        tag = "本页" if s == detail else ("范围内" if s in scope_now else "")
        ts = run_timestamp(r)
        # 该标的的历史决策（二级菜单，可多选），最新在前，最多 10 条
        runs_list = [
            {"date": x.trade_date,
             "decision": BADGE.get(_norm_decision(x.decision), ("", "—"))[1],
             "age": run_age(x), "latest": x.id == r.id}
            for x in db.get_runs(conn, ticker=s, limit=30)
            if x.status == "success"
        ][:10]
        rows.append((rank, -(ts.timestamp() if ts else 0), {
            "sym": s,
            "name": names.get(s, s),
            "decision": BADGE.get(_norm_decision(r.decision), ("", "—"))[1],
            "age": run_age(r),
            "tag": tag,
            "runs": runs_list,
        }))
    rows.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in rows]


def _parse_ai_mentions(text: str) -> list[str]:
    """从文本解析 @代码 / @中文名 引用。"""
    found = []
    for s in symbols:
        nm = names.get(s, s)
        if re.search("@" + re.escape(s) + r"(?![A-Za-z0-9.])", text, re.I) or f"@{nm}" in text:
            found.append(s)
    return found


def _parse_ai_run_mentions(text: str) -> dict[str, list[int]]:
    """解析 @代码(yyyy-mm-dd) 形式的具体决策引用 → {sym: [run_id, ...]}。"""
    out: dict[str, list[int]] = {}
    for s in symbols:
        for m in re.finditer("@" + re.escape(s) + r"\((\d{4}-\d{2}-\d{2})\)", text, re.I):
            runs_ = [x for x in db.get_runs(conn, ticker=s, limit=100)
                     if x.status == "success" and x.trade_date == m.group(1)]
            if runs_:
                out.setdefault(s, [])
                if runs_[0].id not in out[s]:
                    out[s].append(int(runs_[0].id))
    return out


def _queue_ai(prompt: str, thread) -> None:
    """入队一条提问：追加用户消息并标记 pending，流式回答在下一次渲染时进行。"""
    prompt = (prompt or "").strip()
    if not prompt or st.session_state.ai_pending:
        return
    mention_labels = []
    if not thread.get("run_override"):
        current = set(thread_scope_syms(thread))
        run_mentions = _parse_ai_run_mentions(prompt)
        # 指定日期的决策引用：按 run 钉住（不连带钉整个标的，避免混入最新决策）
        for s, rids in run_mentions.items():
            pinned_runs = thread.setdefault("pinned_run_ids", [])
            for rid in rids:
                if rid not in pinned_runs:
                    pinned_runs.append(rid)
                run_ = run_by_id(rid)
                if run_:
                    mention_labels.append(f"@ {names.get(s, s)} · {run_.trade_date}")
        for s in _parse_ai_mentions(prompt):
            if s in run_mentions:
                continue  # 该标的已按具体决策引用
            # @ 过的标的钉进会话，对后续轮次持续生效；同时解除此前的移除
            if s not in thread["pinned_syms"]:
                thread["pinned_syms"].append(s)
            thread["excluded_syms"] = [x for x in thread["excluded_syms"] if x != s]
            if s not in current:
                mention_labels.append(f"@ {names.get(s, s)}")
    if not _set_ai_pending(prompt, thread):
        return
    thread["messages"].append({"role": "user", "content": prompt, "attachments": mention_labels,
                               "ts": datetime.now(timezone.utc).isoformat()})
    if thread["title"] == "新对话":
        thread["title"] = prompt[:14] + ("…" if len(prompt) > 14 else "")
    _save_ai_thread(thread)


def _set_ai_pending(prompt: str, thread) -> bool:
    """计算本轮上下文并标记 pending。上下文为空时写入提示消息并返回 False。"""
    run_ids = thread_run_ids(thread)
    if not run_ids:
        thread["messages"].append({
            "role": "assistant", "ok": False, "attachments": [],
            "ts": datetime.now(timezone.utc).isoformat(),
            "content": "还没有可用的分析记录。先在关注列表跑一次分析，我就能基于结果回答了。"})
        _save_ai_thread(thread)
        return False
    detail = st.session_state.get("detail")
    focus = [s for s in _parse_ai_mentions(prompt) if s in symbols]
    if detail and detail in symbols and detail not in focus:
        focus.append(detail)
    focus.extend(s for s in thread.get("pinned_syms") or [] if s not in focus)
    labels = [full_run_label(r) for r in selected_ai_runs(run_ids)]
    st.session_state.ai_pending = {"thread_id": thread["id"], "prompt": prompt,
                                   "run_ids": run_ids, "labels": labels,
                                   "focus_syms": focus}
    return True


def _note_page_switch(thread) -> None:
    """跟随页面的会话在页面切换时插入一条系统提示，让上下文变化可见。"""
    if thread.get("run_override"):
        return
    cur = st.session_state.get("detail")
    cur = cur if cur in symbols else None
    if "_page" not in thread:
        thread["_page"] = cur
        return
    if thread["_page"] == cur:
        return
    thread["_page"] = cur
    if thread["messages"]:
        label = names.get(cur, cur) if cur else f"全部关注列表 {len(symbols)} 只"
        thread["messages"].append({"role": "system", "content": f"上下文已跟随页面切换：{label}"})
        _save_ai_thread(thread)


def _msg_time_label(ts: str | None) -> str:
    """消息时间：当天只显 HH:MM，跨天带月日。"""
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(ts).astimezone()
    except ValueError:
        return ""
    fmt = "%H:%M" if t.date() == datetime.now().astimezone().date() else "%m-%d %H:%M"
    return t.strftime(fmt)


def _render_ai_message(msg) -> None:
    if msg["role"] == "system":
        st.markdown(f"<div class='t24-ai-sysnote'>{html_safe(msg['content'])}</div>",
                    unsafe_allow_html=True)
        return
    role_label = "你" if msg["role"] == "user" else "AI"
    css_role = "user" if msg["role"] == "user" else "assistant"
    body = (html_safe(msg["content"]).replace(chr(10), "<br>") if msg["role"] == "user"
            else report_text_html(msg["content"]))
    chips = "".join(
        f"<span class='t24-ai-chip'>{html_safe(lb)}</span>"
        for lb in (msg.get("attachments") or [])[:4]
    )
    chips_html = f"<div class='t24-ai-chips'>{chips}</div>" if chips else ""
    tlabel = _msg_time_label(msg.get("ts"))
    meta_left = role_label + (f" · {tlabel}" if tlabel else "")
    copy_btn = ("<button class='t24-ai-copy' type='button'>复制</button>"
                if msg["role"] == "assistant" else "")
    st.markdown(
        f"<div class='t24-ai-msg {css_role}'>"
        f"<div class='t24-ai-meta'><span>{html_safe(meta_left)}</span>{copy_btn}</div>"
        f"<div class='t24-ai-body'>{body}</div>{chips_html}</div>",
        unsafe_allow_html=True,
    )


def _render_ai_threads_view() -> None:
    if st.button("＋ 新对话", key="ai_new_thread_btn", type="primary", width="stretch"):
        _new_ai_thread()
        st.rerun()
    for t in st.session_state.ai_threads:
        cols = st.columns([5, 0.9], vertical_alignment="center")
        n = len(t["messages"])
        active = "▸ " if t["id"] == st.session_state.ai_active_thread else ""
        if cols[0].button(f"{active}{t['title']} · {thread_scope_desc(t)} · {n} 条",
                          key=f"ai_thread_{t['id']}", width="stretch"):
            st.session_state.ai_active_thread = t["id"]
            st.session_state.ai_view = "chat"
            st.rerun()
        if cols[1].button("✕", key=f"ai_thread_del_{t['id']}"):
            st.session_state.ai_threads = [x for x in st.session_state.ai_threads if x["id"] != t["id"]]
            if st.session_state.ai_active_thread == t["id"]:
                st.session_state.ai_active_thread = None
            try:
                db.delete_ai_thread(conn, int(t["id"]))
            except Exception:  # noqa: BLE001
                pass
            st.rerun()


def _render_ai_scope_section(thread) -> None:
    arrow = " ▴" if st.session_state.ai_scope_open else " ▾"
    if st.button(thread_scope_desc(thread) + arrow, key="ai_scope_chip"):
        st.session_state.ai_scope_open = not st.session_state.ai_scope_open
        st.rerun()
    if not st.session_state.ai_scope_open:
        return
    with st.container(key="t24_ai_scope_panel"):
        if thread.get("run_override"):
            st.caption("本会话固定引用以下记录（来自「发给 AI」）：")
            for r in selected_ai_runs(thread["run_override"]):
                st.caption(f"· {full_run_label(r)}")
            if st.button("清除，改用自动范围", key=f"ai_clear_override_{thread['id']}", width="stretch"):
                thread["run_override"] = []
                _save_ai_thread(thread)
                st.rerun()
            return
        detail = st.session_state.get("detail")
        base = [detail] if detail and detail in symbols else list(symbols)
        effective = thread_scope_syms(thread)
        st.caption("本会话的上下文 = 跟随页面 + @ 引用。点 ✕ 移除，输入 @ 添加。")
        for s in effective:
            cols = st.columns([6, 0.9], vertical_alignment="center")
            origin = "本页" if s == detail else ("关注列表" if s in base else "@ 引用")
            cols[0].markdown(
                f"<div class='t24-ai-ctx-row'><b>{html_safe(names.get(s, s))}</b>"
                f" · {html_safe(s)} · {html_safe(origin)} · {html_safe(run_age(latest.get(s)))}</div>",
                unsafe_allow_html=True)
            if cols[1].button("✕", key=f"ai_ctx_rm_{thread['id']}_{s}"):
                if s in thread["pinned_syms"]:
                    thread["pinned_syms"] = [x for x in thread["pinned_syms"] if x != s]
                else:
                    thread["excluded_syms"].append(s)
                _save_ai_thread(thread)
                st.rerun()
        # @ 引用的具体历史决策（run 级，跨页面持续生效）——必须可见、可移除
        pinned_runs = [(rid, run_by_id(rid)) for rid in (thread.get("pinned_run_ids") or [])]
        pinned_runs = [(rid, r) for rid, r in pinned_runs if r]
        if pinned_runs:
            st.caption("@ 引用的历史决策（跨页面持续生效）：")
            for rid, r in pinned_runs:
                cols = st.columns([6, 0.9], vertical_alignment="center")
                cols[0].markdown(
                    f"<div class='t24-ai-ctx-row'><b>{html_safe(names.get(r.ticker, r.ticker))}</b>"
                    f" · {html_safe(r.ticker)} · {html_safe(r.trade_date)}"
                    f" · {html_safe(decision_label(r.decision))} · {html_safe(run_display_no(r))}</div>",
                    unsafe_allow_html=True)
                if cols[1].button("✕", key=f"ai_ctx_rm_run_{thread['id']}_{rid}"):
                    thread["pinned_run_ids"] = [x for x in thread["pinned_run_ids"] if x != rid]
                    _save_ai_thread(thread)
                    st.rerun()
        removed = [s for s in thread["excluded_syms"] if s in base]
        if removed:
            st.caption("已移除：" + "、".join(names.get(s, s) for s in removed))
            if st.button("全部恢复", key=f"ai_ctx_restore_{thread['id']}", width="stretch"):
                thread["excluded_syms"] = []
                _save_ai_thread(thread)
                st.rerun()
        if not effective:
            st.caption("上下文为空——恢复移除项，或在输入框用 @ 添加标的。")


def _stream_pending_answer(thread, pending) -> None:
    """流式生成回答并落库到会话消息。"""
    # 历史截到最近 12 条（约 6 轮），控制多轮对话的 token 体积；system 提示不进历史
    history = [m for m in thread["messages"][:-1] if m.get("role") in ("user", "assistant")][-12:]
    status = {"ok": True}

    def _gen():
        try:
            yield from stream_ai(
                pending["prompt"],
                build_ai_context(pending["run_ids"], pending.get("focus_syms")),
                history,
            )
        except RuntimeError as exc:
            status["ok"] = False
            yield str(exc)
        except Exception as exc:  # noqa: BLE001
            status["ok"] = False
            yield f"AI 请求失败：{type(exc).__name__}。请检查模型密钥、网络和 settings.yaml 的模型配置。"

    # 先占位:流式气泡 + 灰置的输入框壳,避免生成期间输入区消失导致跳动
    stream_box = st.container(key="t24_ai_streaming")
    st.markdown("<div class='t24-ai-composer-ghost'>AI 正在回复…</div>", unsafe_allow_html=True)
    with stream_box:
        st.markdown("<div class='t24-ai-meta'>AI</div>", unsafe_allow_html=True)
        full = st.write_stream(_gen)
    if isinstance(full, list):
        full = "".join(str(x) for x in full)
    thread["messages"].append({
        "role": "assistant", "content": str(full or "").strip(), "ok": status["ok"],
        "attachments": pending.get("labels", [])[:3],
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    st.session_state.ai_pending = None
    _save_ai_thread(thread)
    st.rerun()


def render_ai_plugin():
    with st.container(key="t24_ai_launcher"):
        launcher_type = "secondary" if st.session_state.ai_panel_open else "primary"
        if st.button("AI 助手", key="toggle_ai_panel", type=launcher_type,
                     width="stretch"):
            st.session_state.ai_panel_open = not st.session_state.ai_panel_open
            st.rerun()

    # 「发给 AI」交接：转成一条固定记录的新会话
    handoff = [int(x) for x in st.session_state.ai_selected_run_ids if str(x).isdigit()]
    if handoff:
        st.session_state.ai_selected_run_ids = []
        _new_ai_thread(title=f"选中的 {len(handoff)} 条记录", run_override=handoff)

    if not st.session_state.ai_panel_open:
        return

    # 点击面板外任意位置 → 关闭（等价于点 ✕）。iframe 内(输入框/@菜单)的点击不冒泡，天然豁免。
    # 注意：监听脚本必须注入父页面 realm——components.html 的 iframe 每次 rerun 都会
    # 重建，在 iframe 里 addEventListener 的函数会随之失效。
    components.html(
        """<script>
        (() => {
          const doc = window.parent.document;
          if (doc.getElementById("t24-ai-outside-script")) return;
          const s = doc.createElement("script");
          s.id = "t24-ai-outside-script";
          s.textContent = `(() => {
            if (window.__t24AiClickOutside) return;
            window.__t24AiClickOutside = true;
            document.addEventListener("mousedown", (e) => {
              const panel = document.querySelector(".st-key-t24_ai_panel");
              if (!panel) return;
              const t = e.target;
              if (t.closest(".st-key-t24_ai_panel")) return;
              if (t.closest(".st-key-t24_ai_launcher")) return;
              if (t.closest("[data-testid='stDialog']")) return;
              const btn = document.querySelector(".st-key-ai_close button");
              if (btn) btn.click();
            }, true);
          })();`;
          doc.body.appendChild(s);
          if (!doc.getElementById("t24-ai-copy-script")) {
            const c = doc.createElement("script");
            c.id = "t24-ai-copy-script";
            c.textContent = `(() => {
              if (window.__t24AiCopy) return;
              window.__t24AiCopy = true;
              document.addEventListener("click", (e) => {
                const btn = e.target.closest(".t24-ai-copy");
                if (!btn) return;
                e.preventDefault(); e.stopPropagation();
                const body = btn.closest(".t24-ai-msg")?.querySelector(".t24-ai-body");
                if (!body) return;
                navigator.clipboard.writeText(body.innerText).then(() => {
                  btn.textContent = "已复制";
                  setTimeout(() => { btn.textContent = "复制"; }, 1200);
                }).catch(() => {
                  btn.textContent = "复制失败";
                  setTimeout(() => { btn.textContent = "复制"; }, 1200);
                });
              }, true);
            })();`;
            doc.body.appendChild(c);
          }
        })();
        </script>""",
        height=0,
    )

    thread = active_ai_thread()
    pending = st.session_state.ai_pending
    if pending and pending.get("thread_id") != thread["id"]:
        pending = None  # 只在所属会话里续流

    with st.container(key="t24_ai_panel"):
        # 头部：会话列表 / 标题+范围 / 新对话 / 关闭
        with st.container(key="t24_ai_header"):
            hc = st.columns([0.62, 3.2, 0.62, 0.62], vertical_alignment="center")
            threads_open = st.session_state.ai_view == "threads"
            if hc[0].button("☰", key="ai_threads_btn",
                            help="返回对话" if threads_open else "会话列表"):
                st.session_state.ai_view = "chat" if threads_open else "threads"
                st.rerun()
            title = "会话" if threads_open else thread["title"]
            hc[1].markdown(f"<div class='t24-ai-title'>{html_safe(title)}</div>",
                           unsafe_allow_html=True)
            if hc[2].button("＋", key="ai_new_chat", help="新对话"):
                _new_ai_thread()
                st.rerun()
            if hc[3].button("✕", key="ai_close", help="关闭"):
                st.session_state.ai_panel_open = False
                st.rerun()

        if st.session_state.ai_view == "threads":
            _render_ai_threads_view()
            return

        with st.container(key="t24_ai_scope_row"):
            _render_ai_scope_section(thread)

        # 页面切换时提示上下文变化
        _note_page_switch(thread)

        # 消息流
        for msg in thread["messages"][-16:]:
            _render_ai_message(msg)

        # 空会话:建议问题
        if not thread["messages"] and not pending:
            detail = st.session_state.get("detail")
            if detail:
                nm = names.get(detail, detail)
                sugg = [f"{nm} 现在的核心风险是什么？", f"{nm} 的多空分歧在哪？", "给我一句话操作建议"]
            else:
                sugg = ["今天最该关注哪只？为什么？", "这些标的里谁的风险最高？", "把各只的决策和理由汇总成一段"]
            st.markdown("<div class='t24-muted' style='margin:6px 0 4px;'>试试问我：</div>",
                        unsafe_allow_html=True)
            for i, q in enumerate(sugg):
                if st.button(q, key=f"ai_sugg_{i}", width="stretch"):
                    _queue_ai(q, thread)
                    st.rerun()

        # 失败重试:最后一条是失败回答时给一个重试按钮
        msgs = thread["messages"]
        if (not pending and len(msgs) >= 2 and msgs[-1]["role"] == "assistant"
                and not msgs[-1].get("ok", True) and msgs[-2]["role"] == "user"):
            if st.button("重试上一条", key=f"ai_retry_{thread['id']}_{len(msgs)}", width="stretch"):
                msgs.pop()
                if _set_ai_pending(msgs[-1]["content"], thread):
                    _save_ai_thread(thread)
                    st.rerun()

        # 流式回答:同步生成,期间用灰置输入框壳占位,完成后 rerun 恢复真输入框
        if pending:
            _stream_pending_answer(thread, pending)

        # 输入组件(@ 引用菜单在组件内)
        result = _AI_COMPOSER(
            stocks=_ai_mention_options(thread),
            theme={
                "surface": ACTIVE_THEME["surface"],
                "text": ACTIVE_THEME["text"],
                "muted": ACTIVE_THEME["muted"],
                "subtle": ACTIVE_THEME["subtle"],
                "border": ACTIVE_THEME["border"],
                "border_strong": ACTIVE_THEME["border_strong"],
                "primary": ACTIVE_THEME["primary"],
                "primary_text": ACTIVE_THEME["primary_text"],
            },
            placeholder="问我任何问题，输入 @ 可引用其他标的数据…",
            disabled=bool(st.session_state.ai_pending),
            key="ai_composer",
            default=None,
        )
        if (result and isinstance(result, dict) and result.get("ts")
                and result["ts"] != st.session_state.ai_composer_ts
                and not st.session_state.ai_pending):
            st.session_state.ai_composer_ts = result["ts"]
            _queue_ai(str(result.get("text", "")), thread)
            st.rerun()


def review():
    """决策复盘 · 胜率：每个 AI 决策当一个信号，看 7天/30天后方向押对没有。"""
    from trading24 import winrate

    # 跟踪切换（点表格里的圆点，?track_toggle=<run_id>）
    _tg = st.query_params.get("track_toggle")
    if _tg:
        del st.query_params["track_toggle"]
        try:
            _run = db.get_run(conn, int(_tg))
        except (TypeError, ValueError):
            _run = None
        if _run:
            db.set_run_tracked(conn, int(_run.id), not bool(getattr(_run, "win_tracked", 1)))
        st.rerun()

    signals, summary = winrate.evaluate(conn)

    def _pct(x):
        return "—" if x is None else f"{x * 100:+.1f}%"

    st.markdown("<div class='t24-subhead'>决策复盘 · 胜率</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='t24-muted'>把每个 AI 决策当作一个信号，看它 7 / 30 / 90 天后方向押对没有："
        "增持押涨、减持押跌算对；中性不计胜负；未到期为待定。评级视野多为季度级，"
        "90 天窗才是对评级最公平的判定，7 天窗仅衡量短期择时。"
        "若报告给出硬止损且期间被触发，该窗直接判「破止损」（计划失败，计负），不再等到期。"
        "一只股票的多次决策各算一个信号。</div>",
        unsafe_allow_html=True)

    for h in winrate.HORIZONS:
        a = summary[h]["all"]
        b_buy, b_sell = summary[h]["BUY"], summary[h]["SELL"]
        wr = "—" if a["win_rate"] is None else f"{a['win_rate'] * 100:.0f}%"
        buy_wr = "—" if b_buy["win_rate"] is None else f"{b_buy['win_rate'] * 100:.0f}%"
        sell_wr = "—" if b_sell["win_rate"] is None else f"{b_sell['win_rate'] * 100:.0f}%"
        st.markdown(f"<div class='t24-subhead' style='margin-top:14px'>{h} 天窗</div>",
                    unsafe_allow_html=True)
        cols = st.columns(4)
        kpi_card(cols[0], "总胜率", wr, f"已判定 {a['n_decided']} · 待定 {a['pending']}")
        kpi_card(cols[1], "胜 / 负 / 平", f"{a['wins']} / {a['losses']} / {a['flats']}",
                 f"平均方向收益 {_pct(a['avg_dir_ret'])}")
        kpi_card(cols[2], "增持胜率", buy_wr, f"押对 {b_buy['wins']}/{b_buy['n_decided']}")
        kpi_card(cols[3], "减持胜率", sell_wr, f"押对 {b_sell['wins']}/{b_sell['n_decided']}")

    st.markdown("<div class='t24-subhead' style='margin-top:18px'>逐决策明细</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='t24-muted'>点「跟踪」圆点 = 切换该决策是否计入上方胜率（记录保留，随时可切回）。"
        "点<b>表头</b>可排序/筛选（标的、决策、判定列支持按值勾选）。"
        "入场价取决策日收盘，遇休市顺延（悬停入场价可见实际取价日）。</div>",
        unsafe_allow_html=True)
    if not signals:
        st.info("还没有成功的决策记录。先在关注列表里跑一次分析。")
        return

    ordered = sorted(signals, key=lambda s: (s.trade_date, s.run_id or 0), reverse=True)

    _TICKER_COLORS = ["#10b981", "#3b82f6", "#f59e0b", "#8b5cf6",
                      "#f43f5e", "#06b6d4", "#84cc16", "#fb923c"]
    _VERDICT_CLS = {"胜": "win", "负": "loss", "破止损": "loss",
                    "平": "flat", "中性": "flat", "待定": "pending"}

    def _ret_html(v) -> str:
        if v is None:
            return "<span class='dim'>—</span>"
        cls = "pos" if v > 0 else ("neg" if v < 0 else "dim")
        return f"<span class='{cls}'>{v * 100:+.1f}%</span>"

    body = ""
    for s in ordered:
        tcolor = (_TICKER_COLORS[symbols.index(s.ticker) % len(_TICKER_COLORS)]
                  if s.ticker in symbols else "var(--t24-subtle)")
        if s.plan_entry_low is not None or s.plan_entry_high is not None:
            zlo = s.plan_entry_low if s.plan_entry_low is not None else s.plan_entry_high
            zhi = s.plan_entry_high if s.plan_entry_high is not None else zlo
            zone = _plan_num(zlo) if zlo == zhi else f"{_plan_num(zlo)}–{_plan_num(zhi)}"
        else:
            zone = "<span class='dim'>—</span>"
        stop = f"{s.stop_price:.0f}" if s.stop_price is not None else "<span class='dim'>—</span>"
        last = f"{s.last_price:.2f}" if s.last_price is not None else "<span class='dim'>—</span>"
        entry_title = (f" title='入场日 {s.entry_date}'"
                       if s.entry_date and s.entry_date != s.trade_date else "")
        entry = f"{s.entry_price:.2f}" if s.entry_price is not None else "<span class='dim'>—</span>"
        def _ds(v):
            return "" if v is None else f"{v}"

        win_cells = ""
        for h in winrate.HORIZONS:
            hr = s.horizons[h]
            tip = (f" title='硬止损 {_plan_num(s.stop_price)} 已于 {s.stop_hit_date} 触发，"
                   f"计划终止，各窗口定格为破止损'"
                   if hr.verdict == "破止损" and s.stop_hit_date else "")
            chip = (f"<span class='chip {_VERDICT_CLS.get(hr.verdict, 'flat')}'{tip}>"
                    f"{hr.verdict}</span>")
            ret = f"<span class='ret'>{_ret_html(hr.ret)}</span>" if hr.ret is not None else ""
            win_cells += (f"<td data-sort='{_ds(hr.ret)}' data-val='{hr.verdict}'>"
                          f"<div class='wcell'>{chip}{ret}</div></td>")
        body += (
            f"<tr class='{'' if s.tracked else 'untracked'}'>"
            f"<td><a class='track{' on' if s.tracked else ''}' target='_self' "
            f"data-rid='{s.run_id}' "
            f"href='?p=review&track_toggle={s.run_id}' title='点击切换是否计入胜率'>"
            f"{'✓' if s.tracked else ''}</a></td>"
            f"<td data-sort='{html_safe(s.ticker)}' data-val='{html_safe(s.ticker)}'>"
            f"<span class='dot' style='background:{tcolor};'></span>"
            f"<span class='sym'>{html_safe(s.ticker)}</span></td>"
            f"<td data-sort='{html_safe(s.trade_date)}'>{html_safe(s.trade_date)}</td>"
            f"<td data-sort='{s.dir_label}' data-val='{s.dir_label}'>{badge(s.decision_raw)}</td>"
            f"<td data-sort='{_ds(s.last_price)}'>{last}</td>"
            f"<td data-sort='{_ds(s.entry_price)}'{entry_title}>{entry}</td>"
            f"<td data-sort='{_ds(s.plan_entry_low)}'>{zone}</td>"
            f"<td data-sort='{_ds(s.stop_price)}'>{stop}</td>"
            f"<td data-sort='{_ds(s.live_ret)}'>{_ret_html(s.live_ret)}</td>"
            f"{win_cells}</tr>")
    header = "".join(f"<th>{h}</th>" for h in
                     ["跟踪", "标的", "决策日", "决策", "最新价", "入场价",
                      "建/减仓区", "止损", "至今", "7天", "30天", "90天"])
    # 列宽管理（table-layout:fixed）：窄列给操作/日期，宽列给三个时间窗
    colgroup = "<colgroup>" + "".join(
        f"<col style='width:{w}%'>" for w in
        (4.5, 9.5, 8.5, 7, 8, 8, 9.5, 6, 8, 10.3, 10.3, 10.4)) + "</colgroup>"
    st.markdown(
        f"<div class='t24-rt-wrap'><table class='t24-rt'>{colgroup}"
        f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)

    # 跟踪切换的软刷新通道：隐藏按钮（局部重跑），表格圆点由 JS 代理点击；
    # JS 不可用时退回 href 的整页刷新兜底。
    with st.container(key="t24_review_track_btns"):
        for s in signals:
            if s.run_id is None:
                continue
            if st.button(f"t{s.run_id}", key=f"track_btn_{s.run_id}"):
                db.set_run_tracked(conn, int(s.run_id), not s.tracked)
                st.rerun()
    components.html(
        """<script>
        (() => {
          const doc = window.parent.document;
          if (!doc.getElementById("t24-track-script")) {
            const s = doc.createElement("script");
            s.id = "t24-track-script";
            s.textContent = `(() => {
              if (window.__t24TrackDelegate) return;
              window.__t24TrackDelegate = true;
              document.addEventListener("click", (e) => {
                const a = e.target.closest(".t24-rt a.track");
                if (!a) return;
                const btn = document.querySelector(
                  ".st-key-track_btn_" + a.getAttribute("data-rid") + " button");
                if (btn) { e.preventDefault(); e.stopPropagation(); btn.click(); }
              }, true);
            })();`;
            doc.body.appendChild(s);
          }
          if (!doc.getElementById("t24-rt-head-script")) {
            const s2 = doc.createElement("script");
            s2.id = "t24-rt-head-script";
            s2.textContent = `(() => {
              if (window.__t24RtHead) return;
              window.__t24RtHead = true;
              const KEY = "t24rtState";
              const FILTERABLE = new Set([1, 3, 9, 10, 11]);  // 标的/决策/7天/30天/90天
              let state = {};
              try { state = JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch (e) {}
              let menu = null;
              let mo = null;
              const table = () => document.querySelector(".t24-rt");
              const save = () => sessionStorage.setItem(KEY, JSON.stringify(state));
              const val = (tr, c) => { const td = tr.children[c]; if (!td) return "";
                const d = td.getAttribute("data-sort"); return d !== null ? d : td.textContent.trim(); };
              const disp = (tr, c) => { const td = tr.children[c]; if (!td) return "";
                return (td.getAttribute("data-val") || td.textContent).trim(); };

              function apply() {
                const t = table(); if (!t || !t.tBodies[0]) return;
                if (mo) mo.disconnect();   // 自己引发的 DOM 变化不要再触发自己(否则每帧重排,点击失效)
                const rows = Array.from(t.tBodies[0].rows);
                const filters = state.filters || {};
                rows.forEach(r => {
                  let show = true;
                  for (const c in filters) {
                    const allow = filters[c];
                    if (allow && allow.length && !allow.includes(disp(r, +c))) { show = false; break; }
                  }
                  r.style.display = show ? "" : "none";
                });
                if (state.sort) {
                  const { col, dir } = state.sort, m = dir === "asc" ? 1 : -1;
                  rows.sort((a, b) => {
                    const va = val(a, col), vb = val(b, col);
                    if (va === "" && vb === "") return 0;
                    if (va === "") return 1;            // 空值永远最后
                    if (vb === "") return -1;
                    const na = parseFloat(va), nb = parseFloat(vb);
                    if (!isNaN(na) && !isNaN(nb)) return (na - nb) * m;
                    return va.localeCompare(vb) * m;
                  });
                  rows.forEach(r => t.tBodies[0].appendChild(r));
                }
                if (t.tHead) Array.from(t.tHead.rows[0].cells).forEach((th, i) => {
                  th.removeAttribute("data-sorted"); th.removeAttribute("data-filtered");
                  if (state.sort && state.sort.col === i) th.setAttribute("data-sorted", state.sort.dir);
                  const f = filters[i]; if (f && f.length) th.setAttribute("data-filtered", "1");
                });
                if (mo) { mo.takeRecords();
                  mo.observe(document.body, { childList: true, subtree: true }); }
              }

              function closeMenu() { if (menu) { menu.remove(); menu = null; } }

              function openMenu(th, col) {
                closeMenu();
                const t = table();
                menu = document.createElement("div");
                menu.className = "t24-rt-menu";
                const r = th.getBoundingClientRect();
                menu.style.left = Math.min(r.left, window.innerWidth - 210) + "px";
                menu.style.top = (r.bottom + 4) + "px";
                const item = (label, on, fn) => {
                  const b = document.createElement("button");
                  b.className = "t24-rtm-item" + (on ? " on" : "");
                  b.textContent = label; b.onclick = fn; return b;
                };
                const isS = d => state.sort && state.sort.col === col && state.sort.dir === d;
                menu.append(
                  item("↑ 升序", isS("asc"), () => { state.sort = { col, dir: "asc" }; save(); apply(); closeMenu(); }),
                  item("↓ 降序", isS("desc"), () => { state.sort = { col, dir: "desc" }; save(); apply(); closeMenu(); }));
                if (FILTERABLE.has(col)) {
                  const sep = document.createElement("div"); sep.className = "t24-rtm-sep"; menu.append(sep);
                  const cap = document.createElement("div"); cap.className = "t24-rtm-cap"; cap.textContent = "筛选"; menu.append(cap);
                  const values = [...new Set(Array.from(t.tBodies[0].rows).map(x => disp(x, col)))];
                  const cur = (state.filters || {})[col];
                  values.forEach(v => {
                    const lab = document.createElement("label"); lab.className = "t24-rtm-check";
                    const cb = document.createElement("input"); cb.type = "checkbox";
                    cb.checked = !cur || !cur.length || cur.includes(v);
                    cb.onchange = () => {
                      const on = Array.from(menu.querySelectorAll(".t24-rtm-check input"))
                        .map((c, i) => c.checked ? values[i] : null).filter(x => x !== null);
                      state.filters = state.filters || {};
                      if (on.length === values.length) delete state.filters[col];
                      else state.filters[col] = on;
                      save(); apply();
                    };
                    lab.append(cb, document.createTextNode(v)); menu.append(lab);
                  });
                }
                const sep2 = document.createElement("div"); sep2.className = "t24-rtm-sep"; menu.append(sep2);
                menu.append(item("↺ 恢复默认", false, () => {
                  delete state.sort;
                  if (state.filters) delete state.filters[col];
                  save(); closeMenu();
                  location.reload();   // 恢复服务端默认顺序最干净
                }));
                document.body.appendChild(menu);
              }

              document.addEventListener("click", (e) => {
                const th = e.target.closest(".t24-rt thead th");
                if (th) {
                  const col = Array.from(th.parentNode.children).indexOf(th);
                  if (col === 0) return;   // 跟踪列不出菜单
                  e.stopPropagation(); openMenu(th, col); return;
                }
                if (menu && !e.target.closest(".t24-rt-menu")) closeMenu();
              }, true);

              // Streamlit 重跑重建表格后自动重放排序/筛选
              let raf = 0;
              mo = new MutationObserver(() => {
                if (raf) return;
                raf = requestAnimationFrame(() => { raf = 0; apply(); });
              });
              mo.observe(document.body, { childList: true, subtree: true });
              apply();
            })();`;
            doc.body.appendChild(s2);
          }
        })();
        </script>""",
        height=0,
    )


# ---- 路由 ----
view = st.session_state.view
if view == "管理关注列表":
    manage()
elif view == "复盘":
    review()
elif st.session_state.detail:
    detail_view(st.session_state.detail)
else:
    overview()

render_ai_plugin()
