# HEARTBEAT.md - Scheduled Tasks

## 自动任务配置

Heartbeat 由 `scheduler.py` 驱动（APScheduler），独立于 OpenClaw 运行。

## 任务列表

| 任务 | 频率 | 描述 |
|---|---|---|
| `fetch_market` | 每 5 分钟 | 拉取 Binance 价格 + HL 市场数据 |
| `fetch_hl_account` | 每 5 分钟 | 拉取 HL 账户持仓（需 HL_WALLET_ADDRESS） |
| `check_alerts` | 每 5 分钟 | 扫描预警信号，触发则推送 Telegram |
| `fetch_news` | 每 15 分钟 | 拉取 BlockBeats 快讯 |
| `daily_report` | 每日 08:00 UTC+8 | 生成每日报告并推送 |
| `weekly_report` | 每周一 08:00 UTC+8 | 生成每周复盘报告 |

## 预警触发条件

| 信号 | 条件 | 级别 |
|---|---|---|
| 资金费率异常 | \|rate_8h\| ≥ 0.05% | ⚠️ 警告 |
| 资金费率极端 | \|rate_8h\| ≥ 0.1% | 🔴 严重 |
| 未平仓量暴增 | OI 变化 ≥ ±20% | ⚠️ 警告 |
| 爆仓风险高 | 距爆仓 < 20% | ⚠️ 警告 |
| 爆仓风险极高 | 距爆仓 < 10% | 🔴 严重 |
| 爆仓风险紧急 | 距爆仓 < 5% | 🚨 紧急 |

## 启动方式

```bash
# 前台运行（测试）
python scheduler.py

# 后台运行（生产）
pm2 start scheduler.py --name clawie-scheduler --interpreter python3

# 同时启动数据抓取
pm2 start fetcher.py --name clawie-fetcher --interpreter python3
```
