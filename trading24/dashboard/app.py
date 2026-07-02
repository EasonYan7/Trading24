"""Trading24 看板（Agentic 主题）。启动：uv run streamlit run trading24/dashboard/app.py

信息架构：
  总览页 = 关注列表卡片（迷你趋势图 + 决策 + 一句话结论），可扫读。
  详情页 = 点击卡片进入：大趋势图 + 关键参数 + 完整各 agent 报告。
"""
from __future__ import annotations

import importlib
import os
import re
import threading
from datetime import date, datetime, timezone

import bleach
import httpx
import mistune
import streamlit as st
import streamlit.components.v1 as components
from streamlit_sortables import sort_items

from trading24.config import load_settings, load_watchlist, save_watchlist, TickerCfg, market_of
from trading24.db import database as db
from trading24.db.models import Run
from trading24 import prices, runner
from trading24.dashboard import charts as charts_mod

charts_mod = importlib.reload(charts_mod)
build_trend_figure = charts_mod.build_trend_figure
build_sparkline = charts_mod.build_sparkline
_norm_decision = charts_mod._norm_decision

st.set_page_config(page_title="Trading24", layout="wide")

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
  max-width:1260px;
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
.mono { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; }
.caps {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:11px;
  letter-spacing:0;
  text-transform:uppercase;
  color:var(--t24-muted);
}
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
.t24-price { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:22px; font-weight:600; color:var(--t24-text); }
.t24-card-name {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  font-size:17px;
  font-weight:700;
  color:var(--t24-text);
}
.t24-concl {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  font-size:14px;
  line-height:1.55;
  color:var(--t24-muted);
}
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
  padding:0 16px;
  color:var(--t24-tertiary);
  background:var(--t24-surface-low);
  border-bottom:1px solid var(--t24-border);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
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
  color:var(--t24-text) !important;
  font-size:0 !important;
}
.st-key-theme_toggle_dark button::before,
.st-key-theme_toggle_light button::before,
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
.st-key-open_ai_from_nav button * {
  font-size:0 !important;
  line-height:0 !important;
  width:0 !important;
  height:0 !important;
  overflow:hidden !important;
  color:transparent !important;
}
.st-key-theme_toggle_dark button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M21 14.8A8.8 8.8 0 0 1 9.2 3a7.2 7.2 0 1 0 11.8 11.8Z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M21 14.8A8.8 8.8 0 0 1 9.2 3a7.2 7.2 0 1 0 11.8 11.8Z'/%3E%3C/svg%3E");
}
.st-key-theme_toggle_light button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 7a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm0-5h1v3h-2V2h1Zm0 17h1v3h-2v-3h1ZM4.2 5.6l1.4-1.4l2.1 2.1l-1.4 1.4l-2.1-2.1Zm12.1 12.1l1.4-1.4l2.1 2.1l-1.4 1.4l-2.1-2.1ZM2 11h3v2H2v-2Zm17 0h3v2h-3v-2ZM5.6 19.8l-1.4-1.4l2.1-2.1l1.4 1.4l-2.1 2.1ZM17.7 7.7l-1.4-1.4l2.1-2.1l1.4 1.4l-2.1 2.1Z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 7a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm0-5h1v3h-2V2h1Zm0 17h1v3h-2v-3h1ZM4.2 5.6l1.4-1.4l2.1 2.1l-1.4 1.4l-2.1-2.1Zm12.1 12.1l1.4-1.4l2.1 2.1l-1.4 1.4l-2.1-2.1ZM2 11h3v2H2v-2Zm17 0h3v2h-3v-2ZM5.6 19.8l-1.4-1.4l2.1-2.1l1.4 1.4l-2.1 2.1ZM17.7 7.7l-1.4-1.4l2.1-2.1l1.4 1.4l-2.1 2.1Z'/%3E%3C/svg%3E");
}
.st-key-open_ai_from_nav button::before {
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 3h10v2h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-1.5L14 21v-4H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3V3Zm0 4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9v2l1.8-2H19a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H7Zm2 3h2v2H9v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 3h10v2h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-1.5L14 21v-4H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3V3Zm0 4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9v2l1.8-2H19a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H7Zm2 3h2v2H9v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E");
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
  .st-key-t24_topnav { margin-left:-10px; margin-right:-10px; }
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
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(6) {
    display:none !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(5) {
    order:3;
    flex:1 1 100% !important;
    width:100% !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"]:has(.st-key-search_add_ticker) > [data-testid="stColumn"]:nth-child(5) {
    flex:1 1 calc(100% - 88px) !important;
    width:auto !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(6):has(.st-key-search_add_ticker) {
    display:flex !important;
    order:3;
    flex:0 0 80px !important;
    width:80px !important;
  }
  .st-key-t24_topnav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(7),
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
.t24-watch-top {
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:12px;
}
.t24-watch-name {
  font-size:22px;
  font-weight:800;
  color:var(--t24-text);
  white-space:nowrap;
}
.t24-watch-code-line {
  display:flex;
  align-items:center;
  gap:7px;
  margin-top:2px;
}
.t24-watch-code {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:12px;
  font-weight:650;
  color:var(--t24-muted);
  line-height:1.2;
  white-space:nowrap;
}
.t24-watch-market {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:10px;
  font-weight:650;
  color:var(--t24-muted);
  line-height:1.2;
}
.t24-watch-price {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:17px;
  font-weight:600;
  color:var(--accent, var(--t24-text));
  margin-top:6px;
  white-space:nowrap;
}
.t24-watch-side {
  text-align:right;
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:12px;
  color:var(--t24-muted);
  min-width:92px;
}
.t24-watch-metric {
  margin-bottom:4px;
}
.t24-watch-metric-label {
  display:block;
  color:var(--t24-subtle);
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  font-size:11px;
  line-height:1.15;
}
.t24-watch-metric-value {
  display:block;
  font-size:12px;
  line-height:1.25;
  white-space:nowrap;
}
.t24-watch-decision {
  --decision: var(--t24-muted);
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin:16px 0 16px;
  padding:10px 12px;
  border:1px solid color-mix(in srgb, var(--decision) 34%, var(--t24-border));
  border-radius:12px;
  background:color-mix(in srgb, var(--decision) 8%, transparent);
}
.t24-watch-decision-kicker {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:10px;
  line-height:1.2;
  color:var(--t24-muted);
  margin-bottom:4px;
}
.t24-watch-decision-value {
  display:inline-flex;
  align-items:center;
  gap:7px;
  color:var(--decision);
  font-size:13px;
  font-weight:750;
  line-height:1.2;
}
.t24-watch-decision-value::before {
  content:"";
  width:8px;
  height:8px;
  border-radius:99px;
  background:var(--decision);
  box-shadow:0 0 0 4px color-mix(in srgb, var(--decision) 14%, transparent);
}
.t24-watch-decision-age {
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  color:var(--t24-muted);
  font-size:12px;
  white-space:nowrap;
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
  color:var(--t24-primary);
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
    left:max(22px, calc((100vw - 1260px) / 2 + 22px));
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
.t24-brand-mark {
  width:26px;
  height:26px;
  border-radius:6px;
  background:var(--t24-primary);
  color:var(--t24-primary-text);
  display:flex;
  align-items:center;
  justify-content:center;
  font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important;
  font-size:13px;
  font-weight:800;
}
.t24-brand-name {
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  font-size:21px;
  font-weight:800;
  line-height:1;
  color:var(--t24-text);
}
.t24-theme-switch { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:12px 0 10px; }
.t24-ai-msg { border:1px solid var(--t24-border); border-radius:10px; padding:8px 11px; margin:6px 0; background:var(--t24-surface); font-size:13px; line-height:1.55; }
.t24-ai-msg.user { background:var(--t24-ai-user); border-color:var(--t24-border-strong); }
.t24-ai-msg.assistant { background:var(--t24-ai-assistant); border-color:var(--t24-border); }
.t24-ai-msg :is(p,li) { font-size:13px !important; line-height:1.55 !important; margin:.25em 0 !important; }
.t24-ai-msg :is(h1,h2,h3,h4) { font-size:13.5px !important; font-weight:700 !important; margin:.45em 0 .2em !important; }
.t24-ai-msg table { font-size:12px !important; }
.t24-ai-meta { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:9px; color:var(--t24-muted); margin-bottom:3px; }
.t24-ai-title { font-size:15px; font-weight:700; color:var(--t24-text); line-height:1; }
.t24-ai-scope { font-size:11px; margin:4px 0 6px; }
.t24-ai-stat { border:1px solid var(--t24-border); border-radius:8px; padding:10px 12px; background:var(--t24-surface); min-height:58px; }
.t24-ai-stat-label { font-size:11px; color:var(--t24-muted); margin-bottom:5px; }
.t24-ai-stat-value { font-family:'Geist Mono', 'SFMono-Regular', Menlo, monospace !important; font-size:18px; font-weight:700; line-height:1.15; white-space:nowrap; color:var(--t24-text); }
        .st-key-t24_ai_launcher {
            position: fixed;
            right: 20px;
            bottom: 20px;
            z-index: 1002;
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
            bottom: 20px;
            width: 356px;
            height: 492px;
            overflow: auto;
            z-index: 1002;
            background:var(--t24-surface);
            border:1px solid var(--t24-border-strong);
            border-top: none;
            border-radius:0 0 14px 14px;
            padding:10px 12px 12px;
            box-shadow: var(--t24-shadow);
        }
        .st-key-t24_ai_panel [data-testid="stVerticalBlock"] { gap: .4rem; }
        /* sticky 头部：滚动时始终可见 */
        /* 固定头部（独立于滚动面板，永远可见） */
        .st-key-t24_ai_header {
            position: fixed; right: 20px; bottom: 512px; width: 356px; z-index: 1003;
            background: var(--t24-surface);
            border: 1px solid var(--t24-border-strong); border-bottom: 1px solid var(--t24-border);
            border-radius: 14px 14px 0 0; padding: 8px 12px 6px;
            box-shadow: var(--t24-shadow);
        }
        /* 圆形 × 关闭按钮（头部左侧） */
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
        }
        /* 头部图标按钮：新对话(加号) + 选择记录(列表) */
        .st-key-ai_new_chat button, .st-key-ai_scope_toggle button {
            width:30px !important; min-width:30px !important; height:30px !important; min-height:30px !important;
            padding:0 !important; font-size:0 !important;
            border:none !important; background:transparent !important; box-shadow:none !important;
            border-radius:8px !important; color:var(--t24-text-muted, var(--t24-text)) !important;
            display:flex !important; align-items:center !important; justify-content:center !important;
        }
        .st-key-ai_new_chat button:hover, .st-key-ai_scope_toggle button:hover {
            background:color-mix(in srgb, currentColor 12%, transparent) !important;
            border:none !important; box-shadow:none !important;
        }
        .st-key-ai_new_chat button *, .st-key-ai_scope_toggle button * {
            font-size:0 !important; line-height:0 !important; color:transparent !important;
        }
        .st-key-ai_new_chat button::before, .st-key-ai_scope_toggle button::before {
            content:""; display:block; width:17px; height:17px; background:currentColor;
            -webkit-mask-repeat:no-repeat; -webkit-mask-position:center; -webkit-mask-size:17px;
            mask-repeat:no-repeat; mask-position:center; mask-size:17px;
        }
        .st-key-ai_new_chat button::before {
            -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z'/%3E%3C/svg%3E");
            mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z'/%3E%3C/svg%3E");
        }
        .st-key-ai_scope_toggle button::before {
            -webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M4 6h16v2H4zM4 11h16v2H4zM4 16h16v2H4z'/%3E%3C/svg%3E");
            mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M4 6h16v2H4zM4 11h16v2H4zM4 16h16v2H4z'/%3E%3C/svg%3E");
        }
        .st-key-t24_ai_panel textarea { font-size: 13px !important; }
        .st-key-t24_ai_panel [data-testid="stForm"] button { font-size: 13px !important; }
        [class*="st-key-ai_sugg_"] button { font-size: 12.5px !important; white-space: normal !important; text-align: left !important; padding: 7px 10px !important; }
        @media (max-width: 760px) {
            [data-testid="stSidebar"],
            [data-testid="stSidebar"] > div:first-child {
                width: 100% !important;
                min-width: 0 !important;
                max-width: none !important;
            }
            .t24-page-title { font-size: 26px; }
            .st-key-t24_ai_launcher { right: 12px; bottom: 14px; width: 52px; }
            .st-key-t24_ai_header {
                left: 12px; right: 12px; width: auto; bottom: auto; top: 12px;
                border-radius: 14px 14px 0 0;
            }
            .st-key-t24_ai_panel {
                left: 12px; right: 12px; width: auto;
                top: 64px; bottom: 12px; height: auto;
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


conn = get_conn()
st.markdown(THEME_CSS + CSS, unsafe_allow_html=True)
run_time, watch = load_watchlist()
symbols = [t.symbol for t in watch]
names = {t.symbol: t.name for t in watch}
latest = {s: (db.get_runs(conn, ticker=s, limit=1) or [None])[0] for s in symbols}

if "detail" not in st.session_state:
    st.session_state.detail = None
if "view" not in st.session_state:
    st.session_state.view = "总览"
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
    st.session_state.ai_selected_run_ids = []
if "ai_messages" not in st.session_state:
    st.session_state.ai_messages = []
if "ai_show_history" not in st.session_state:
    st.session_state.ai_show_history = True
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
    """从最终决策段抽一句话结论。"""
    reps = {r.section: r.content for r in db.get_reports(conn, run_id)}
    txt = reps.get("final") or reps.get("trader") or ""
    m = re.search(r"Executive Summary\**[:：]\s*(.+)", txt)
    if m:
        sent = re.split(r"(?<=[.。!?])\s", m.group(1).strip())[0]
        return sent[:140]
    return (txt.strip().replace("\n", " "))[:140] or "（无摘要）"


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


def report_context_for_run(run) -> str:
    lines = [
        f"## {names.get(run.ticker, run.ticker)} · {run.ticker} · {run.trade_date} · {run_display_no(run)}",
        f"- 状态：{status_label(run.status)}",
        f"- 决策：{BADGE.get(_norm_decision(run.decision), ('', '—'))[1]}",
        f"- 模型：{run.model or '未知'}",
        f"- 摘要：{conclusion(run.id or 0)}",
        "",
    ]
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


def build_ai_context(run_ids: list[int]) -> str:
    runs = selected_ai_runs(run_ids)
    if not runs:
        return ""
    return "\n\n---\n\n".join(report_context_for_run(run) for run in runs)


def ask_ai(prompt: str, context_text: str, history: list | None = None) -> tuple[bool, str]:
    settings = load_settings()
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return False, (
            "还没有配置模型密钥。配置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY 后即可对话。"
        )
    base_url = (settings.backend_url or "https://api.deepseek.com/v1").rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    # 系统消息只带一次分析记录，随后附上多轮对话历史 → 支持连续追问
    messages = [{
        "role": "system",
        "content": (
            "你是 Trading24 的投资分析助手。基于下面提供的历史分析记录和与用户的对话回答；"
            "如果记录不足以支持结论，要明确说明缺口。回答使用中文，先给结论，再给依据和风险。"
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
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return True, content.strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"AI 请求失败：{type(exc).__name__}。请检查模型密钥、网络和 settings.yaml 的模型配置。"


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


def goto_dashboard() -> None:
    st.session_state.view = "总览"
    st.session_state.detail = None


def goto_analysis(sym: str | None = None) -> None:
    st.session_state.view = "分析"
    st.session_state.detail = sym or (symbols[0] if symbols else None)


def goto_history() -> None:
    goto_analysis()


def goto_watchlist() -> None:
    st.session_state.view = "管理关注列表"
    st.session_state.detail = None


def route_key() -> str:
    if st.session_state.detail:
        return "Analysis"
    if st.session_state.view == "管理关注列表":
        return "Watchlist"
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
    return re.sub(r"\s+", "", raw)


def is_ticker_like(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Z][0-9A-Z.]{0,18}", value))


def analysis_worker(symbol: str, trade_date: str) -> None:
    try:
        runner.run_one(symbol, trade_date)
    except Exception:
        # runner.run_one already records the failed run in SQLite.
        pass


def start_background_analysis(symbol: str) -> None:
    pending = st.session_state.pending_analysis_symbols
    if symbol in pending:
        return
    pending[symbol] = datetime.now(timezone.utc).isoformat()
    thread = threading.Thread(
        target=analysis_worker,
        args=(symbol, date.today().isoformat()),
        daemon=True,
    )
    thread.start()


def render_auto_refresh(active: bool) -> None:
    if not active:
        return
    components.html(
        """
        <script>
        window.setTimeout(() => window.parent.location.reload(), 5000);
        </script>
        """,
        height=0,
    )


def render_live_analysis_status(sym: str, run: Run | None) -> None:
    pending = sym in st.session_state.pending_analysis_symbols
    status = run.status if run else ("pending" if pending else None)
    if status not in {"pending", "running", "success", "failed"}:
        return
    if status == "pending":
        title = f"{sym} 正在创建分析任务"
        sub = "已完成价格数据校验，正在启动单只股票分析。"
        step_classes = ["done", "active", "", ""]
        refresh = True
    elif status == "running":
        title = f"{sym} Agent Pipeline 正在运行"
        sub = "页面会自动刷新；完成后会展示决策历史和完整报告。"
        step_classes = ["done", "done", "active", ""]
        refresh = True
    elif status == "success":
        title = f"{sym} 分析完成"
        sub = "最新决策和报告已写入，可以继续查看下方模块。"
        step_classes = ["done", "done", "done", "done"]
        refresh = False
    else:
        title = f"{sym} 分析失败"
        sub = html_safe(run.error if run and run.error else "运行失败，请检查 API Key、数据源或限流状态。")
        step_classes = ["done", "done", "active", ""]
        refresh = False
    steps = ("数据源", "创建任务", "Agent Pipeline", "写入报告")
    step_html = "".join(
        f"<div class='t24-live-step {css}'>{html_safe(label)}</div>"
        for label, css in zip(steps, step_classes)
    )
    st.markdown(
        f"<div class='t24-live-run'>"
        f"<div class='t24-live-run-head'><span>{html_safe(title)}</span>"
        f"<span class='t24-data'>{html_safe(status_label(status))}</span></div>"
        f"<div class='t24-live-run-sub'>{sub}</div>"
        f"<div class='t24-live-run-steps'>{step_html}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if status in {"success", "failed"}:
        st.session_state.pending_analysis_symbols.pop(sym, None)
    render_auto_refresh(refresh)


def render_market_tape() -> None:
    tape_items = []
    for sym in symbols[:4]:
        tape_items.append(
            f"{html_safe(sym)}: {html_safe(price_display(sym))} "
            f"({html_safe(delta_display(sym))})"
        )
    if not tape_items:
        tape_items.append("No watchlist symbols")
    tape_items.append(f"Scheduler: Next run {html_safe(run_time)}")
    st.markdown(f"<div class='t24-tape'>{' &nbsp; | &nbsp; '.join(tape_items)}</div>",
                unsafe_allow_html=True)


def render_top_nav() -> None:
    active = route_key()
    with st.container(key="t24_topnav"):
        nav_cols = st.columns(
            [1.3, .5, .5, .5, 1.95, 1.3, .5, .5],
            gap="small",
            vertical_alignment="center",
        )
        nav_cols[0].markdown("<div class='t24-logo'>TRADING24</div>", unsafe_allow_html=True)
        nav_items = [
            ("Dashboard", "总览", goto_dashboard, icon("overview", 15)),
            ("Analysis", "分析", goto_analysis, icon("analysis", 15)),
            ("Watchlist", "关注", goto_watchlist, icon("watch", 15)),
        ]
        for col, (key, label, handler, _) in zip(nav_cols[1:4], nav_items):
            if col.button(label, key=f"topnav_{key}", width="stretch",
                          type="primary" if active == key else "secondary"):
                handler()
                st.rerun()
        nav_cols[4].markdown("")  # 弹性留白，把搜索栏推到右侧
        search = nav_cols[5].selectbox(
            "Search ticker",
            symbols,
            index=None,
            format_func=search_option_label,
            placeholder="Search ticker...",
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
            if nav_cols[6].button("添加", key="search_add_ticker", width="stretch"):
                if not is_ticker_like(candidate):
                    st.toast("请输入股票代码，例如 AAPL / 600519.SS / 0700.HK")
                    st.rerun()
                with st.spinner(f"校验并添加 {candidate} ..."):
                    try:
                        got = prices.snapshot(conn, candidate)
                    except Exception:
                        got = 0
                if not got:
                    st.toast(f"取不到 {candidate} 的价格数据，请检查代码格式")
                    st.rerun()
                rt, current_watch = load_watchlist()
                current_symbols = [t.symbol for t in current_watch]
                if candidate not in current_symbols:
                    current_watch.append(TickerCfg(symbol=candidate, name=candidate))
                    save_watchlist(rt, current_watch)
                start_background_analysis(candidate)
                st.session_state.search_nonce += 1
                goto_analysis(candidate)
                st.toast(f"已添加 {candidate}，正在运行分析")
                st.rerun()
        else:
            nav_cols[6].markdown("")
        next_theme = "深色" if st.session_state.theme_mode == "浅色" else "浅色"
        theme_key = "theme_toggle_dark" if next_theme == "深色" else "theme_toggle_light"
        if nav_cols[7].button("切换主题", key=theme_key, width="stretch", help="切换深浅主题"):
            st.session_state.theme_mode = next_theme
            st.rerun()


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
                    "<div class='caps'>分析标的</div>",
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
            f"<div class='t24-data' style='color:var(--t24-primary);'>{html_safe(price_delta_text)}</div></div>"
            f"<div style='text-align:right;'><div class='caps'>决策状态</div>"
            f"{badge(r.decision if r else None, big=True)}"
            f"<div class='t24-data' style='font-size:12px;color:var(--t24-muted);margin-top:8px;'>"
            f"{html_safe(token_cost_text(r))}</div></div></div>"
        )
        st.markdown(hero, unsafe_allow_html=True)

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
                    mode_cols = st.columns([1, 0.16, 0.16], vertical_alignment="center")
                    if mode_cols[1].button("取消", width="stretch", key=f"analysis_cancel_delete_{sym}"):
                        st.session_state.analysis_delete_mode = False
                        st.session_state.analysis_delete_pending_ids = []
                        for key in list(st.session_state.keys()):
                            if str(key).startswith("analysis_pick_"):
                                st.session_state[key] = False
                        st.rerun()
                    if mode_cols[2].button("删除", disabled=not selected_delete_ids, width="stretch",
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
                history_html += f"<details class='t24-decision-item'{decision_open_attr}>"
                history_html += (
                    f"<summary style='--decision:{accent};'><div class='t24-decision-row'>"
                    f"<div class='t24-decision-main'><span class='t24-decision-dot'></span>"
                    f"<div class='t24-decision-copy'>"
                    f"<div class='t24-decision-date'>{html_safe(item.trade_date)}</div>"
                    f"<div class='t24-decision-sub'>{html_safe(run_display_no(item))} · "
                    f"{html_safe(status_label(item.status))}</div>"
                    f"</div></div>"
                    f"<span class='t24-decision-chip'>{html_safe(decision_label(item.decision))}</span>"
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


def overview():
    done = sum(1 for s in symbols if latest[s] and latest[s].status == "success")
    running = sum(1 for s in symbols if latest[s] and latest[s].status == "running")
    total = max(len(symbols), 1)
    pct = int(done / total * 100)
    st.markdown(
        "<div class='t24-page-head'>"
        "<div><div class='t24-headline'>Watchlist Overview</div>"
        "<div class='t24-subhead'>High-priority targets based on daily agent analysis.</div></div>"
        "<div class='caps'>Live workspace · Bento grid</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    main_left, main_right = st.columns([1.35, .95], gap="large")
    with main_left:
        for sym in symbols:
            r = latest[sym]
            dec = _norm_decision(r.decision if r else None)
            accent = decision_accent(r.decision if r else None)
            delta = delta_display(sym)
            volume = (db.get_prices(conn, sym, limit=1) or [{}])[-1].get("volume")
            vol_text = f"{volume / 1_000_000:.1f}M" if volume else "—"
            display_name = names.get(sym, sym) or sym
            with st.container(border=True):
                st.markdown(
                    f"<div style='--accent:{accent};border-left:4px solid {accent};padding-left:14px;'>"
                    f"<div class='t24-watch-top'>"
                    f"<div><div class='t24-watch-name'>{html_safe(display_name)}</div>"
                    f"<div class='t24-watch-code-line'><span class='t24-watch-code'>{html_safe(sym)}</span>"
                    f"<span class='t24-watch-market'>{html_safe(market_label(sym))}</span></div>"
                    f"<div class='t24-watch-price'>{html_safe(price_display(sym))}</div></div>"
                    f"<div class='t24-watch-side'>"
                    f"<div class='t24-watch-metric'><span class='t24-watch-metric-label'>日涨跌</span>"
                    f"<span class='t24-watch-metric-value' style='color:{accent};'>{html_safe(delta)}</span></div>"
                    f"<div class='t24-watch-metric'><span class='t24-watch-metric-label'>成交量</span>"
                    f"<span class='t24-watch-metric-value'>{html_safe(vol_text)}</span></div></div></div></div>",
                    unsafe_allow_html=True,
                )
                card_prices = db.get_prices(conn, sym, limit=120)
                card_runs = db.get_runs(conn, ticker=sym, limit=12)
                st.plotly_chart(
                    build_sparkline(card_prices, ACTIVE_THEME, card_runs),
                    width="stretch",
                    key=f"sp_{sym}",
                    config={"displayModeBar": False},
                )
                if r and r.status == "success":
                    st.markdown(f"<div class='t24-concl'>{html_safe(conclusion(r.id))}</div>",
                                unsafe_allow_html=True)
                elif r and r.status == "failed":
                    st.markdown("<div class='t24-concl' style='color:var(--t24-danger);'>最近运行失败</div>",
                                unsafe_allow_html=True)
                else:
                    st.markdown("<div class='t24-concl' style='color:var(--t24-muted);'>暂无分析</div>",
                                unsafe_allow_html=True)
                st.markdown(
                    f"<div class='t24-watch-decision' style='--decision:{accent};'>"
                    f"<div><div class='t24-watch-decision-kicker'>决策状态</div>"
                    f"<div class='t24-watch-decision-value'>{html_safe(decision_label(r.decision if r else None))}</div></div>"
                    f"<div class='t24-watch-decision-age'>{html_safe(run_age(r))}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button("查看分析", key=f"go_{sym}", width="stretch",
                             type="primary" if dec == "BUY" else "secondary"):
                    goto_analysis(sym)
                    st.rerun()

    # 用真实的决策分布，而不是编造的分析师进度
    buys = sum(1 for s in symbols if latest[s] and _norm_decision(latest[s].decision) == "BUY")
    holds = sum(1 for s in symbols if latest[s] and _norm_decision(latest[s].decision) == "HOLD")
    sells = sum(1 for s in symbols if latest[s] and _norm_decision(latest[s].decision) == "SELL")
    stage_rows = [
        ("增持", f"{buys} 只", "var(--t24-primary)", False),
        ("中性", f"{holds} 只", "var(--t24-muted)", False),
        ("减持", f"{sells} 只", "var(--t24-danger)", False),
    ]
    status_html = (
        f"<div class='t24-bento'><div class='t24-bento-title'>{icon('cpu', 18)}"
        f"<span>今日运行状态</span>"
        f"<span class='t24-data' style='margin-left:auto;font-size:12px;color:var(--t24-muted);'>{done}/{len(symbols)}</span></div>"
        f"<div class='t24-card-footer' style='margin:0 0 4px;'>"
        f"<span>{'分析进行中' if running else ('今日已完成' if done >= len(symbols) else '待运行')}</span>"
        f"<span class='t24-data'>已分析 {done}/{len(symbols)}</span></div>"
        f"<div class='t24-progress' style='--value:{pct}%;'><span></span></div>"
    )
    for label, state, dot, is_running in stage_rows:
        status_html += (
            f"<div class='t24-stage-row'><span class='t24-dot {'running' if is_running else ''}' "
            f"style='--dot:{dot};'></span><span>{label}</span>"
            f"<span class='t24-data' style='color:{dot};'>{html_safe(state)}</span></div>"
        )
    status_html += "</div>"

    all_runs = db.get_runs(conn, limit=6)
    alert_html = (
        f"<div class='t24-bento' style='min-height:360px;'>"
        f"<div class='t24-bento-title'>{icon('bell', 18)}<span>Recent Alerts</span>"
        f"<span class='caps' style='margin-left:auto;'>Clear</span></div>"
    )
    for r in all_runs[:5]:
        d = _norm_decision(r.decision)
        accent = decision_accent(r.decision)
        title = "Analysis Completed" if r.status == "success" else status_label(r.status)
        if d == "SELL":
            title = "Risk Signal"
        elif d == "BUY":
            title = "Opportunity Signal"
        alert_html += (
            f"<div class='t24-alert-row'><span style='color:{accent};'>{icon('trend', 17)}</span>"
            f"<span><strong>{html_safe(title)}: {html_safe(r.ticker)}</strong><br>"
            f"<span class='t24-data' style='color:var(--t24-muted);font-size:12px;'>"
            f"{html_safe(run_age(r))} · {html_safe(run_display_no(r))}</span></span>"
            f"<span>{badge(r.decision)}</span></div>"
        )
    alert_html += "</div>"
    with main_right:
        st.markdown(
            f"{status_html}<div class='t24-overview-side-gap'></div>{alert_html}",
            unsafe_allow_html=True,
        )


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


def manage():
    rt, tickers = load_watchlist()
    st.markdown(
        f"<div class='t24-page-title'>{icon('manage', 23)}<span>管理关注列表</span></div>"
        "<div class='t24-muted'>维护每日分析标的、运行时间和预警阈值。</div>",
        unsafe_allow_html=True,
    )

    with st.form("run_time_form", border=False):
        ct = st.columns([2, 1], vertical_alignment="bottom")
        new_time = ct[0].text_input("每日运行时间 (HH:MM)", rt)
        saved_time = ct[1].form_submit_button("保存时间", width="stretch")
    if saved_time:
        clean_time = new_time.strip() or rt
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", clean_time):
            st.error("时间格式请使用 HH:MM，例如 08:30。")
        else:
            save_watchlist(clean_time, tickers)
            st.toast("已保存运行时间")
            st.rerun()

    st.markdown("<div class='caps' style='margin:18px 0 6px;'>当前标的 · 按住左侧把手拖动排序</div>",
                unsafe_allow_html=True)
    if tickers:
        label_to_ticker = {ticker_label(t): t for t in tickers}
        current_labels = list(label_to_ticker.keys())
        list_col, edit_col, delete_col = st.columns([5.8, .95, .95], gap="small")
        with list_col:
            sorted_labels = sort_items(
                current_labels,
                direction="vertical",
                custom_style=f"""
                html, body, #root {{
                    margin: 0;
                    background: {ACTIVE_THEME["surface"]} !important;
                    color: {ACTIVE_THEME["text"]} !important;
                }}
                .sortable-component {{
                    margin: 4px 0 12px 0;
                    width: 100%;
                    box-sizing: border-box;
                    background: {ACTIVE_THEME["surface"]} !important;
                    color: {ACTIVE_THEME["text"]} !important;
                }}
                .sortable-component.vertical {{
                    overflow: visible;
                }}
                .sortable-container {{
                    width: 100%;
                    box-sizing: border-box;
                }}
                .sortable-container-body {{
                    display: flex;
                    flex-direction: column;
                    gap: 10px;
                    padding: 12px;
                    border: 1px solid {ACTIVE_THEME["border"]};
                    border-radius: 14px;
                    background: {ACTIVE_THEME["surface"]};
                    box-sizing: border-box;
                    min-height: 0;
                }}
                .sortable-item {{
                    position: relative;
                    border: 1px solid {ACTIVE_THEME["border"]} !important;
                    border-radius: 10px;
                    background: {ACTIVE_THEME["surface_high"]} !important;
                    color: {ACTIVE_THEME["text"]} !important;
                    box-sizing: border-box;
                    font-family: "Geist Mono", monospace;
                    font-size: 12px;
                    line-height: 52px !important;
                    height: 52px;
                    min-height: 52px;
                    padding: 0 14px 0 46px !important;
                    margin: 0 !important;
                    cursor: grab;
                    display: flex;
                    align-items: center;
                    justify-content: flex-start;
                    text-align: left;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    box-shadow: none !important;
                    transition: background-color 160ms ease, border-color 160ms ease !important;
                }}
                .sortable-item > * {{
                    height: 100%;
                    min-height: 100%;
                    display: flex;
                    align-items: center;
                    justify-content: flex-start;
                    line-height: 1 !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }}
                .sortable-item::before {{
                    content: "";
                    position: absolute;
                    left: 15px;
                    top: 50%;
                    width: 18px;
                    height: 18px;
                    transform: translateY(-50%);
                    background: {ACTIVE_THEME["text"]};
                    opacity: .6;
                    -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 6h2v2H7V6Zm4 0h2v2h-2V6Zm4 0h2v2h-2V6ZM7 11h2v2H7v-2Zm4 0h2v2h-2v-2Zm4 0h2v2h-2v-2ZM7 16h2v2H7v-2Zm4 0h2v2h-2v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E") center / 18px 18px no-repeat;
                    mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M7 6h2v2H7V6Zm4 0h2v2h-2V6Zm4 0h2v2h-2V6ZM7 11h2v2H7v-2Zm4 0h2v2h-2v-2Zm4 0h2v2h-2v-2ZM7 16h2v2H7v-2Zm4 0h2v2h-2v-2Zm4 0h2v2h-2v-2Z'/%3E%3C/svg%3E") center / 18px 18px no-repeat;
                }}
                .sortable-item:hover {{
                    background: {ACTIVE_THEME["surface"]} !important;
                    border-color: {ACTIVE_THEME["border_strong"]} !important;
                }}
                .sortable-item:active {{
                    cursor: grabbing;
                }}
                """,
                key="ticker_sorter",
            )
        with edit_col:
            with st.container(key="watchlist_edit_actions"):
                for t in tickers:
                    if st.button("编辑", key=f"edit_{t.symbol}", width="stretch"):
                        st.session_state.editing_symbol = t.symbol
                        st.session_state.pending_delete = None
                        st.rerun()
        with delete_col:
            with st.container(key="watchlist_delete_actions"):
                for t in tickers:
                    if st.button("删除", key=f"del_{t.symbol}", width="stretch"):
                        st.session_state.pending_delete = t.symbol
                        st.session_state.editing_symbol = None
                        st.rerun()
        if sorted_labels != current_labels and all(label in label_to_ticker for label in sorted_labels):
            save_watchlist(rt, [label_to_ticker[label] for label in sorted_labels])
            st.toast("已保存排序")
            st.rerun()
    else:
        st.info("暂无标的。")

    if st.session_state.pending_delete:
        target = next((t for t in tickers if t.symbol == st.session_state.pending_delete), None)
        if target:
            st.warning(f"确认删除 {target.name or target.symbol}？")
            dc = st.columns([1, 1, 4])
            if dc[0].button("确认删除", type="primary", width="stretch"):
                save_watchlist(rt, [t for t in tickers if t.symbol != target.symbol])
                st.session_state.pending_delete = None
                st.toast(f"已删除 {target.symbol}")
                st.rerun()
            if dc[1].button("取消", width="stretch"):
                st.session_state.pending_delete = None
                st.rerun()

    editing = next((t for t in tickers if t.symbol == st.session_state.editing_symbol), None)
    if editing:
        st.markdown("<div class='caps' style='margin:18px 0 6px;'>编辑标的</div>", unsafe_allow_html=True)
        with st.form(f"edit_{editing.symbol}", border=True):
            a = st.columns([1, 1])
            edit_symbol = a[0].text_input("代码", editing.symbol, disabled=True)
            edit_name = a[1].text_input("名称", editing.name)
            b = st.columns(3)
            edit_upper = b[0].number_input(
                "上限预警", min_value=0.0, value=float(editing.upper or 0), step=1.0,
                key=f"upper_{editing.symbol}",
            )
            edit_lower = b[1].number_input(
                "下限预警", min_value=0.0, value=float(editing.lower or 0), step=1.0,
                key=f"lower_{editing.symbol}",
            )
            edit_pct = b[2].number_input(
                "日涨跌幅% 预警", min_value=0.0, value=float(editing.pct_move or 0), step=1.0,
                key=f"pct_{editing.symbol}",
            )
            st.markdown(
                "<div class='t24-edit-note'>填 0 表示不启用该预警阈值。</div>",
                unsafe_allow_html=True,
            )
            ec = st.columns([1, 1, 4])
            saved_ticker = ec[0].form_submit_button("保存", width="stretch")
            canceled_edit = ec[1].form_submit_button("取消", width="stretch")
        if saved_ticker:
            updated = []
            for t in tickers:
                if t.symbol == edit_symbol:
                    updated.append(TickerCfg(
                        symbol=t.symbol,
                        name=edit_name.strip(),
                        upper=edit_upper or None,
                        lower=edit_lower or None,
                        pct_move=edit_pct or None,
                    ))
                else:
                    updated.append(t)
            save_watchlist(rt, updated)
            st.session_state.editing_symbol = None
            st.toast(f"已更新 {editing.symbol}")
            st.rerun()
        if canceled_edit:
            st.session_state.editing_symbol = None
            st.rerun()

    st.markdown("<div class='caps' style='margin:16px 0 6px;'>添加标的</div>", unsafe_allow_html=True)
    with st.form("add_ticker", clear_on_submit=True):
        a = st.columns([2, 2])
        sym = a[0].text_input("代码", placeholder="NVDA / 600519.SS / 0700.HK")
        name = a[1].text_input("名称（可选）")
        b = st.columns(3)
        upper = b[0].number_input("上限预警", min_value=0.0, value=0.0, step=1.0)
        lower = b[1].number_input("下限预警", min_value=0.0, value=0.0, step=1.0)
        pct = b[2].number_input("日涨跌幅% 预警", min_value=0.0, value=0.0, step=1.0)
        if st.form_submit_button("添加并校验数据源"):
            s = sym.strip().upper()
            if not s:
                st.error("请填写代码")
            elif s in [t.symbol for t in tickers]:
                st.warning(f"{s} 已在列表中")
            else:
                with st.spinner(f"校验 {s} 数据源…"):
                    try:
                        got = prices.snapshot(conn, s)
                    except Exception:  # noqa: BLE001
                        got = 0
                if not got:
                    st.error(f"取不到 {s} 的数据，请检查代码格式（美股直接代码 / A股加 .SS/.SZ / 港股加 .HK）")
                else:
                    tickers.append(TickerCfg(symbol=s, name=name.strip(),
                                             upper=upper or None, lower=lower or None,
                                             pct_move=pct or None))
                    save_watchlist(rt, tickers)
                    st.success(f"已添加 {s}（已抓取 {got} 天价格，运行一次分析后出决策）")
                    st.rerun()


def default_ai_run_ids() -> list[int]:
    """默认上下文：详情页=当前标的最近成功记录；否则=每只标的最近成功记录。"""
    detail = st.session_state.get("detail")
    if detail:
        r = latest.get(detail)
        if r and r.status == "success" and r.id:
            return [int(r.id)]
    ids = []
    for s in symbols:
        r = latest.get(s)
        if r and r.status == "success" and r.id:
            ids.append(int(r.id))
    return ids


def _send_ai(prompt: str, run_ids: list[int]) -> None:
    prompt = (prompt or "").strip()
    if not prompt:
        return
    if not run_ids:
        st.session_state.ai_messages.append({
            "role": "assistant", "ok": False, "attachments": [],
            "content": "还没有可用的分析记录。先在关注列表跑一次分析，我就能基于结果回答了。"})
        return
    labels = [full_run_label(r) for r in selected_ai_runs(run_ids)]
    history = list(st.session_state.ai_messages)  # 本轮提问之前的对话历史
    st.session_state.ai_messages.append({"role": "user", "content": prompt, "attachments": labels})
    with st.spinner("AI 正在思考…"):
        ok, answer = ask_ai(prompt, build_ai_context(run_ids), history)
    st.session_state.ai_messages.append({"role": "assistant", "content": answer, "ok": ok, "attachments": labels})


def render_ai_plugin():
    with st.container(key="t24_ai_launcher"):
        launcher_type = "secondary" if st.session_state.ai_panel_open else "primary"
        if st.button("AI 助手", key="toggle_ai_panel", type=launcher_type,
                     width="stretch"):
            st.session_state.ai_panel_open = not st.session_state.ai_panel_open
            st.rerun()

    if not st.session_state.ai_panel_open:
        return

    detail = st.session_state.get("detail")
    default_ids = default_ai_run_ids()
    override = [int(x) for x in st.session_state.ai_selected_run_ids if str(x).isdigit()]
    effective_ids = override or default_ids
    if override:
        scope_desc = f"手动选定的 {len(override)} 条记录"
    elif detail:
        scope_desc = f"当前标的 · {names.get(detail, detail)}"
    else:
        scope_desc = f"全部关注列表 · {len(default_ids)} 只"

    with st.container(key="t24_ai_header"):
        hc = st.columns([0.9, 2.7, 0.8, 0.8], vertical_alignment="center")
        if hc[0].button("✕", key="ai_close", help="关闭"):
            st.session_state.ai_panel_open = False
            st.rerun()
        hc[1].markdown("<div class='t24-ai-title'>AI 助手</div>", unsafe_allow_html=True)
        if hc[2].button("选择记录", key="ai_scope_toggle", width="stretch", help="选择分析记录"):
            st.session_state.ai_show_advanced = not st.session_state.get("ai_show_advanced", False)
            st.rerun()
        if hc[3].button("新对话", key="ai_new_chat", width="stretch", help="开始新对话"):
            st.session_state.ai_messages = []
            st.rerun()

    with st.container(key="t24_ai_panel"):
        st.markdown(
            f"<div class='t24-muted t24-ai-scope'>分析范围：{html_safe(scope_desc)}</div>",
            unsafe_allow_html=True,
        )

        # 对话主体
        if st.session_state.ai_messages:
            for msg in st.session_state.ai_messages[-12:]:
                role_label = "你" if msg["role"] == "user" else "AI"
                css_role = "user" if msg["role"] == "user" else "assistant"
                body = (html_safe(msg["content"]).replace(chr(10), "<br>") if msg["role"] == "user"
                        else report_text_html(msg["content"]))
                st.markdown(
                    f"<div class='t24-ai-msg {css_role}'>"
                    f"<div class='t24-ai-meta'>{role_label}</div>"
                    f"<div>{body}</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            if detail:
                nm = names.get(detail, detail)
                sugg = [f"{nm} 现在的核心风险是什么？", f"{nm} 的多空分歧在哪？", "给我一句话操作建议"]
            else:
                sugg = ["今天最该关注哪只？为什么？", "这些标的里谁的风险最高？", "把各只的决策和理由汇总成一段"]
            st.markdown("<div class='t24-muted' style='margin:6px 0 4px;'>试试问我：</div>",
                        unsafe_allow_html=True)
            for i, q in enumerate(sugg):
                if st.button(q, key=f"ai_sugg_{i}", width="stretch"):
                    _send_ai(q, effective_ids)
                    st.rerun()

        # 输入（表单：提交后自动清空）
        with st.form("ai_form", clear_on_submit=True):
            user_prompt = st.text_area(
                "提问", height=80, label_visibility="collapsed",
                placeholder="问我关于这些分析的任何问题…")
            if st.form_submit_button("发送", type="primary", width="stretch"):
                _send_ai(user_prompt, effective_ids)
                st.rerun()

        # 高级：手动指定上下文（默认隐藏；留空=自动）
        if st.session_state.get("ai_show_advanced", False):
            available_runs = [r for r in db.get_runs(conn, limit=200) if r.status == "success"]
            available_ids = [int(r.id) for r in available_runs if r.id is not None]
            available_by_id = {int(r.id): r for r in available_runs if r.id is not None}
            chosen_ids = st.multiselect(
                "手动选择分析记录（留空=按上面的范围自动）",
                available_ids, default=override, key="ai_run_picker",
                format_func=lambda rid: full_run_label(available_by_id[rid]) if rid in available_by_id else f"#{rid}",
            )
            st.session_state.ai_selected_run_ids = [int(rid) for rid in chosen_ids]


# ---- 路由 ----
view = st.session_state.view
if view == "管理关注列表":
    manage()
elif st.session_state.detail:
    detail_view(st.session_state.detail)
else:
    overview()

render_ai_plugin()
