# Trading24 部署到 Mac mini（家里常驻）

把看板和每日批量分析搬到家里的 Mac mini，常年运行；再用 **Cloudflare Tunnel** 让你在外面任何地方都能访问 —— 不用开公网端口、不用买/备案域名（用你已托管在 Cloudflare 的域名即可），还能加一道登录墙。

> **为什么用原生 macOS 而不是 Docker：** Docker Desktop 在 Mac 上是个 GUI 应用，让它在一台没接显示器、常年开机的 mini 上自启动＋常驻既别扭又吃内存。Mac mini 用 `uv` + `launchd`（macOS 自带的服务/定时机制）最省心，跟你 MacBook 上的跑法完全一样，也正是 `design.md` §8 早就规划的路线。
>
> 想以后真上云（Linux）时再用 Docker？仓库里保留了那套方案，见文末附录。

架构：

```
你（外面任意浏览器）
   │  https://trading24.你的域名
   ▼
Cloudflare（DNS + Access 登录墙）
   │  Tunnel（加密，家里 NAT 也能穿，无需公网 IP/端口）
   ▼
家里 Mac mini（常年开机）
   • launchd: ai.trading24.dashboard  → Streamlit 常驻(127.0.0.1:8501)
   • launchd: ai.trading24.batch      → 每天定点跑批量分析(调 LLM，花钱)
   • launchd: ai.trading24.prices     → 每2小时只刷行情(不调 LLM，免费)
   • cloudflared                      → 对外入口
   • data/trading24.sqlite            → 数据库(本地文件)
```

---

## 前置检查（重要）

1. **能直连外网**：数据管道要访问 Yahoo Finance / FRED / Reddit / Telegram，这些在中国大陆会被墙。确认 mini 的家庭网络能直连；若不能，要给 mini 单独配代理（DeepSeek 在国内不受影响）。
2. **保持不休眠**：mini 睡了就既不能定时跑、也访问不了（见第 5 步）。

---

## 1. 装 uv + 拉两个仓库

在 mini 上：

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 主仓库（私有）
git clone https://github.com/EasonYan7/Trading24.git
cd Trading24
```

**tradingagents 本地依赖**（`pyproject.toml` 里写死了它的路径）。
⚠️ 本机的 TradingAgents **带有我们自己的提交**（`df045a3`：最终决策输出建仓区/止损/仓位字段），
GitHub 上游没有这个改动，**不能直接 clone 上游**。两种迁移方式任选：

```bash
# 方式 A（推荐）：直接从本机拷贝整个目录（含本地提交）
rsync -a --exclude .venv 旧机器:/Users/didi/TradingAgents/ /Users/didi/TradingAgents/

# 方式 B：把本机 TradingAgents 推到你自己的 GitHub fork，mini 上 clone 你的 fork
# （在本机）git remote add mine git@github.com:<你>/TradingAgents.git && git push mine main
```

若 mini 上的用户名不是 didi，改 `pyproject.toml` 的 `[tool.uv.sources]` path 或放到同名路径。

> `data/trading24.sqlite` 已随主仓库带过来，作**初始种子库**，clone 后直接有历史结果。上线后以 mini 上这份为准（见第 7 节）。

---

## 2. 填密钥（.env）

```bash
cp .env.example .env
nano .env
```

必填：

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | 批量分析用的 LLM |
| `FRED_API_KEY` | **必填**，缺了 News 分析师会让整次运行崩（免费申请：https://fredaccount.stlouisfed.org/apikey） |

`.env` 不进 Git。

---

## 3. 一键装成常驻服务

```bash
bash scripts/install_launchd.sh
```

它会：`uv sync` 复现环境 → 生成并加载三个 launchd 任务 → 打印后续两步。三个任务：
- **看板常驻**（`ai.trading24.dashboard`）
- **每日批量分析**（`ai.trading24.batch`）：每天定点跑一遍，出 BUY/HOLD/SELL，会调 LLM 花钱；时间取 `config/watchlist.yaml` 的 `run_time`，默认 08:30。
- **只刷行情**（`ai.trading24.prices`）：每 2 小时刷一次价格，不调 LLM、近零成本，让趋势图/最新价保持新鲜。手动跑：`uv run python scripts/refresh_prices.py`。

装完看板就在本机 `http://127.0.0.1:8501` 起来了。

---

## 4. 对外访问（Cloudflare Tunnel）

在 mini 上：

```bash
brew install cloudflared
```

在浏览器打开 **Cloudflare 控制台 → Zero Trust**（`one.dash.cloudflare.com`，免费额度够用）：

