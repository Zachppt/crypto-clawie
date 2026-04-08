"""
scheduler.py — 后台调度器
使用 APScheduler 定期抓取数据、扫描预警、发送 Telegram 通知。
与 OpenClaw 独立运行，专门处理主动推送。

用法：
  python scheduler.py                    # 持续运行
  pm2 start scheduler.py --name clawie-scheduler --interpreter python3
"""

import os
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import db  # SQLite 持久化预警状态

load_dotenv()

# ── 目录 ─────────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / "data"
LOGS_DIR  = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ── 日志 ─────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "scheduler.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────────────────────

BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID        = os.getenv("TELEGRAM_CHAT_ID", "")
ALERT_CHAT_ID  = os.getenv("TELEGRAM_ALERT_CHAT_ID", "") or CHAT_ID

FETCH_INTERVAL    = int(os.getenv("FETCH_INTERVAL_MIN", "5"))
NEWS_INTERVAL     = int(os.getenv("NEWS_INTERVAL_MIN", "15"))
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "8"))  # CST 小时数

FUNDING_THRESHOLD = float(os.getenv("HL_FUNDING_ALERT_THRESHOLD", "0.0005"))
LIQ_THRESHOLD     = float(os.getenv("HL_LIQ_ALERT_THRESHOLD", "0.15"))

# ── Telegram 发送 ─────────────────────────────────────────────────────────────

def send_telegram(msg: str, chat_id: str = None, parse_mode: str = "Markdown"):
    if not BOT_TOKEN or not (chat_id or CHAT_ID):
        log.warning("Telegram not configured, skip send")
        return
    cid = chat_id or CHAT_ID
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def send_alert(msg: str):
    send_telegram(msg, chat_id=ALERT_CHAT_ID)


# ── 数据读取 ──────────────────────────────────────────────────────────────────

def _load(filename: str) -> dict | list | None:
    path = DATA_DIR / filename
    if not path.exists():
        return None
    try:
        with open(path) as f:
            raw = json.load(f)
        return raw.get("data") if isinstance(raw, dict) and "data" in raw else raw
    except Exception:
        return None


# ── 任务：数据抓取 ────────────────────────────────────────────────────────────

def job_fetch():
    """调用 fetcher 并发抓取全量数据。"""
    try:
        import fetcher
        fetcher.fetch_all()
    except Exception as e:
        log.error(f"job_fetch failed: {e}")


# ── 任务：资金费率预警 ─────────────────────────────────────────────────────────

def job_check_funding():
    market = _load("hl_market.json")
    if not market:
        return

    assets = market.get("assets", [])
    for asset in assets:
        sym      = asset["symbol"]
        rate     = asset["funding_8h"]
        abs_rate = abs(rate)

        if abs_rate < FUNDING_THRESHOLD:
            continue  # 费率正常，db 记录会自然过期（TTL=8h）

        alert_key = f"funding:{sym}"
        if db.is_alerted(alert_key):
            continue  # 已推送且未过期，不重复

        direction = "多头付空头 📈" if rate > 0 else "空头付多头 📉"
        level     = "🔴 极端" if abs_rate >= 0.001 else "⚠️ 异常"
        ann       = round(rate * 3 * 365 * 100, 1)

        msg = (
            f"{level} *资金费率预警* — `{sym}`\n"
            f"8h 费率：`{rate*100:.4f}%` ({direction})\n"
            f"年化：`{ann}%`\n"
            f"💡 做多前请注意：高资金费率会持续蚕食收益"
        )
        send_alert(msg)
        db.mark_alerted(alert_key, ttl_hours=8)
        log.info(f"Funding alert sent: {sym} {rate:.6f}")


# ── 任务：爆仓风险预警 ─────────────────────────────────────────────────────────

def job_check_liquidation():
    account = _load("hl_account.json")
    if not account:
        return

    for alert in account.get("liq_alerts", []):
        sym      = alert["symbol"]
        level    = alert["level"]
        dist_pct = alert["dist_pct"]

        alert_key = f"liq:{sym}:{level}"
        if db.is_alerted(alert_key):
            continue

        emoji = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "⚠️"}.get(level, "⚠️")
        msg = (
            f"{emoji} *爆仓风险 {level}* — `{sym}`\n"
            f"距爆仓价格仅剩：`{dist_pct:.1f}%`\n"
            f"⚡ 建议立即减仓或追加保证金"
        )
        send_alert(msg)
        db.mark_alerted(alert_key, ttl_hours=4)
        log.info(f"Liquidation alert: {sym} {level} {dist_pct:.1f}%")


# ── 任务：新闻推送 ────────────────────────────────────────────────────────────

