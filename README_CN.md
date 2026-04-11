# 🦞 crypto-clawie 用户使用手册

> Hyperliquid 永续合约交易助手 · 多交易所数据聚合 · AI Agent 协同分析 · 资金费率监控 · 自动预警 · Telegram 实时推送

**版本：** 3.0.0 · **Python：** ≥ 3.9 · **协议：** MIT

---

## 目录

1. [系统架构](#1-系统架构)
2. [部署与启动](#2-部署与启动)
3. [配置说明](#3-配置说明)
4. [指令完整手册](#4-指令完整手册)
   - 4.1 [账户查询](#41-账户查询)
   - 4.2 [市场行情](#42-市场行情)
   - 4.3 [多交易所聚合](#43-多交易所聚合)
   - 4.4 [快讯](#44-快讯)
   - 4.5 [信号与报告](#45-信号与报告)
   - 4.6 [专项追踪](#46-专项追踪)
   - 4.7 [做市商阶段分析](#47-做市商阶段分析)
   - 4.8 [交易所净流量](#48-交易所净流量)
   - 4.9 [用户策略向导](#49-用户策略向导)
   - 4.10 [Agent 智能交易](#410-agent-智能交易)
   - 4.11 [数据上下文整理（配合 AI Agent）](#411-数据上下文整理配合-ai-agent)
   - 4.12 [手动交易指令](#412-手动交易指令)
   - 4.13 [套利与策略](#413-套利与策略)
   - 4.14 [链上监控](#414-链上监控)
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
│     调用本地 skill 脚本
│     每 60s 刷新价格缓存
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
pm2 start scheduler.py --name clawie-scheduler --interpreter venv/bin/python3
pm2 start bot.py       --name clawie-bot       --interpreter venv/bin/python3
pm2 save               # 开机自启
```

### 验证运行

```bash
pm2 status                        # 两个进程均应为 online
pm2 logs clawie-scheduler --lines 30
pm2 logs clawie-bot       --lines 30
```

---

## 3. 配置说明

```env
# ── Hyperliquid（必填）──────────────────────────────────────
HL_PRIVATE_KEY=0x...            # EVM 私钥，切勿提交 Git！
HL_WALLET_ADDRESS=0x...         # 对应钱包地址

# ── Telegram（必填）────────────────────────────────────────
TELEGRAM_BOT_TOKEN=             # 向 @BotFather 申请
TELEGRAM_CHAT_ID=               # 向 @userinfobot 查询你的 ID
TELEGRAM_ALERT_CHAT_ID=         # 可选：预警单独发往另一频道/话题

# 话题（Telegram 超级群 Topic）—— 可选
TELEGRAM_TOPIC_ALERT=           # 预警话题 ID
TELEGRAM_TOPIC_MARKET=          # 行情话题 ID
TELEGRAM_TOPIC_POSITION=        # 持仓话题 ID
TELEGRAM_TOPIC_TRADE=           # 交易话题 ID

# ── 推荐配置 ───────────────────────────────────────────────
BLOCKBEATS_API_KEY=             # BlockBeats 快讯 API Key
HL_USE_TESTNET=true             # 首次部署建议先测试网
AUTONOMOUS_MODE=false           # false=手动确认 true=自动执行
MAX_POSITION_SIZE_USD=500       # 单笔最大仓位（USD）
MAX_DAILY_LOSS_PCT=5            # 每日亏损熔断阈值

# ── 高级配置 ───────────────────────────────────────────────
HL_DEFAULT_LEVERAGE=3           # 默认杠杆（1–50）
HL_DEFAULT_MARGIN_MODE=cross    # cross | isolated
HL_FUNDING_ALERT_THRESHOLD=0.0005
HL_LIQ_ALERT_THRESHOLD=0.15
FETCH_INTERVAL_MIN=5
NEWS_INTERVAL_MIN=15
DAILY_REPORT_HOUR=8             # 每日报告时间（CST）

# ── 自动交易 ───────────────────────────────────────────────
AGENT_TRADE_ENABLED=false
AUTO_TRADE_SIZE_USD=50
AUTO_TRADE_MAX_POSITIONS=2
AUTO_TRADE_MIN_CONFIDENCE=0.7
AUTO_TRADE_PROFIT_PCT=3
AUTO_TRADE_STOP_PCT=2

# ── 链上监控 ───────────────────────────────────────────────
ETHERSCAN_API_KEY=
```

> **安全提示**：`.env` 已在 `.gitignore`，切勿提交。私钥懒加载，不写入日志。

---

## 4. 指令完整手册

### 4.1 账户查询

#### `/position` — 持仓明细与账户余额

```
📊 账户余额：$2,340.50 USDC
保证金使用：$680.00 / $2,340.50（29.1%）

持仓明细：
• BTC-PERP  多 ×3x  0.005 @ $94,200  未实现 PnL: +$12.30  距爆仓 18.2%
• ETH-PERP  空 ×5x  0.3   @ $3,480   未实现 PnL: -$4.20   距爆仓 9.6%
```

#### `/liq` — 爆仓风险评估

```
🛡️ 爆仓风险评估

• ETH-PERP  空  爆仓价：$3,750  距离：7.8%  🔴 高风险
• BTC-PERP  多  爆仓价：$89,100 距离：5.4%  🔴 高风险
```

距爆仓 < 5% 触发紧急预警，< 10% 触发高风险预警。

---

### 4.2 市场行情

#### `/market` — 市场概览

一键获取：资金费率 Top5 异动 + 恐慌贪婪指数 + 账户余额摘要。

#### `/funding` — 资金费率排行 Top 20

```
📈 资金费率排行（Top 20）

1. WIF     +0.1823%/8h  年化 ~200%  🔴
2. DOGE    +0.1201%/8h  年化 ~131%  🔴
3. BTC     +0.0412%/8h  年化 ~45%   🟡
```

#### `/funding BTC` — 单个资产详情

#### `/oi` / `/oi BTC` — 未平仓量排行 / 单个资产

#### `/price ETH` — 实时价格（低于 5% 的缓存自动回落 Binance 实时接口）

#### `/fng` — 恐慌贪婪指数

#### `/BTC` `/ETH` `/SOL` 等 — 快捷查询

任意 HL 支持的交易对符号，返回：价格 · 24h 涨跌 · 资金费率 · 年化 · 未平仓量。

---

### 4.3 多交易所聚合

数据覆盖：**Binance · OKX · Bybit · Gate.io · Bitget · Hyperliquid**

#### `/compare BTC` — 跨所价格对比

```
💹 BTC 跨所价格对比

• Binance   $94,231.50
• OKX       $94,228.00
• Bybit     $94,235.20
• Gate.io   $94,229.80
• Bitget    $94,230.50
• HL        $94,232.00（永续标记价）

最大价差：$7.20（0.008%）
```

#### `/exfunding BTC` — 跨所资金费率对比

```
💸 BTC 跨所资金费率

• Binance    +0.0123%/8h
• OKX        +0.0098%/8h
• Bybit      +0.0134%/8h
• Gate.io    +0.0112%/8h
• Bitget     +0.0108%/8h
• HL         +0.0412%/8h  ← 最高

HL 费率显著高于其他交易所 → 做空 HL 收费机会
```

#### `/vol BTC` — 跨所成交量对比

现货 + 合约成交量，直观看资金流向哪个平台。

#### `/divergence` — 扫描全市场跨所价差

自动扫描 BTC/ETH/SOL/BNB/XRP 等主流币，找出价差超过 0.05% 的套利机会。

#### `/listings SOL` — 查询代币上架情况

```
📋 SOL 上架情况

现货（5/5）
✅ Binance  $142.30
✅ OKX      $142.28
✅ Bybit    $142.31
✅ Gate.io  $142.27
✅ Bitget   $142.29

永续合约（6/6）
✅ Binance  +0.0089%/8h
✅ OKX      +0.0076%/8h
✅ Bybit    +0.0092%/8h
✅ Gate.io  +0.0081%/8h
✅ Bitget   +0.0085%/8h
✅ HL       +0.0234%/8h
```

适合查冷门代币是否已在目标交易所上架。

---

### 4.4 快讯

#### `/news` — 最新快讯（前 10 条）

BlockBeats 实时快讯，带时间戳。

#### `/hlnews` — HL 相关快讯

自动过滤含 "Hyperliquid / HYPE / HL" 关键词的条目。

---

### 4.5 信号与报告

#### `/alerts` — 全部异动信号扫描

```
🔔 异动信号扫描 — 发现 3 个

🔴 WIF  资金费率极端
  +0.1823%/8h · OI $180M · 24h +8.2%
  ████████░░ 82%  建议做空

🟡 SOL  资金费率偏高
  +0.0612%/8h · OI $320M
  ████░░░░░░ 45%

🔴 ETH  爆仓风险高
  持仓距爆仓 8.3%，建议减仓
  ██████████ 100%
```

#### `/report` — 今日报告

市场情绪 · 主流价格 · 资金费率异动 · 账户状态摘要

#### `/weekly` — 本周复盘报告

---

### 4.6 专项追踪

对某个代币进行定期自动报告，适合"盯盘"某个仓位或关注标的。

#### `/focus SOL` — 开启专项追踪（默认每 15 分钟）

```
✅ 已开启 SOL 专项追踪
每 15 分钟自动推送：价格 · 资金费率 · 跨所数据 · 做市商阶段 · 相关新闻
```

#### `/focus SOL 30` — 自定义间隔（单位：分钟）

#### `/focus report` — 立即生成一次报告

```
🎯 SOL 专项追踪报告
2026-04-11 14:30 UTC

【价格与资金费率】
标记价：$142.30  24h: +3.2%
HL 资金费率：+0.0234%/8h（年化 ~26%）

【做市商阶段】
当前：积累期（置信度 68%）
信号：OI 低于均值 · 资金费率偏低 · 价格横盘

【跨所资金费率】
Binance: +0.0089% | OKX: +0.0076% | Bybit: +0.0092%

【相关新闻】
• Solana DeFi TVL 突破 $12B...
```

#### `/focus status` — 查看追踪状态

#### `/focus cancel` — 取消追踪

---

### 4.7 做市商阶段分析

识别主力做市商的当前操作阶段，辅助判断趋势。

#### `/mm SOL` — 分析指定币种

```
🕵️ SOL 做市商阶段分析

当前阶段：📦 积累期（置信度 72%）

评分明细：
  积累：8.2  清洗：2.1  出货：1.4  拉升准备：3.8

关键信号：
  · 资金费率偏低（-0.002%/8h）
  · 价格振幅收窄
  · OI 低于 30 日均值

策略提示：
  主力可能在低调建仓，关注放量突破信号
  适合小仓位试探性做多，止损设在近期低点
```

**四个阶段含义：**

| 阶段 | 含义 | 常见特征 |
|---|---|---|
| 📦 积累期 | 主力悄悄建仓 | 价格横盘 · 成交量低 · 资金费率偏低 |
| 🌀 清洗期 | 人为制造恐慌洗掉浮筹 | 价格剧烈震荡 · 成交量异常高 · 价差变大 |
| 📤 出货期 | 主力逐步变现 | 价格上涨但费率极端 · OI 下降 |
| 🚀 拉升准备 | 即将发动行情 | 资金费率负值 · 价格低迷 · OI 低位 |

#### `/mm scan` 或 `/mm` — 全市场扫描 Top 10

---

### 4.8 交易所净流量

监控主要 CEX 热钱包的代币净流入/流出，判断大户动向。

#### `/netflow` — 分析过去 24h 净流量（默认 USDT）

```
🌊 交易所净流量分析（24h · USDT · ERC-20）

• Binance   流入 $124M  流出 $98M   净流入 +$26M  📥
• OKX       流入 $87M   流出 $103M  净流出 -$16M  📤
• Bybit     流入 $43M   流出 $31M   净流入 +$12M  📥
• Coinbase  流入 $56M   流出 $89M   净流出 -$33M  📤

综合信号：🟢 BULLISH（交易所整体净流出，筹码向链上转移）
```

净流出 = 用户将代币提走 → 看涨信号（减少抛压）
净流入 = 用户将代币充入 → 看跌信号（可能准备卖出）

#### `/netflow 12` — 查看过去 12 小时

#### `/netflow signal BTC` — BTC 综合信号（流量 + HL 资金费率）

#### `/netflow wallets` — 查看监控的交易所热钱包地址列表

> 需要配置 `ETHERSCAN_API_KEY`（etherscan.io 免费注册）

---

### 4.9 用户策略向导

通过对话向导设置一套个性化的自动交易策略，Agent 会按策略筛选并执行机会。

#### `/strategy new` — 启动 6 步配置向导

```
🎯 策略配置向导

共 6 步，随时发送 /cancel 退出。

📌 第 1/6 步
你想交易哪个币种？
例：BTC、ETH、SOL
```

向导步骤：**币种 → 方向 → 入场触发 → 仓位金额 → 止损% → 止盈%**

入场触发选项：
- `funding` — 资金费率超阈值时自动入场
- `agent` — 由 Agent 多因子评分决策
- `manual` — 由我手动确认，Agent 帮我执行

#### `/strategy show` — 查看当前策略

```
🎯 我的交易策略

• 标的：SOL
• 方向：long
• 入场方式：资金费率触发
• 每笔仓位：$100 USDC
• 止损：-2%
• 止盈：+5%
• 状态：✅ 启用
```

#### `/strategy off` / `/strategy on` — 暂停 / 恢复策略

#### `/strategy delete` — 删除策略

---

### 4.10 Agent 智能交易

基于多因子量化评分（非 AI 大模型）自动扫描和执行交易机会。

#### `/agent scan` — 全市场多因子分析

```
🤖 Agent 市场分析
找到 3 个机会

📉 WIF SHORT
  置信度：`████████░░ 80%`
  因子：费率极端 +0.1823% | OI $180M 极大 | 动量同向 +8.2%
  建议仓位：`$89` USDC

📉 DOGE SHORT
  置信度：`██████░░░░ 62%`
  因子：费率很高 +0.1201% | Binance同向
  建议仓位：`$71` USDC
```

**评分权重：**

| 因子 | 权重 |
|---|---|
| 资金费率幅度 | 最高 50% |
| 未平仓量规模 | 最高 20% |
| 价格动量同向 | 最高 20% |
| Binance 跨所确认 | 最高 15% |

#### `/agent status` — Agent 状态与近期决策记录

#### `/agent history` — 历史决策记录（最近 10 条）

#### `/agent decide` — 立即生成可执行决策

若配置了 `/strategy`，优先按策略约束过滤（指定币种 + 方向 + 仓位大小）。

---

### 4.11 数据上下文整理（配合 AI Agent）

这组命令**不调用 AI**，而是将本地缓存数据整理成可读的上下文块，发到群里后 @AI Agent 进行深度分析。

#### `/ask 现在 SOL 适合做多吗？`

```
📊 市场数据上下文

数据采集时间：2026-04-11 14:30 UTC

【SOL Hyperliquid 数据】
标记价格：$142.30
24h 涨跌：+3.2%
资金费率(8h)：+0.0234%
年化资金费率：+25.7%
未平仓量：$890M

【资金费率 Top5 异动】
  WIF: +0.1823%/8h (年化+200%)
  ...

────────────────────

❓ 问题：现在 SOL 适合做多吗？
```

发出后直接 @AI Agent，它会结合上下文回答。

#### `/deep BTC` — 整理指定币种深度数据

包含：HL 数据 + 做市商阶段 + 跨所实时资金费率 + 账户持仓 + 新闻。

#### `/advice` — 整理当前持仓数据

将所有持仓和市场数据整理发出，@AI Agent 给出持仓管理建议。

---

### 4.12 手动交易指令

> 开启 `AUTONOMOUS_MODE=true` 后，交易直接执行；否则显示确认键盘。

#### 开仓

```
/trade open ETH long 100       # 做多 ETH $100
/trade open BTC short 200 3    # 做空 BTC $200，3倍杠杆
```

#### 平仓

```
/trade close ETH
```

#### 其他

```
/trade cancel ETH 12345        # 撤单（需 order_id）
/trade leverage ETH 5 cross    # 设置杠杆和保证金模式
/trade                         # 查看当前持仓
/override_circuit              # 临时覆盖当日亏损熔断（1小时有效）
```

---

### 4.13 套利与策略

#### 资金费率套利

Delta 中性策略：做空 HL 永续（收取费率）+ 买入等量 Binance 现货（对冲价格风险）

```
/arb scan              # 扫描套利机会
/arb open BTC 500      # 记录套利仓位（$500 USDC）
/arb status            # 查看持仓状态 + 预估收益
/arb close BTC         # 平仓（附精确 Binance 平仓数量）
```

入场条件：`|funding_8h| ≥ 0.05%`（年化约 54%）
离场条件：`|funding_8h| ≤ 0.01%`

> HL 腿：`AUTONOMOUS_MODE=true` 时自动执行；Binance 现货腿**始终手动**，Bot 给出精确数量。

#### 网格交易

```
/grid BTC 90000 100000 10 50   # 低价 高价 格数 每格USD
/grid                          # 查看活跃网格
/grid cancel <grid_id>         # 取消网格
```

#### 回测

```
/backtest                      # 合成数据快速验证参数
```

---

### 4.14 链上监控

#### 添加地址监控

```
/watch add ETH 0x1234...abcd 标签名    # 监控以太坊地址
/watch add ETH 0x1234...abcd 标签 5    # 阈值 5 ETH 才预警
```

#### 查询 / 管理

```
/watch list                    # 查看所有监控地址
/watch ETH 0x1234...           # 查看近期交易
/watch remove ETH 0x1234...    # 移除监控
/chains                        # 链上监控概览
```

---

## 5. AI Agent 协同使用指南

### 两种触发方式

**方式 A：直接 @AI Agent**

在群里直接 @mention AI Agent，它可以：
- 自行读取 `data/*.json`、`memory/*.json` 缓存文件
- 联网搜索最新新闻和链上数据
- 综合本地数据 + 互联网信息作答

```
你：@AIBot SOL 现在的资金费率情况怎么样，适合套利吗？
AI：根据 HL 最新数据，SOL 资金费率为 +0.0234%/8h，年化约 26%...
```

**方式 B：脚本先整理数据，再 @AI Agent**

1. 先用 `/deep SOL` 整理深度数据发到群里
2. 再 @AI Agent 并提问

```
你：/deep SOL
Bot：[输出 SOL 全量数据上下文]

你：@AIBot 基于以上数据，现在适合开多还是空？
AI：从做市商阶段（积累期）和当前资金费率来看...
```

### 脚本 vs AI Agent 的分工

| 需求 | 推荐方式 |
|---|---|
| 快速查价格、费率、持仓 | 直接 slash 命令 |
| 预警推送、定时报告 | 自动（调度器） |
| 分析某个币种的市场结构 | `/deep` + @AI Agent |
| 持仓管理建议 | `/advice` + @AI Agent |
| 综合判断入场时机 | @AI Agent（可联网） |
| 自动执行套利 | `/agent decide` |

---

## 6. 预警系统

### 信号阈值

| 信号类型 | 触发条件 | 基础置信度 |
|---|---|---|
| 资金费率极端 | `\|rate_8h\| ≥ 0.1%` | 80–100% |
| 资金费率偏高 | `\|rate_8h\| ≥ 0.05%` | 40–70% |
| OI 确认加分 | OI > $50M（同方向） | +20% |
| 价格动量加分 | 24h 涨跌 > 3%（同方向） | +10% |
| 低流动性折扣 | UTC 16:00–22:00（北京凌晨） | −10% |
| 爆仓风险紧急 | 距爆仓 < 5% | 100% |
| 爆仓风险高 | 距爆仓 < 10% | 70% |
| 爆仓风险中等 | 距爆仓 < 20% | 40% |

置信度 < 40% 的信号不推送。

### SQLite 去重机制

| 预警类型 | 重复抑制时长 |
|---|---|
| 资金费率 | 8 小时 |
| 爆仓风险 | 4 小时 |
| 新闻快讯 | 24 小时 |

重启调度器不会重置去重状态。随时发 `/alerts` 手动扫描（不受去重限制）。

---

## 7. 安全机制

| 保护层 | 行为 |
|---|---|
| `AUTONOMOUS_MODE=false`（默认） | 所有交易显示确认键盘，需手动点击 |
| `MAX_POSITION_SIZE_USD` | 每笔仓位硬上限，超额请求直接拒绝 |
| `MAX_DAILY_LOSS_PCT` | 当日亏损超阈值自动停止新开仓（熔断） |
| 私钥安全 | 懒加载，不写入日志，签名在本地完成 |
| 爆仓预警 | 距爆仓 < 20%/10%/5% 三级预警，SQLite 持久化 |
| 熔断覆盖 | `/override_circuit` 临时覆盖，有效期 1 小时 |

---

## 8. 定时任务

| 任务 | 间隔 | 说明 |
|---|---|---|
| 价格快速刷新 | 每 60s | 仅刷新 Binance 价格，保持行情实时 |
| 全量数据抓取 | 每 5min | HL市场 + HL账户 + FNG + 新闻，异步并发 |
| 资金费率预警 | 每 5min | 新数据落地后自动扫描 |
| 爆仓风险预警 | 每 5min | 检查所有持仓爆仓距离 |
| 新闻检查 | 每 15min | BlockBeats HL 关键词过滤 |
| 专项追踪 | 每 5min | 检查 focus.json，到时间则推送报告 |
| 每日报告 | 08:00 CST | 市场 + 账户汇总 |
| 每周复盘 | 周一 08:00 CST | 周报 |
| DB 清理 | 每 1 小时 | 清理过期预警去重记录 |

---

## 9. 日常运维

```bash
# 查看进程状态
pm2 status

# 查看日志
pm2 logs clawie-scheduler --lines 50
pm2 logs clawie-bot       --lines 50

# 重启
pm2 restart clawie-scheduler
pm2 restart clawie-bot

# 更新代码
git pull origin main
pip install -r requirements.txt
pm2 restart all

# 手动触发一次数据抓取
python3 fetcher.py

# 查看缓存数据
cat data/hl_market.json   | python3 -m json.tool
cat data/hl_account.json  | python3 -m json.tool
cat memory/focus.json
cat memory/my_strategy.json
```

---

## 10. 常见问题

**Q: 调度器启动后 Telegram 没有收到消息？**

检查 `pm2 logs clawie-scheduler` 有无报错；确认 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 填写正确；向机器人发送 `/status` 测试连通性。

**Q: `/price BTC` 显示的是实时价格还是缓存？**

若市场快照缓存超过 90 秒，脚本会自动回落到 Binance 实时接口并在结果中标注"✨ 实时价格"。此外调度器每 60s 刷新一次价格缓存。

**Q: `/listings` 查询某个代币，某交易所显示 ❌？**

该代币确实未在该交易所上市，或交易对名称不同（如 Bitget 部分代币用 USDT 以外的计价）。

**Q: `/mm` 的做市商阶段准确吗？**

这是基于资金费率、价格变化、换手率、跨所价差的量化模型，提供参考而非预测。结合 AI Agent 分析效果更好。

**Q: `/netflow` 净流量数据可信吗？**

数据来自 Etherscan 的 ERC-20 转账记录，仅覆盖以太坊链上的 USDT/USDC 流量，不含 BNB Chain 或其他链。作为辅助参考，不能单独作为决策依据。

**Q: `AUTONOMOUS_MODE=true` 安全吗？**

开启后机器人直接签名提交订单。强烈建议先在测试网（`HL_USE_TESTNET=true`）验证，并配置合理的 `MAX_POSITION_SIZE_USD` 和 `MAX_DAILY_LOSS_PCT`。

**Q: AI Agent 能访问哪些数据？**

AI Agent 可读取本地 `data/*.json` 和 `memory/*.json` 缓存文件，同时可联网搜索实时信息。脚本 Bot 每 60s–5min 刷新缓存，AI Agent 读到的本地数据最多延迟 5 分钟。

**Q: 如何完全停止机器人？**

```bash
pm2 stop all && pm2 save
```

已挂的 HL 限价单不会自动撤销，需手动在 Hyperliquid 界面处理。

---

## 协议

MIT
