# 🤖 crypto-clawie 使用说明书

> Hyperliquid 永续合约交易机器人 · 资金费率监控 · 套利策略 · 网格交易 · Telegram 实时推送

**版本：** 2.0.0 · **Python：** ≥ 3.10 · **协议：** MIT

---

## 目录

1. [部署与启动](#1-部署与启动)
2. [配置说明](#2-配置说明)
3. [Telegram 指令手册](#3-telegram-指令手册)
4. [预警系统](#4-预警系统)
5. [资金费率套利策略](#5-资金费率套利策略)
6. [网格交易](#6-网格交易)
7. [回测引擎](#7-回测引擎)
8. [安全机制](#8-安全机制)
9. [定时任务](#9-定时任务)
10. [日常运维](#10-日常运维)
11. [常见问题](#11-常见问题)

---

## 1. 部署与启动

### 方式一：VPS 一键部署（推荐）

```bash
bash <(curl -s https://raw.githubusercontent.com/Zachppt/crypto-clawie/main/setup.sh)
```

脚本自动完成：克隆代码 → 创建虚拟环境 → 安装依赖 → 引导填写 `.env` → 通过 pm2 启动

### 方式二：手动部署

```bash
git clone https://github.com/Zachppt/crypto-clawie.git
cd crypto-clawie
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入真实值
nano .env
```

启动后台进程：

```bash
pm2 start scheduler.py --name clawie-scheduler --interpreter venv/bin/python3
pm2 start bot.py       --name clawie-bot       --interpreter venv/bin/python3
pm2 save               # 开机自启
```

### 验证运行

```bash
pm2 status                       # 两个进程均应为 online
pm2 logs clawie-scheduler        # 查看调度器日志
pm2 logs clawie-bot              # 查看 bot 日志
```

---

## 2. 配置说明

复制 `.env.example` 为 `.env`，逐项填写：

```env
# ── Hyperliquid（必填）──────────────────────────────────────
HL_PRIVATE_KEY=0x...            # EVM 私钥，切勿提交 Git！
HL_WALLET_ADDRESS=0x...         # 对应钱包地址

# ── Telegram（必填）────────────────────────────────────────
TELEGRAM_BOT_TOKEN=             # 向 @BotFather 申请
TELEGRAM_CHAT_ID=               # 向 @userinfobot 查询你的 ID
TELEGRAM_ALERT_CHAT_ID=        # 可选：预警单独发往另一频道

# ── 推荐配置 ───────────────────────────────────────────────
BLOCKBEATS_API_KEY=             # BlockBeats 快讯 API Key
HL_USE_TESTNET=true             # 首次部署建议先测试网验证
AUTONOMOUS_MODE=false           # false = 手动确认，true = 自动执行
MAX_POSITION_SIZE_USD=500       # 单笔最大仓位（USD），建议从小值起步
MAX_DAILY_LOSS_PCT=5            # 每日亏损熔断：超过账户 5% 自动停止开仓

# ── 高级配置 ───────────────────────────────────────────────
HL_DEFAULT_LEVERAGE=3           # 默认杠杆（1–50）
HL_DEFAULT_MARGIN_MODE=cross    # cross（全仓）| isolated（逐仓）
HL_FUNDING_ALERT_THRESHOLD=0.0005  # 资金费率预警阈值（0.05%/8h）
HL_LIQ_ALERT_THRESHOLD=0.15    # 爆仓距离预警阈值（15%）
FETCH_INTERVAL_MIN=5            # 数据抓取间隔（分钟）
NEWS_INTERVAL_MIN=15            # 新闻检查间隔（分钟）
DAILY_REPORT_HOUR=8             # 每日报告时间（CST）
```

> **安全提示**：`.env` 已在 `.gitignore` 中，请勿手动将私钥粘贴到任何聊天或日志中。

---

## 3. Telegram 指令手册

向机器人发送以下指令即可获得响应，无需自然语言。

### 3.1 账户查询

#### `/position` — 持仓明细与账户余额

```
📊 账户余额：$2,340.50 USDC
保证金使用：$680.00 / $2,340.50（29.1%）

持仓明细：
• BTC-PERP  多  0.005 @ $94,200  未实现 PnL: +$12.30
• ETH-PERP  空  0.3   @ $3,480   未实现 PnL: -$4.20
```

#### `/liq` — 爆仓风险评估

```
🛡️ 爆仓风险评估

• BTC-PERP  多  爆仓价：$89,100  距离：5.4%  🔴 高风险
• ETH-PERP  空  爆仓价：$3,720   距离：6.9%  🟡 中等风险
```

### 3.2 市场行情

#### `/market` — 市场概览

一键获取：前 5 资金费率 + 恐慌贪婪指数 + 账户余额摘要。

#### `/funding` — 资金费率排行 Top 20

```
📈 资金费率排行（Top 20）

1. WIF     +0.1823%/8h  年化 ~200%  🔴
2. DOGE    +0.1201%/8h  年化 ~131%  🔴
3. BTC     +0.0412%/8h  年化 ~45%   🟡
...
```

#### `/funding BTC` — 单个资产详情

```
📊 BTC 资金费率

• 当前：+0.0412%/8h（年化 ~45%）
• 标记价：$95,230
• 未平仓量：$2.1B
• 方向：多头付空头（做空有利）
```

#### `/oi` — 未平仓量 Top 10

#### `/oi BTC` — 单个资产 OI

#### `/price ETH` — 实时价格

#### `/fng` — 恐慌贪婪指数

```
😱 恐慌贪婪指数：72（贪婪）
历史分位：近 30 日处于 85 分位
```

#### `/BTC` `/ETH` `/SOL` — 快捷查询

任意 HL 支持的交易对符号，返回：价格 · 24h 涨跌 · 资金费率(8h) · 年化 · 未平仓量。

### 3.3 快讯

#### `/news` — 最新快讯（前 10 条）

BlockBeats 实时快讯，带时间戳。

#### `/hlnews` — HL 相关快讯

自动过滤含 "Hyperliquid / HYPE / HL" 关键词的条目。

### 3.4 信号与报告

#### `/alerts` — 全部异动信号扫描

```
🔔 异动信号扫描 — 发现 3 个信号

🔴 WIF  融资费率极端
  费率 +0.1823%/8h · OI $180M · 24h +8.2%
  ████████░░ 82%  建议做空套利

🟡 SOL  融资费率偏高
  费率 +0.0612%/8h · OI $320M
  ████░░░░░░ 45%  关注中

🔴 BTC  爆仓风险高
  持仓距爆仓 8.3%  紧急控制仓位
  ██████████ 100%
```

置信度 < 40% 的信号不显示。

#### `/report` — 今日报告

包含：市场情绪 · 主流价格 · 资金费率异动 · 账户状态摘要

#### `/weekly` — 本周复盘报告

包含：本周涨跌幅 · 资金费率分布 · 账户净值变化 · 交易记录

### 3.5 套利与策略

见第 5–7 章详细说明。

---

## 4. 预警系统

### 4.1 信号阈值

| 信号类型 | 触发条件 | 基础置信度 |
|---|---|---|
| 资金费率极端 | `\|rate_8h\| ≥ 0.1%` | 80–100% |
| 资金费率偏高 | `\|rate_8h\| ≥ 0.05%` | 40–70% |
| OI 确认加分 | OI > $50M（同方向） | +20% |
| 价格动量加分 | 24h 涨跌 > 3%（同方向） | +10% |
| 低流动性折扣 | UTC 16:00–22:00（北京 00:00–06:00） | −10% |
| 爆仓风险紧急 | 距爆仓 < 5% | 100% |
| 爆仓风险高 | 距爆仓 < 10% | 70% |
| 爆仓风险中等 | 距爆仓 < 20% | 40% |

### 4.2 SQLite 去重机制

预警通过 SQLite 持久化去重，避免重复推送：

| 预警类型 | 重复抑制时长 |
|---|---|
| 资金费率 | 8 小时 |
| 爆仓风险 | 4 小时 |
| 新闻快讯 | 24 小时 |

重启调度器不会重置去重状态，不会产生"开机轰炸"。

### 4.3 手动扫描

随时发送 `/alerts` 触发即时扫描，不受去重限制。

---

## 5. 资金费率套利策略

### 策略逻辑

**Delta 中性套利**：做空 HL 永续合约（收取资金费）+ 买入等量 Binance 现货（对冲价格风险）

- 入场条件：`|funding_8h| ≥ 0.05%`（年化约 54%）
- 离场条件：`|funding_8h| ≤ 0.01%`
- HL perp 腿：若 `AUTONOMOUS_MODE=true` 自动执行；否则机器人给出参数，需手动操作
- Binance 现货腿：**始终手动**，机器人给出精确数量

### 5.1 扫描机会

```
/arb scan
```

```
💰 资金费率套利机会 — 3 个

🔴 WIF  +0.1823%/8h | 年化 ~200%
  策略：做空HL + 买现货 | OI $180M

🟡 DOGE +0.0812%/8h | 年化 ~89%
  策略：做空HL + 买现货 | OI $420M

💡 记录套利仓位：/arb open <币种> <金额USD>
```

### 5.2 开启套利

```
/arb open WIF 500
```

```
✅ 套利仓位已记录 — WIF

• HL 方向：做空 perp 📉
• 金额：$500 USDC
• 入场费率：+0.1823%/8h（年化 ~200%）
• 入场价格：$2.850

⚡ 请同步在 Binance 买入 175.439 WIF 现货
  （约 $500 USDC @ $2.850）

退出信号：费率 ≤ 0.010%/8h 时执行 /arb close WIF
```

> **操作步骤**：收到消息后立即在 Binance 按提示数量买入现货，两腿完成即构成 Delta 中性头寸。

### 5.3 查看状态

```
/arb status
```

```
📋 套利仓位状态 — 2 个

• WIF  short | $500
  入场 +0.1823% → 当前 +0.0934%
  持有 16h | 预估收益 +$11.40
  🟢 持有中

• BTC  short | $1,000
  入场 +0.0521% → 当前 +0.0089%
  持有 48h | 预估收益 +$31.26
  🔴 建议平仓

💰 总预估资金费收益：+$42.66
```

### 5.4 关闭套利

```
/arb close BTC
```

```
✅ 套利仓位已关闭 — BTC

• 持有时间：48 小时
• 预估资金费收益：+$31.26

⚡ 请在 Binance 卖出 0.010526 BTC 现货平掉对冲
  （入场均价 $95,000）
```

> 收到消息后在 Binance 按提示数量卖出现货，两腿均平掉后套利结束。

---

## 6. 网格交易

网格交易在设定价格区间内分层挂限价单，价格震荡时反复低买高卖。

### 6.1 创建网格

```
/grid BTC 90000 100000 10 50
```

参数说明：`/grid <币种> <低价> <高价> <格数> <每格USD>`

| 参数 | 示例 | 说明 |
|---|---|---|
| 币种 | BTC | HL 支持的任意交易对 |
| 低价 | 90000 | 网格下界（USD） |
| 高价 | 100000 | 网格上界（USD） |
| 格数 | 10 | 将区间分为 10 等份（11 个价位） |
| 每格USD | 50 | 每笔限价单金额 |

创建后每个价位挂一张限价单，价格穿越时自动成交并在反向挂回补单。

### 6.2 查看网格状态

```
/grid
```

```
📊 活跃网格 — 1 个

BTC 网格 #abc123
• 区间：$90,000 – $100,000（10 格）
• 每格：$50 | 总投入：$500
• 状态：运行中 | 创建于 2026-04-07 09:00
```

### 6.3 取消网格

```
/grid cancel abc123
```

取消网格后需手动在 Hyperliquid 撤销对应的挂单。

---

## 7. 回测引擎

### 7.1 快速回测（合成数据）

```
/backtest
```

```
📊 回测结果（合成数据，仅供参数验证）

• 总交易次数：47
• 胜率：72.3%
• 净 PnL：+$284.50（资金费 +$312.00 / 手续费 -$27.50）
• 最大回撤：8.2%
• 夏普比率：2.34（优秀 ✅）

基于合成价格模拟，结果不代表真实收益
```

合成数据使用均值回归模型模拟 BTC/ETH/SOL 的资金费率走势，适合快速验证参数设置。

### 7.2 积累真实历史数据

部署后可每 8 小时运行一次数据收集脚本：

```bash
# 手动运行
python3 backtest/data_collector.py

# 加入 pm2（可选）
pm2 start backtest/data_collector.py --name clawie-datacollector \
  --interpreter venv/bin/python3 --cron "0 */8 * * *"
pm2 save
```

数据累积至 `data/hl_market_history.json` 后，可用真实数据回测：

```python
from backtest.engine import BacktestEngine, FundingArbStrategy
engine = BacktestEngine()
engine.load_data("data/hl_market_history.json")
result = engine.run(FundingArbStrategy(entry_threshold=0.0004))
print(result.summary(data_label="真实历史数据"))
```

---

## 8. 安全机制

### 8.1 自动交易开关

| 配置 | 行为 |
|---|---|
| `AUTONOMOUS_MODE=false`（默认） | 机器人显示交易参数，提示前往 Hyperliquid 手动下单 |
| `AUTONOMOUS_MODE=true` | 机器人直接签名并提交订单到 HL |

**建议**：新部署时始终从 `false` 开始，熟悉流程后再开启自动模式。

### 8.2 每日亏损熔断

```env
MAX_DAILY_LOSS_PCT=5    # 账户每日亏损超过 5% 自动停止新开仓
```

- 每次平仓后记录实现盈亏到 `memory/trade_history.json`
- 当日已实现亏损超过阈值时，新开仓请求返回错误提示
- 次日 UTC 00:00 自动重置

### 8.3 单笔仓位上限

```env
MAX_POSITION_SIZE_USD=500    # 单笔最大仓位 500 USD
```

超过上限的开仓请求会被拒绝，不影响手动操作。

### 8.4 私钥安全

- 私钥**懒加载**：仅在触发交易时读取，启动时不接触
- 不写入任何文件或日志
- 所有签名在本地完成，私钥不离开服务器

---

## 9. 定时任务

| 任务 | 间隔 | 说明 |
|---|---|---|
| `fetch`（异步并发） | 每 5 分钟 | HL市场 + HL账户 + Binance + 新闻 同时抓取，约 3s 完成 |
| `funding_alert` | 每 5 分钟（+60s 偏移） | 新数据落地后扫描资金费率异动 |
| `liq_alert` | 每 5 分钟（+60s 偏移） | 新数据落地后检查爆仓风险 |
| `news_check` | 每 15 分钟 | BlockBeats HL 关键词过滤推送 |
| `daily_report` | 每日 08:00 CST | 市场 + 账户每日汇总 |
| `weekly_report` | 周一 08:00 CST | 每周复盘报告 |
| `db_cleanup` | 每 1 小时 | 清理过期预警去重记录 |

> 60s 偏移确保 fetch 完成后 alert 任务才扫描，避免读到旧数据。

---

## 10. 日常运维

### 进程管理

```bash
pm2 status                          # 查看所有进程
pm2 logs clawie-scheduler --lines 50 # 最近 50 行日志
pm2 logs clawie-bot --lines 50
pm2 restart clawie-scheduler        # 重启调度器
pm2 restart clawie-bot              # 重启 bot
pm2 stop all                        # 停止所有进程
pm2 save                            # 保存状态（重启后自启）
```

### 手动触发数据更新

```bash
cd ~/crypto-clawie && source venv/bin/activate
python3 fetcher.py                  # 立即抓取一次最新数据
```

### 更新代码

```bash
cd ~/crypto-clawie
git pull origin main
source venv/bin/activate
pip install -r requirements.txt     # 如有新依赖
pm2 restart all
```

### 查看数据文件

```bash
cat data/hl_market.json | python3 -m json.tool    # HL 市场数据
cat data/hl_account.json | python3 -m json.tool   # 账户持仓
cat memory/arb_positions.json                      # 套利仓位记录
cat memory/trade_history.json                      # 交易历史
```

---

## 11. 常见问题

**Q: 调度器启动后 Telegram 没有收到消息怎么办？**

先确认 `pm2 logs clawie-scheduler` 无报错，再检查 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 是否正确。向机器人发送任意指令测试连通性。

**Q: 数据文件是空的 / 市场数据未就绪？**

手动运行 `python3 fetcher.py` 查看抓取是否成功。若报网络错误，检查 VPS 是否能访问 `api.hyperliquid.xyz`。

**Q: `/arb open` 提示费率未达阈值？**

当前资金费率低于 0.05%/8h，不满足入场条件。先用 `/arb scan` 查看哪些资产有机会。

**Q: 如何修改资金费率入场阈值？**

修改 `skills/funding_arb/__init__.py` 中的 `ENTRY_THRESHOLD`（默认 `0.0005`），重启 bot 生效。

**Q: `AUTONOMOUS_MODE=true` 安全吗？**

开启后机器人会直接签名提交订单。建议先在测试网（`HL_USE_TESTNET=true`）验证，并设置合理的 `MAX_POSITION_SIZE_USD` 和 `MAX_DAILY_LOSS_PCT` 作为双重保险。

**Q: 回测结果夏普比率很高，是否可信？**

`/backtest` 使用合成数据，仅用于验证策略参数的量级是否合理，不代表真实收益。建议积累 30 天以上的真实历史数据后再做严肃回测。

**Q: 套利仓位的预估收益准确吗？**

预估收益基于入场时的资金费率 × 持仓时间，未考虑费率波动。实际收益以 HL 结算为准，每 8 小时一次。

**Q: 如何完全停止机器人？**

```bash
pm2 stop all && pm2 save
```

已挂的 HL 限价单不会自动撤销，需手动在 Hyperliquid 界面处理。

---

## 协议

MIT
