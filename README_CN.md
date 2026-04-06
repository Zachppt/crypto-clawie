# 🤖 crypto-clawie

> 基于 **OpenClaw 框架**构建的 Hyperliquid 永续合约交易 Agent。
> 实时监控资金费率、执行合约订单、检测爆仓风险，并通过 Telegram 进行双向交互。

**版本：** 1.0.0 · **Python：** ≥ 3.10 · **协议：** MIT

---

## 功能概览

| 技能 | 描述 |
|---|---|
| 📡 **HL 监控** | 资金费率、未平仓量、爆仓风险、账户持仓 |
| ⚡ **HL 交易** | 开多/开空、平仓、设置杠杆、撤单 |
| 📊 **行情数据** | Binance 价格追踪、恐慌贪婪指数 |
| 🔔 **异动预警** | 资金费率异常、价格波动、爆仓风险预警 |
| 📰 **快讯** | BlockBeats 实时快讯，HL 相关事件过滤 |
| 📋 **报告** | 每日/每周市场与账户汇总 |

---

## 架构

```
OpenClaw（Telegram ↔ LLM）
        │
        ▼
   AGENTS.md          ← 会话协议（启动时读取顺序）
   SOUL.md            ← Agent 身份与行为准则
        │
        ▼
   skills/
   ├── hl_trade/      ← Hyperliquid 订单执行（官方 SDK + EIP-712）
   ├── hl_monitor/    ← 资金费率、持仓、爆仓风险
   ├── crypto_data/   ← 价格数据与恐慌贪婪
   ├── crypto_alert/  ← 信号扫描
   ├── crypto_news/   ← BlockBeats 快讯
   └── crypto_report/ ← 每日/每周报告

后台进程（pm2 管理）：
   fetcher.py         ← 每 5 分钟缓存市场 + HL 数据
   scheduler.py       ← 监控预警，主动推送 Telegram 通知
```

---

## 快速开始

### 第一步：VPS 一键部署

```bash
bash <(curl -s https://raw.githubusercontent.com/Zachppt/crypto-clawie/main/setup.sh)
```

脚本自动完成：
- 删除旧 `crypto-agent` 目录
- 克隆本项目到 `~/crypto-clawie`
- 创建 Python 虚拟环境并安装依赖
- 引导填写 `.env` 配置
- 更新 OpenClaw workspace 配置
- 通过 pm2 启动调度器

### 第二步：配置 `.env`

```env
# 交易必填
HL_PRIVATE_KEY=0x...          # EVM 私钥，切勿提交 Git
HL_WALLET_ADDRESS=0x...       # 对应钱包地址

# Telegram 必填
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 推荐配置
BLOCKBEATS_API_KEY=
HL_USE_TESTNET=false          # 先用测试网验证
AUTONOMOUS_MODE=false         # 确认无误后再开启自动交易
MAX_POSITION_SIZE_USD=1000    # 单笔最大仓位
```

### 第三步：验证运行状态

```bash
pm2 status                    # 查看 clawie-scheduler 是否 online
pm2 logs clawie-scheduler     # 查看实时日志
cat ~/crypto-clawie/data/hl_market.json | python3 -m json.tool
```

---

## 目录结构

```
crypto-clawie/
├── AGENTS.md              ← OpenClaw 会话协议
├── SOUL.md                ← Agent 身份与行为准则
├── USER.md                ← 用户偏好（Zach）
├── MEMORY.md              ← 长期记忆
├── HEARTBEAT.md           ← 定时任务说明
├── TOOLS.md               ← API 与工具参考
├── fetcher.py             ← 数据抓取（Binance + HL + BlockBeats）
├── scheduler.py           ← 后台调度器（APScheduler）
├── setup.sh               ← VPS 一键部署脚本
├── requirements.txt
├── .env.example
├── skills/
│   ├── base.py            ← 技能基类
│   ├── hl_trade/          ← Hyperliquid 交易执行
│   ├── hl_monitor/        ← HL 市场监控
│   ├── crypto_data/       ← 价格与行情数据
│   ├── crypto_alert/      ← 异动信号检测
│   ├── crypto_news/       ← 新闻快讯
│   └── crypto_report/     ← 报告生成
├── data/                  ← JSON 缓存（自动创建）
├── memory/                ← 每日对话记录
├── reports/               ← 生成的报告文件
└── logs/                  ← 脚本运行日志
```