def job_check_news():
    news = _load("news_cache.json")
    if not news:
        return

    hl_keywords = ["hyperliquid", "HL", "永续", "合约", "资金费率", "清算", "爆仓"]
    for item in news[:10]:
        title   = item.get("title", "")
        content = item.get("content", "")
        ts      = item.get("time", "")

        alert_key = f"news:{hash(title + ts)}"
        if db.is_alerted(alert_key):
            continue

        text    = (title + content).lower()
        matched = any(kw.lower() in text for kw in hl_keywords)
        db.mark_alerted(alert_key, ttl_hours=24)  # 无论是否推送，都记录防重复

        if not matched:
            continue

        msg = f"📰 *快讯*\n{title}\n\n{content[:200]}{'...' if len(content) > 200 else ''}"
        send_telegram(msg)
        log.info(f"News sent: {title[:50]}")


# ── 任务：回测历史数据收集 ────────────────────────────────────────────────────

def job_collect_backtest_data():
    """每 8 小时追加一次 HL 市场快照到历史数据文件，用于真实数据回测。"""
    try:
        from backtest.data_collector import collect_snapshot
        count = collect_snapshot()
        if count:
            log.info(f"Backtest snapshot collected: {count} records")
    except Exception as e:
        log.error(f"job_collect_backtest_data failed: {e}")


# ── 任务：每日报告 ────────────────────────────────────────────────────────────

def job_daily_report():
    try:
        from skills.crypto_report import CryptoReportSkill
        skill  = CryptoReportSkill(DATA_DIR, BASE_DIR / "memory", {})
        report = skill.run(period="daily")
        send_telegram(report.get("text", "⚠️ 报告生成失败"))
        log.info("Daily report sent")
    except Exception as e:
        log.error(f"daily_report failed: {e}")
        send_telegram(f"⚠️ 每日报告生成失败：{e}")


# ── 任务：每周报告 ────────────────────────────────────────────────────────────

def job_weekly_report():
    try:
        from skills.crypto_report import CryptoReportSkill
        skill  = CryptoReportSkill(DATA_DIR, BASE_DIR / "memory", {})
        report = skill.run(period="weekly")
        send_telegram(report.get("text", "⚠️ 周报生成失败"))
        log.info("Weekly report sent")
    except Exception as e:
        log.error(f"weekly_report failed: {e}")


# ── 主调度器 ──────────────────────────────────────────────────────────────────

def main():
    db.init_db()
    log.info("Clawie scheduler starting...")

    scheduler = BlockingScheduler(timezone="UTC")

    # 数据抓取：立即执行第一次
    scheduler.add_job(job_fetch, IntervalTrigger(minutes=FETCH_INTERVAL), id="fetch",
                      next_run_time=datetime.now(timezone.utc))

    # 预警扫描：在 fetch 后 60s 启动，确保使用最新数据
    alert_start = datetime.now(timezone.utc) + timedelta(seconds=60)
    scheduler.add_job(job_check_funding,     IntervalTrigger(minutes=FETCH_INTERVAL),
                      id="funding_alert",    next_run_time=alert_start)
    scheduler.add_job(job_check_liquidation, IntervalTrigger(minutes=FETCH_INTERVAL),
                      id="liq_alert",        next_run_time=alert_start)
    scheduler.add_job(job_check_news,        IntervalTrigger(minutes=NEWS_INTERVAL),
                      id="news_check")

    # 每日报告：使用 Asia/Shanghai 时区，无需手动做 UTC 偏移计算
    scheduler.add_job(job_daily_report,
                      CronTrigger(hour=DAILY_REPORT_HOUR, minute=0, timezone="Asia/Shanghai"),
                      id="daily_report")

    # 每周报告：周一 CST 早上
    scheduler.add_job(job_weekly_report,
                      CronTrigger(day_of_week="mon", hour=DAILY_REPORT_HOUR, minute=5,
                                  timezone="Asia/Shanghai"),
                      id="weekly_report")

    # 回测历史数据收集：每 8 小时一次
    scheduler.add_job(job_collect_backtest_data, IntervalTrigger(hours=8),
                      id="backtest_collect")

    # 每小时清理过期预警记录
    scheduler.add_job(db.clear_expired, IntervalTrigger(hours=1), id="db_cleanup")

    log.info(f"  Data fetch:      every {FETCH_INTERVAL} min")
    log.info(f"  News check:      every {NEWS_INTERVAL} min")
    log.info(f"  Backtest data:   every 8h")
    log.info(f"  Daily report:    {DAILY_REPORT_HOUR}:00 CST")
    log.info("Clawie scheduler running. Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