1. **Networks → Tunnels → Create a tunnel → Cloudflared**，起名 `trading24`，创建后复制它给的 **token（`eyJ...`）**。
2. 进该隧道 **Public Hostname → Add**：
   - Subdomain：`trading24`（随你）
   - Domain：选你在 Cloudflare 的域名
   - Type：`HTTP`
   - URL：`http://localhost:8501`  ← 原生部署填 localhost（Docker 版才填服务名）
3. （强烈建议）**Access → Applications → Add → Self-hosted**：域名设 `trading24.你的域名`，Policy 里 `Allow` + 你的邮箱。打开网站要先邮箱验证码，看板就不对外公开。

把隧道装成系统服务（开机自启）：

```bash
sudo cloudflared service install <上面复制的 token>
```

---

## 5. 让 mini 不休眠

```bash
sudo pmset -c sleep 0 disksleep 0 displaysleep 10
# 电源上永不系统休眠；硬盘不休眠；屏幕 10 分钟关（不影响服务）
```

---

## 6. 验证

- 本机：`curl -I http://127.0.0.1:8501/_stcore/health` → `200`
- 外面浏览器打开 `https://trading24.你的域名`：先过 Cloudflare 登录，再看到看板
- `launchctl list | grep trading24` 能看到两个任务
- 手动跑一次批量（会花钱，约 $0.08–0.10/只）：
  ```bash
  launchctl kickstart -k gui/$UID/ai.trading24.batch
  tail -f data/batch.log
  ```

---

## 7. 数据库 & 备份（你问的"包括数据库"）

- 就是那个 **704KB 的 SQLite 文件**，待在 mini 的 `data/trading24.sqlite`。**不用换 Postgres**——单写入者（每日一次批量）+ 你读，正是 SQLite 的强项。
- **别让 git 覆盖它**：它当前被提交进了 Git（当种子方便）。上线稳定后建议停止跟踪：
  ```bash
  git rm --cached data/trading24.sqlite
  echo 'data/trading24.sqlite' >> .gitignore
  git commit -m "chore: stop tracking live DB after deploy"
  ```
- **备份**：
  - 最省事：mini 开 **Time Machine**，整盘（含数据库）自动有版本历史。
  - 想要异地/云端：`brew install litestream`，持续增量备份 `data/trading24.sqlite` 到 Cloudflare R2（配置参考仓库 `deploy/litestream.yml`，用 `brew services` 起成常驻）。

---

## 8. 日常运维

```bash
tail -f data/dashboard.log      # 看板日志
tail -f data/batch.log          # 每日批量日志

# 改完代码后更新
git pull && uv sync
launchctl kickstart -k gui/$UID/ai.trading24.dashboard   # 重启看板

# 停 / 起某个任务
launchctl unload ~/Library/LaunchAgents/ai.trading24.dashboard.plist
launchctl load   ~/Library/LaunchAgents/ai.trading24.dashboard.plist
```

改每日触发时间：编辑 `config/watchlist.yaml` 的 `run_time`，再重跑 `bash scripts/install_launchd.sh`（幂等）。

---

## 9. 常见坑

- **打开网站转圈/连不上**：Streamlit websocket 经反代握手问题。已在 launchd 里关掉 CORS/XSRF；确认 Cloudflare Public Hostname 的 URL 填的是 `http://localhost:8501`。
- **News 分析师报错、整次崩**：`FRED_API_KEY` 没填。
- **DeepSeek 404**：模型名要用 `config/settings.yaml` 里当前的名字（DeepSeek 换过命名）。
- **半夜没跑批量**：多半是 mini 睡了（第 5 步）或那天没开机；launchd 的 `StartCalendarInterval` 错过就是错过，不补跑。
- **改了 watchlist 没生效**：看板"管理"页写的是 `config/*.yaml`，原生部署直接读写本地文件，没问题。
- **通知**：本地那套 `hermes` CLI 若 mini 上没装，就先没有 Telegram 通知；要的话二期改直连 Telegram Bot API。

---

## 附录：以后上云（Linux / Docker，可选）

如果哪天要把它放到一台海外 Linux 服务器（design.md §8 说的"最后可选上云"），仓库里已备好一套 Docker 方案，无需重写：

- `Dockerfile` / `docker-compose.yml` —— 看板 + scheduler(supercronic 定时) + cloudflared 一把起
- `deploy/crontab` —— Docker 版的每日触发
- `deploy/litestream.yml` —— SQLite → R2 备份

届时在 Linux 上：`cp .env.example .env` 填好 → `docker compose up -d --build`。Cloudflare Tunnel 的 Public Hostname URL 那里改成 `http://dashboard:8501`（容器服务名）即可。