---

## Hyperliquid 交易说明

所有订单通过官方 `hyperliquid-python-sdk` 使用 **EIP-712** 签名：

```
HL_PRIVATE_KEY → eth_account.Account → Exchange SDK → Hyperliquid API
```

- 私钥**懒加载** — 仅在触发交易时读取，启动时不接触
- 不写入任何文件或日志
- 自动交易默认关闭（`AUTONOMOUS_MODE=false`）

### Telegram 可用指令示例

```
查看我的持仓和账户余额
查看 BTC 资金费率
以 3 倍杠杆做多 ETH，100 USDC
平仓 ETH
设置 BTC 杠杆为 5 倍
撤销订单 [order_id]
查看市场异动信号
今日报告
```

---

## 定时任务

| 任务 | 频率 | 说明 |
|---|---|---|
| `fetch_market` | 每 5 分钟 | HL 资金费率/OI/价格 + Binance 快照 |
| `fetch_hl_account` | 每 5 分钟 | 账户持仓、保证金、爆仓距离 |
| `check_alerts` | 每 5 分钟 | 扫描预警信号 → Telegram 推送 |
| `fetch_news` | 每 15 分钟 | BlockBeats 快讯更新 |
| `daily_report` | 每日 08:00 CST | 市场 + 账户每日汇总 |
| `weekly_report` | 周一 08:00 CST | 每周复盘报告 |

---

## 预警阈值

| 信号 | 触发条件 | 级别 |
|---|---|---|
| 资金费率偏高 | \|rate_8h\| ≥ 0.05% | ⚠️ 警告 |
| 资金费率极端 | \|rate_8h\| ≥ 0.1% | 🔴 严重 |
| 未平仓量暴增 | 变化 ≥ ±20% | ⚠️ 警告 |
| 爆仓风险中等 | 距爆仓 < 20% | ⚠️ 警告 |
| 爆仓风险高 | 距爆仓 < 10% | 🔴 严重 |
| 爆仓风险紧急 | 距爆仓 < 5% | 🚨 紧急 |

---

## 环境变量一览

```env
# Hyperliquid
HL_PRIVATE_KEY=              # 0x 前缀 EVM 私钥 — 切勿提交
HL_WALLET_ADDRESS=           # 对应钱包地址
HL_USE_TESTNET=false         # true = 测试网
HL_DEFAULT_LEVERAGE=3        # 默认杠杆（1–50）
HL_DEFAULT_MARGIN_MODE=cross # cross（全仓）| isolated（逐仓）
HL_FUNDING_ALERT_THRESHOLD=0.0005
HL_LIQ_ALERT_THRESHOLD=0.15

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ALERT_CHAT_ID=      # 可选，默认与主频道相同

# 新闻
BLOCKBEATS_API_KEY=

# 调度器
FETCH_INTERVAL_MIN=5
NEWS_INTERVAL_MIN=15
DAILY_REPORT_HOUR=8          # CST 小时数

# 安全
AUTONOMOUS_MODE=false        # true = 允许自动交易
MAX_POSITION_SIZE_USD=1000   # 单笔最大仓位（USD）
```

---

## PM2 常用命令

```bash
pm2 status                          # 查看所有进程状态
pm2 logs clawie-scheduler           # 实时日志
pm2 restart clawie-scheduler        # 重启调度器
pm2 stop clawie-scheduler           # 停止调度器
pm2 save                            # 保存进程列表（开机自启）
```

---

## 安全设计

| 防护机制 | 行为 |
|---|---|
| `AUTONOMOUS_MODE=false` | Agent 说明交易参数，等待用户确认后才执行 |
| 最大仓位限制 | 单笔上限（默认 $1,000 USD） |
| 交易日志 | 每笔订单记录到 `memory/trade_history.json` |
| 私钥保护 | 懒加载，不写入任何文件或日志 |
| 爆仓预警 | <20% / <10% / <5% 距爆仓时分级告警 |

---

## 协议

MIT
