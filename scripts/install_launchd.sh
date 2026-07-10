#!/usr/bin/env bash
# 在 Mac mini 上把 Trading24 装成常驻服务（macOS 原生，无需 Docker）。
# 做三件事：
#   1) uv sync 复现环境
#   2) 生成并加载两个 launchd 任务：看板常驻 + 每日批量分析
#   3) 打印防休眠与 Cloudflare Tunnel 的后续步骤
#
# 用法（在 Mac mini 上、仓库根目录执行）：
#   bash scripts/install_launchd.sh
#
# 卸载：
#   launchctl unload ~/Library/LaunchAgents/ai.trading24.dashboard.plist
#   launchctl unload ~/Library/LaunchAgents/ai.trading24.batch.plist
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
echo "▶ 项目目录：$PROJECT_DIR"

# ── 0. 前置检查 ──────────────────────────────────────────────
command -v uv >/dev/null || {
  echo "❌ 没装 uv。先装： curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

[ -f .env ] || {
  echo "❌ 缺 .env。先： cp .env.example .env 然后填 DEEPSEEK_API_KEY / FRED_API_KEY"; exit 1; }
grep -q '^DEEPSEEK_API_KEY=.\+' .env || { echo "❌ .env 里 DEEPSEEK_API_KEY 没填"; exit 1; }
grep -q '^FRED_API_KEY=.\+'    .env || echo "⚠️  .env 里 FRED_API_KEY 没填：News 分析师会让整次运行崩（见 README 坑#3）"

# tradingagents 是本地路径依赖，路径写在 pyproject.toml。mini 上这个目录必须存在。
TA_PATH="$(grep -E 'tradingagents\s*=\s*\{\s*path' pyproject.toml | sed -E 's/.*path\s*=\s*"([^"]+)".*/\1/')"
if [ ! -d "$TA_PATH" ]; then
  echo "❌ tradingagents 依赖目录不存在：$TA_PATH"
  echo "   在 mini 上克隆它（并按需改 pyproject.toml 的 [tool.uv.sources] path）："
  echo "     git clone https://github.com/TauricResearch/TradingAgents.git \"$TA_PATH\""
  echo "     git -C \"$TA_PATH\" checkout c15200dc286b66abce3f1bcf09b298dc06b8539d   # 与本机同版本"
  exit 1
fi
echo "✓ tradingagents 依赖：$TA_PATH"

# ── 1. 复现环境 ──────────────────────────────────────────────
echo "▶ uv sync ..."
uv sync
VENV="$PROJECT_DIR/.venv/bin"
[ -x "$VENV/streamlit" ] || { echo "❌ uv sync 后仍找不到 $VENV/streamlit"; exit 1; }

# ── 2. 解析每日运行时间（取 config/watchlist.yaml 的 run_time，默认 08:30）──
RUN_TIME="$(grep -E '^\s*run_time:' config/watchlist.yaml 2>/dev/null | head -1 \
  | sed -E 's/.*run_time:\s*"?([0-9]{1,2}:[0-9]{2})"?.*/\1/')"
[ -n "${RUN_TIME:-}" ] || RUN_TIME="08:30"
HOUR=$((10#${RUN_TIME%%:*}))   # 10# 防止 08 被当八进制
MIN=$((10#${RUN_TIME##*:}))
echo "✓ 每日批量触发时间：$(printf '%02d:%02d' "$HOUR" "$MIN")（本机时区）"

LA_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LA_DIR" "$PROJECT_DIR/data"
COMMON_PATH="$VENV:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ── 3. 看板常驻任务 ──────────────────────────────────────────
DASH_PLIST="$LA_DIR/ai.trading24.dashboard.plist"
cat > "$DASH_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.trading24.dashboard</string>
  <key>ProgramArguments</key><array>
    <string>$VENV/streamlit</string>
    <string>run</string>
    <string>$PROJECT_DIR/trading24/dashboard/app.py</string>
    <string>--server.port=8501</string>
    <string>--server.address=127.0.0.1</string>
    <string>--server.headless=true</string>
    <string>--browser.gatherUsageStats=false</string>
    <string>--server.enableCORS=false</string>
    <string>--server.enableXsrfProtection=false</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$PROJECT_DIR/data/dashboard.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/data/dashboard.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$COMMON_PATH</string>
  </dict>
</dict></plist>
PLIST

# ── 4. 每日批量任务 ──────────────────────────────────────────
BATCH_PLIST="$LA_DIR/ai.trading24.batch.plist"
cat > "$BATCH_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.trading24.batch</string>
  <key>ProgramArguments</key><array>
    <string>$VENV/python</string>
    <string>$PROJECT_DIR/scripts/run_batch.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MIN</integer>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$PROJECT_DIR/data/batch.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/data/batch.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$COMMON_PATH</string>
  </dict>
</dict></plist>
PLIST

# ── 4b. 只刷行情任务（不跑 LLM，近零成本，每 2 小时一次）──────────
PRICES_PLIST="$LA_DIR/ai.trading24.prices.plist"
cat > "$PRICES_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.trading24.prices</string>
  <key>ProgramArguments</key><array>
    <string>$VENV/python</string>
    <string>$PROJECT_DIR/scripts/refresh_prices.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>StartInterval</key><integer>7200</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$PROJECT_DIR/data/prices.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/data/prices.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$COMMON_PATH</string>
  </dict>
</dict></plist>
PLIST

# ── 5. 加载（先卸旧再装新，幂等）──────────────────────────────
for p in "$DASH_PLIST" "$BATCH_PLIST" "$PRICES_PLIST"; do
  launchctl unload "$p" 2>/dev/null || true
  launchctl load "$p"
  echo "✓ 已加载 $(basename "$p")"
done

echo
echo "══════════════════════════════════════════════════════════"
echo "✅ 服务已装好（看板常驻 + 每日批量分析 + 每2小时只刷行情）。看板在本机 http://127.0.0.1:8501"
echo
echo "还差两步（需要你手动做）："
echo
echo "① 防休眠（Mac mini 要常年可用）："
echo "     sudo pmset -c sleep 0 disksleep 0 displaysleep 10"
echo
echo "② 对外访问（Cloudflare Tunnel）："
echo "     brew install cloudflared"
echo "     # 在 Cloudflare Zero Trust 建隧道，Public Hostname 的 URL 填： http://localhost:8501"
echo "     # 建议再加 Access 登录墙，然后把隧道装成系统服务："
echo "     sudo cloudflared service install <你的隧道 token>"
echo
echo "常用运维："
echo "     tail -f data/dashboard.log        # 看板日志"
echo "     tail -f data/batch.log            # 每日批量日志"
echo "     launchctl kickstart -k gui/\$UID/ai.trading24.batch    # 立刻手动跑一次批量分析（会花钱）"
echo "     launchctl kickstart -k gui/\$UID/ai.trading24.prices   # 立刻只刷一次行情（免费）"
echo "══════════════════════════════════════════════════════════"
