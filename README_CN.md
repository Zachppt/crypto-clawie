# 🦞 crypto-clawie 用户使用手册

> 通用 Crypto 交易助手 · 多交易所数据聚合 · AI Agent 协同分析 · 资金费率监控 · 自动预警 · Telegram 实时推送
> Hyperliquid 是支持的交易所之一，不是唯一依赖。

**版本：** 4.0.0 · **Python：** ≥ 3.9 · **协议：** MIT

---

## 目录

1. [系统架构](#1-系统架构)
2. [部署与启动](#2-部署与启动)
3. [配置说明](#3-配置说明)
4. [指令完整手册](#4-指令完整手册)
5. [AI Agent 协同使用指南](#5-ai-agent-协同使用指南)
6. [预警系统](#6-预警系统)
7. [安全机制](#7-安全机制)
8. [定时任务](#8-定时任务)
9. [日常运维](#9-日常运维)
10. [常见问题](#10-常见问题)

---

## 1. 系统架构

crypto-clawie 由**两个角色**组成，共存于同一个 Telegram 群组：

```
Telegram 群组
├── @ScriptBot（bot.py）           ← slash 命令 → 直接返回结构化数据
│     通过 ccxt 接入 Binance/OKX/Bybit/HL
│     WebSocket 实时价格（ws_feeder.py）
│     每 5min 刷新完整市场数据
│
└── @AI Agent（OpenClaw）           ← @mention → AI 推理 + 联网搜索
      读取 data/*.json 本地缓存
      自定义 LLM（Claude / GPT / Gemini 等）
      可访问互联网实时信息
```

**两种使用模式：**

| 模式 | 方式 | 适合场景 |
|---|---|---|
| 直接查数据 | 输入 `/命令` | 看价格、资金费率、持仓、预警等结构化数据 |
| AI 深度分析 | `@AI Agent 问题` 或 `/ask /deep /advice` | 需要推理、判断、综合分析 |

---

## 2. 部署与启动

### 方式一：VPS 一键部署（推荐）

```bash
bash <(curl -s https://raw.githubusercontent.com/Zachppt/crypto-clawie/main/setup.sh)
```

脚本自动完成：克隆 → 创建虚拟环境 → 安装依赖 → 引导填写 `.env` → pm2 启动

### 方式二：手动部署

```bash
git clone https://github.com/Zachppt/crypto-clawie.git
cd crypto-clawie
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env          # 填入真实值
```

启动后台进程：

```bash
# 推荐用 pm2
pm2 start scheduler.py --name clawie-scheduler --interpreter venv/bin/python3
pm2 start bot.py       --name clawie-bot       --interpreter venv/bin/python3
pm2 start ws_feeder.py --name clawie-ws        --interpreter venv/bin/python3
pm2 save               # 开机自启

# 或用 nohup
nohup venv/bin/python3 bot.py       > logs/bot.log       2>&1 &
nohup venv/bin/python3 scheduler.py > logs/scheduler.log 2>&1 &
nohup venv/bin/python3 ws_feeder.py > logs/ws.log        2>&1 &
```

### 验证运行

```bash
pm2 status
pm2 logs clawie-bot --lines 30
```

---

## 3. 配置说明

```env
# ── Telegram（必填）────────────────────────────────────────
TELEGRAM_BOT_TOKEN=             # 向 @BotFather 申请
TELEGRAM_CHAT_ID=               # 向 @userinfobot 查询
TELEGRAM_ALERT_CHAT_ID=         # 可选：预警单独发往另一频道

# 话题（Telegram 超级群 Topic）—— 可选
TELEGRAM_TOPIC_ALERT=
TELEGRAM_TOPIC_MARKET=
TELEGRAM_TOPIC_POSITION=
TELEGRAM_TOPIC_TRADE=

# ── 交易所（至少配置一个，用于账户/交易类命令）──────────────
TRADING_EXCHANGE=hyperliquid    # binance | okx | bybit | hyperliquid

# Hyperliquid（用于 HL 交易 + 持仓监控）
HL_PRIVATE_KEY=0x...            # EVM 私钥，切勿提交 Git！
HL_WALLET_ADDRESS=0x...
HL_USE_TESTNET=true             # 首次部署建议先测试网

# Binance
BINANCE_API_KEY=
BINANCE_SECRET_KEY=

# OKX
OKX_API_KEY=
OKX_SECRET_KEY=
OKX_PASSPHRASE=

# Bybit
BYBIT_API_KEY=
BYBIT_SECRET_KEY=

# ── 推荐配置 ───────────────────────────────────────────────
AUTONOMOUS_MODE=false           # false=手动确认 true=自动执行
MAX_POSITION_SIZE_USD=500
MAX_DAILY_LOSS_PCT=5

# ── 快讯 ───────────────────────────────────────────────────
BLOCKBEATS_API_KEY=             # BlockBeats 快讯 API Key

# ── 链上监控 ───────────────────────────────────────────────
ETHERSCAN_API_KEY=

# ── 高级配置 ───────────────────────────────────────────────
HL_DEFAULT_LEVERAGE=3
HL_DEFAULT_MARGIN_MODE=cross
HL_FUNDING_ALERT_THRESHOLD=0.0005
HL_LIQ_ALERT_THRESHOLD=0.15
FETCH_INTERVAL_MIN=5
NEWS_INTERVAL_MIN=15
DAILY_REPORT_HOUR=8

# ── 自动交易 ───────────────────────────────────────────────
AGENT_TRADE_ENABLED=false
AUTO_TRADE_SIZE_USD=50
AUTO_TRADE_MAX_POSITIONS=2
AUTO_TRADE_MIN_CONFIDENCE=0.7
```

> **重要提示：** 行情类命令（`/market`、`/funding`、`/alerts`、`/compare` 等）**无需任何 API Key**，使用 Binance/OKX 公开接口。账户/交易类命令才需要配置对应交易所的 Key。

---

## 4. 指令完整手册

### 4.1 账户查询（需配置 API Key）

#### `/position` — 持仓明细与账户余额

读取 `TRADING_EXCHANGE` 环境变量决定查哪个所：

```
/position              # 默认交易所
/position binance      # 指定 Binance
/position okx          # 指定 OKX
/position bybit        # 指定 Bybit
/position hl           # 指定 Hyperliquid
```

#### `/liq` — 爆仓风险评估（仅 Hyperliquid）

距爆仓 < 5% 触发紧急预警，< 10% 高风险预警。

---

### 4.2 市场行情（无需配置）

#### `/market` — 主流币跨所行情 + 恐慌贪婪指数

#### `/funding` — 跨所资金费率对比

```
/funding               # 全市场排行 Top 20
/funding BTC           # 单币种：Binance / OKX / Bybit / HL 对比
```

#### `/oi` — Hyperliquid 全市场未平仓量排行 Top 10

> 查询指定币种的 OI 分析请使用 `/mm BTC`（OI 是 MM 分析的子集）

#### `/price ETH` — 实时价格（WebSocket 优先，Binance 兜底）

#### `/fng` — 恐慌贪婪指数

#### `/BTC` `/ETH` `/SOL` 等 — 跨所实时价格 + 资金费率

---

### 4.3 多交易所聚合（无需配置）

数据覆盖：**Binance · OKX · Bybit · Gate.io · Bitget · Hyperliquid**

#### `/compare BTC` — 跨所价格 + 成交量 + 价差

一次命令同时展示各所现货价格、24h 成交量、最大价差：

```
💹 BTC 跨所价格对比

现货价格
• Binance   $94,231.50  成交量 $28.4B
• OKX       $94,228.00  成交量 $12.1B
• Bybit     $94,235.20  成交量 $8.3B
• Gate.io   $94,229.80  成交量 $2.1B
• Bitget    $94,230.50  成交量 $1.8B
• HL（永续）$94,232.00

最大价差：$7.20（0.008%）
```

#### `/divergence` — 扫描主流币跨所价差异动

#### `/listings SOL` — 查询代币上架情况（现货 + 合约各所）

---

### 4.4 快讯

#### `/news` — 最新快讯（前 10 条）

#### `/news hl` — HL 相关快讯

---

### 4.5 信号与报告

#### `/alerts` — 全市场多因子信号扫描

数据源：**Binance 永续合约公开 API（200+ 标的，无需 Key）**

**评分因子：**

| 因子 | 权重 |
|---|---|
| 资金费率幅度 | 最高 50% |
| 24h 成交量确认 | 最高 20% |
| 价格动量同向 | 最高 20% |
| OKX 跨所确认 | 最高 15% |

```
🤖 Agent 市场分析
找到 3 个机会

📉 WIF SHORT
  置信度：`████████░░ 80%`
  因子：费率极端 +0.1823% | 成交量高 $3.2B | 动量同向 +8.2% | OKX同向
  建议仓位：$89 USDC
```

#### `/report` — 今日报告

#### `/weekly` — 本周复盘报告

---

### 4.6 专项追踪

对某个代币定期自动报告，适合盯盘某个仓位或关注标的。

```
/track SOL             # 开启，默认每 15 分钟
/track SOL 30          # 自定义间隔（分钟）
/track report          # 立即生成深度报告（数据整理后 @AI Agent 分析）
/track status          # 查看追踪配置
/track cancel          # 停止追踪
```

---

### 4.7 做市商阶段分析（跨所：Binance + OKX + Bybit + HL）

识别主力做市商的当前操作阶段，辅助判断趋势。

```
/mm BTC                # 跨所综合评分（资金费率 + OI 分布 + 阶段判断）
/mm BTC cross          # 只看跨所数据
/mm scan               # 全市场快扫
```

**四个阶段：**

| 阶段 | 含义 | 常见特征 |
|---|---|---|
| 📦 积累期 | 主力悄悄建仓 | 价格横盘 · 费率低 · OI 低位 |
| 🌀 清洗期 | 制造恐慌洗浮筹 | 价格剧震 · 成交量异常 |
| 📤 出货期 | 主力逐步变现 | 价格上涨但费率极端 · OI 下降 |
| 🚀 拉升准备 | 即将发动行情 | 费率负值 · 价格低迷 |

---

### 4.8 技术分析

```
/ta BTC                # BTC 1h 全分析（RSI + MA + BB + MACD）
/ta ETH 4h             # 指定时间周期
/ta SOL 1d signal      # 只看交易信号
```

---

### 4.9 交易所净流量

```
/netflow               # 过去 24h CEX 净流量
/netflow 12            # 过去 12 小时
/netflow signal BTC    # BTC 综合信号（流量 + 资金费率）
/netflow wallets       # 监控的热钱包地址列表
```

> 需要配置 `ETHERSCAN_API_KEY`

---

### 4.10 用户策略向导

```
/strategy new          # 6 步配置向导（币种→方向→触发→仓位→止损→止盈）
/strategy show         # 查看当前策略
/strategy on / off     # 启用 / 暂停
/strategy delete       # 删除策略
```

---

### 4.11 数据上下文整理（配合 AI Agent）

这组命令**不调用 AI**，而是将数据整理成可读的上下文块，发到群里后 @AI Agent 深度分析。

#### `/ask 现在 SOL 适合做多吗？`

整理当前市场数据 + 附上你的问题，@AI Agent 分析。

#### `/deep BTC` — 指定币种深度数据

包含：跨所行情 + 做市商阶段 + 跨所资金费率 + 技术信号。

#### `/advice` — 整理当前持仓数据

将持仓和市场数据整理发出，@AI Agent 给出持仓管理建议。

---

### 4.12 手动交易（需配置 API Key）

```
/trade open ETH long 100           # 做多 ETH $100（默认交易所）
/trade open BTC short 200 binance  # 在 Binance 做空 BTC $200
/trade close ETH                   # 平仓 ETH
/trade leverage ETH 5 cross        # 设置杠杆和保证金模式
/override_circuit                  # 临时覆盖当日亏损熔断（1小时）
```

---

### 4.13 套利与策略

#### 资金费率套利

```
/arb scan              # 扫描套利机会
/arb open BTC 500      # 记录套利仓位（$500 USDC）
/arb status            # 查看持仓 + 预估收益
/arb close BTC         # 平仓
```

#### 网格交易（Hyperliquid）

```
/grid BTC 90000 100000 10 50       # 低价 高价 格数 每格USD
/grid                              # 查看活跃网格
/grid cancel <grid_id>             # 取消
```

---

### 4.14 链上监控

```
/watch add ETH 0x1234... 标签      # 添加地址监控
/watch list                        # 查看监控列表
/watch ETH 0x1234...               # 查看近期交易
/watch remove ETH 0x1234...        # 移除
/chains                            # 链上监控概览
```

---

### 4.15 系统

```
/status                # Bot 运行状态 + 各交易所 API Key 配置情况
/help                  # 完整指令列表
```

---

## 5. AI Agent 协同使用指南

### 两种触发方式

**方式 A：直接 @AI Agent**

```
你：@AIBot SOL 现在适合套利吗？
AI：根据当前数据，SOL 资金费率为 +0.0234%/8h，年化约 26%...
```

**方式 B：脚本先整理数据，再 @AI Agent**

```
你：/deep SOL
Bot：[输出 SOL 全量数据上下文]

你：@AIBot 基于以上数据，现在适合开多还是空？
AI：从做市商阶段（积累期）和当前资金费率来看...
```

### 分工建议

| 需求 | 推荐方式 |
|---|---|
| 快速查价格、费率、持仓 | 直接 slash 命令 |
| 预警推送、定时报告 | 自动（调度器） |
| 分析某个币种的市场结构 | `/deep` + @AI Agent |
| 持仓管理建议 | `/advice` + @AI Agent |
| 综合判断入场时机 | @AI Agent（可联网） |
| 自动扫描交易机会 | `/alerts` |

---

## 6. 预警系统

### 信号阈值

| 信号类型 | 触发条件 |
|---|---|
| 资金费率极端 | `\|rate_8h\| ≥ 0.2%` → 最高得分 50% |
| 资金费率很高 | `\|rate_8h\| ≥ 0.1%` → 35% |
| 资金费率偏高 | `\|rate_8h\| ≥ 0.05%` → 20% |
| 成交量高 | 24h 成交量 > $1B → +20% |
| 动量同向 | 24h 涨跌幅同方向 > 8% → +20% |
| OKX 跨所确认 | OKX 费率同方向 > 0.03%/8h → +15% |
| 爆仓风险紧急 | 距爆仓 < 5% |

总分 ≥ 60% 的标的才会出现在 `/alerts` 结果中。

### SQLite 去重

| 预警类型 | 重复抑制时长 |
|---|---|
| 资金费率 | 8 小时 |
| 爆仓风险 | 4 小时 |
| 新闻快讯 | 24 小时 |

随时发 `/alerts` 手动扫描（不受去重限制）。

---

## 7. 安全机制

| 保护层 | 行为 |
|---|---|
| `AUTONOMOUS_MODE=false`（默认） | 所有交易显示确认键盘，需手动点击 |
| `MAX_POSITION_SIZE_USD` | 每笔仓位硬上限 |
| `MAX_DAILY_LOSS_PCT` | 当日亏损超阈值自动停止新开仓（熔断） |
| 私钥安全 | 懒加载，不写入日志，签名在本地完成 |
| 熔断覆盖 | `/override_circuit` 临时覆盖，有效期 1 小时 |

---

## 8. 定时任务

| 任务 | 间隔 | 说明 |
|---|---|---|
| 价格快速刷新 | 每 60s | Binance 价格，保持行情实时 |
| 全量数据抓取 | 每 5min | 市场快照 + 账户 + FNG + 新闻 |
| 资金费率预警 | 每 5min | 新数据落地后自动扫描 |
| 爆仓风险预警 | 每 5min | 检查所有持仓爆仓距离 |
| 新闻检查 | 每 15min | BlockBeats 关键词过滤 |
| 专项追踪 | 每 5min | 检查 focus.json，到时间则推送 |
| 每日报告 | 08:00 CST | 市场 + 账户汇总 |
| 每周复盘 | 周一 08:00 CST | 周报 |
| DB 清理 | 每 1 小时 | 清理过期预警去重记录 |

---

## 9. 日常运维

```bash
# 查看进程状态
pm2 status

# 查看日志
pm2 logs clawie-bot       --lines 50
pm2 logs clawie-scheduler --lines 50

# 重启
pm2 restart clawie-bot
pm2 restart clawie-scheduler

# 更新代码
git pull origin main
pip install -r requirements.txt
pm2 restart all

# 查看缓存数据
cat data/market_snapshot.json | python3 -m json.tool
cat data/hl_account.json      | python3 -m json.tool
cat memory/my_strategy.json
```

---

## 10. 常见问题

**Q: 行情命令（/market、/funding 等）需要填 API Key 吗？**

不需要。行情类命令全部使用 Binance/OKX 公开接口，无需任何 Key。只有 `/position`、`/trade` 等账户类命令才需要配置对应交易所的 Key。

**Q: 我没有 Hyperliquid 账户，还能用吗？**

可以。大部分功能与 HL 无关。只有 `/liq`（爆仓风险）、`/grid`（网格）是 HL 专属。交易功能可以配置 Binance/OKX/Bybit 任意一个使用。

**Q: /alerts 扫描的是哪些标的？**

Binance 永续合约全市场（200+ 标的），通过两次并行 API 请求获取，无需 Key，每次调用都是实时数据。

**Q: /oi BTC 怎么用？**

`/oi` 无参数时显示 Hyperliquid 全市场 OI 排行。`/oi BTC` 会提示你改用 `/mm BTC`，因为 OI 数据已经包含在 MM 分析里，不重复展示。

**Q: /compare 和 /vol 的区别？**

`/compare BTC` 同时展示各所价格 + 成交量 + 价差，是完整的跨所对比。`/vol` 是旧命令的别名，会自动重定向到 `/compare`。

**Q: /mm 的做市商阶段准确吗？**

这是基于资金费率、OI 变化、价格动量、跨所价差的量化模型，提供参考而非预测。结合 AI Agent 分析效果更好。

**Q: AUTONOMOUS_MODE=true 安全吗？**

开启后机器人直接签名提交订单。强烈建议先在测试网（`HL_USE_TESTNET=true`）验证，并配置合理的 `MAX_POSITION_SIZE_USD` 和 `MAX_DAILY_LOSS_PCT`。

**Q: 如何完全停止机器人？**

```bash
pm2 stop all && pm2 save
```

---

## 协议

MIT
