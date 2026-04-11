"""
scheduler.py — 后台调度器
使用 APScheduler 定期抓取数据、扫描预警、发送 Telegram 通知。
与 OpenClaw 独立运行，专门处理主动推送。

用法：
  python scheduler.py                    # 持续运行
  pm2 start scheduler.py --name clawie-scheduler --interpreter python3
"""
from __future__ import annotations

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

TOPIC_ALERT    = os.getenv("TELEGRAM_TOPIC_ALERT")
TOPIC_MARKET   = os.getenv("TELEGRAM_TOPIC_MARKET")
TOPIC_POSITION = os.getenv("TELEGRAM_TOPIC_POSITION")

FETCH_INTERVAL    = int(os.getenv("FETCH_INTERVAL_MIN", "5"))
NEWS_INTERVAL     = int(os.getenv("NEWS_INTERVAL_MIN", "15"))
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "8"))  # CST 小时数

FUNDING_THRESHOLD = float(os.getenv("HL_FUNDING_ALERT_THRESHOLD", "0.0005"))
LIQ_THRESHOLD     = float(os.getenv("HL_LIQ_ALERT_THRESHOLD", "0.15"))

# ── Telegram 发送 ─────────────────────────────────────────────────────────────

def send_telegram(msg: str, chat_id: str = None, parse_mode: str = "Markdown",
                  thread_id: str = None):
    if not BOT_TOKEN or not (chat_id or CHAT_ID):
        log.warning("Telegram not configured, skip send")
        return
    cid     = chat_id or CHAT_ID
    payload = {"chat_id": cid, "text": msg, "parse_mode": parse_mode}
    if thread_id:
        payload["message_thread_id"] = int(thread_id)
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def send_alert(msg: str):
    send_telegram(msg, chat_id=ALERT_CHAT_ID, thread_id=TOPIC_ALERT)


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


def job_fetch_prices():
    """轻量价格刷新：仅 Binance 主流币价格，60s 一次，保留 FNG 不动。"""
    try:
        import fetcher
        fetcher.fetch_prices_fast()
    except Exception as e:
        log.error(f"job_fetch_prices failed: {e}")


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
        send_telegram(msg, thread_id=TOPIC_MARKET)
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


# ── 任务：自动开平仓 ──────────────────────────────────────────────────────────

def job_auto_trade():
    """
    基于资金费率信号自动开平仓（资金费率套利方向）。

    策略：
      - 资金费率为正（多头付空头）→ 做空，收取资金费
      - 资金费率为负（空头付多头）→ 做多，收取资金费
      - 退出条件：费率恢复正常 / 达到止盈 / 触发止损

    需要配置：
      AUTO_TRADE_ENABLED=true
      AUTONOMOUS_MODE=true
    """
    if os.getenv("AUTO_TRADE_ENABLED", "false").lower() != "true":
        return
    if os.getenv("AUTONOMOUS_MODE", "false").lower() != "true":
        log.warning("AUTO_TRADE_ENABLED=true 但 AUTONOMOUS_MODE=false，自动交易跳过")
        send_alert(
            "⚠️ *自动交易未能执行*\n"
            "`AUTO_TRADE_ENABLED=true` 但 `AUTONOMOUS_MODE=false`\n"
            "请在 .env 中同时开启两个开关，或发送 /autotrade 查看说明"
        )
        return

    min_confidence = float(os.getenv("AUTO_TRADE_MIN_CONFIDENCE", "0.7"))
    size_usd       = float(os.getenv("AUTO_TRADE_SIZE_USD", "50"))
    max_positions  = int(os.getenv("AUTO_TRADE_MAX_POSITIONS", "2"))
    exit_funding   = float(os.getenv("AUTO_TRADE_EXIT_FUNDING", "0.0001"))
    profit_pct     = float(os.getenv("AUTO_TRADE_PROFIT_PCT", "3"))
    stop_pct       = float(os.getenv("AUTO_TRADE_STOP_PCT", "2"))

    try:
        from skills.crypto_alert import CryptoAlertSkill
        from skills.hl_trade import HLTradeSkill

        trade_env = {k: os.getenv(k, "") for k in [
            "HL_PRIVATE_KEY", "HL_WALLET_ADDRESS", "HL_USE_TESTNET",
            "HL_DEFAULT_LEVERAGE", "HL_DEFAULT_MARGIN_MODE",
            "MAX_POSITION_SIZE_USD", "MAX_DAILY_LOSS_PCT",
        ]}
        trade_env["AUTONOMOUS_MODE"] = "true"  # 自动交易强制开启

        alert_skill = CryptoAlertSkill(DATA_DIR, BASE_DIR / "memory", {})
        trade_skill = HLTradeSkill(DATA_DIR, BASE_DIR / "memory", trade_env)

        # 加载自动交易状态记录
        auto_trades_path = BASE_DIR / "memory" / "auto_trades.json"
        auto_trades: list = []
        if auto_trades_path.exists():
            try:
                with open(auto_trades_path) as f:
                    auto_trades = json.load(f)
            except Exception:
                auto_trades = []

        # 当前市场数据
        market = _load("hl_market.json")
        market_map: dict = {}
        if market:
            for a in market.get("assets", []):
                market_map[a["symbol"]] = a

        # 当前账户持仓
        account = _load("hl_account.json")
        open_symbols: set = set()
        if account:
            for pos in account.get("positions", []):
                open_symbols.add(pos["symbol"])

        # ── Step 1：检查自动仓位的退出条件 ───────────────────────────────────
        remaining_trades = []
        for at in auto_trades:
            sym = at["symbol"]

            # 如果持仓已被外部平掉，清理记录
            if sym not in open_symbols:
                log.info(f"Auto-trade {sym} 已无持仓，清理记录")
                continue

            asset       = market_map.get(sym, {})
            funding     = asset.get("funding_8h", 0)
            price       = asset.get("mark_price", 0)
            entry_price = at.get("entry_price", price) or price
            side        = at["side"]

            should_exit = False
            exit_reason = ""

            # 退出条件1：资金费率恢复正常
            if abs(funding) < exit_funding:
                should_exit = True
                exit_reason = f"资金费率恢复正常（{funding*100:.4f}%/8h）"

            # 退出条件2：止盈/止损（按标记价格估算）
            if price and entry_price:
                pnl_pct = ((price - entry_price) / entry_price) * (1 if side == "long" else -1) * 100
                if pnl_pct >= profit_pct:
                    should_exit = True
                    exit_reason = f"止盈触发（约 +{pnl_pct:.1f}%）"
                elif pnl_pct <= -stop_pct:
                    should_exit = True
                    exit_reason = f"止损触发（约 {pnl_pct:.1f}%）"

            if should_exit:
                result = trade_skill.run(action="close", symbol=sym)
                if result.get("success"):
                    open_symbols.discard(sym)
                    send_alert(
                        f"🤖 *自动平仓*\n"
                        f"• 标的：`{sym}`\n"
                        f"• 原因：{exit_reason}\n"
                        f"• {result['text'].split(chr(10))[0]}"
                    )
                    log.info(f"Auto-close {sym}: {exit_reason}")
                else:
                    log.error(f"Auto-close {sym} failed: {result['text']}")
                    remaining_trades.append(at)  # 平仓失败，保留记录
            else:
                remaining_trades.append(at)

        auto_trades = remaining_trades

        # ── Step 2：扫描新开仓机会 ────────────────────────────────────────────
        auto_symbols = {at["symbol"] for at in auto_trades}

        if len(auto_trades) < max_positions:
            sig_result = alert_skill.run(action="funding")
            signals = sig_result.get("data", {}).get("signals", [])
            signals = [s for s in signals if s["confidence"] >= min_confidence]
            signals.sort(key=lambda x: x["confidence"], reverse=True)

            for sig in signals:
                if len(auto_trades) >= max_positions:
                    break
                sym = sig["symbol"]
                if sym in open_symbols or sym in auto_symbols:
                    continue  # 已有持仓，跳过

                funding = market_map.get(sym, {}).get("funding_8h", 0)
                if funding == 0:
                    continue

                # 资金费率为正 → 做空（收取多头支付的费用）
                # 资金费率为负 → 做多（收取空头支付的费用）
                side = "short" if funding > 0 else "long"

                result = trade_skill.run(
                    action="open",
                    symbol=sym,
                    side=side,
                    size_usd=size_usd,
                )
                if result.get("success"):
                    entry_price = market_map.get(sym, {}).get("mark_price", 0)
                    ann = abs(funding) * 3 * 365 * 100
                    auto_trades.append({
                        "symbol":         sym,
                        "side":           side,
                        "size_usd":       size_usd,
                        "entry_price":    entry_price,
                        "entry_time":     datetime.now(timezone.utc).isoformat(),
                        "entry_funding":  funding,
                        "confidence":     sig["confidence"],
                    })
                    open_symbols.add(sym)
                    auto_symbols.add(sym)
                    send_alert(
                        f"🤖 *自动开仓*\n"
                        f"• 标的：`{sym}` {'做空 📉' if side == 'short' else '做多 📈'}\n"
                        f"• 金额：`${size_usd}` USDC\n"
                        f"• 资金费率：`{funding*100:+.4f}%/8h`（年化 ~{ann:.0f}%）\n"
                        f"• 置信度：`{sig['confidence']*100:.0f}%`\n"
                        f"• 策略：收取资金费，待费率恢复平仓\n"
                        f"• 止盈 +{profit_pct}% / 止损 -{stop_pct}%"
                    )
                    log.info(f"Auto-open {sym} {side} ${size_usd} (funding={funding:.6f})")
                else:
                    log.warning(f"Auto-open {sym} failed: {result['text']}")

        # 持久化自动交易记录
        auto_trades_path.parent.mkdir(exist_ok=True)
        with open(auto_trades_path, "w") as f:
            json.dump(auto_trades, f, ensure_ascii=False, indent=2)

    except Exception as e:
        log.error(f"job_auto_trade failed: {e}", exc_info=True)


# ── 任务：Agent 智能开平仓 ────────────────────────────────────────────────────

def job_agent_trade():
    """
    多因子 Agent 交易决策（替代简单阈值触发）。
    评分维度：资金费率幅度 + OI 规模 + 价格动量 + 跨所费率确认
    需要：AGENT_TRADE_ENABLED=true  AUTONOMOUS_MODE=true
    """
    if os.getenv("AGENT_TRADE_ENABLED", "false").lower() != "true":
        return
    if os.getenv("AUTONOMOUS_MODE", "false").lower() != "true":
        log.warning("AGENT_TRADE_ENABLED=true 但 AUTONOMOUS_MODE=false，Agent 跳过")
        return

    try:
        from skills.agent_trade import AgentTradeSkill
        from skills.hl_trade import HLTradeSkill

        trade_env = {k: os.getenv(k, "") for k in [
            "HL_PRIVATE_KEY", "HL_WALLET_ADDRESS", "HL_USE_TESTNET",
            "HL_DEFAULT_LEVERAGE", "HL_DEFAULT_MARGIN_MODE",
            "MAX_POSITION_SIZE_USD", "MAX_DAILY_LOSS_PCT",
            "AUTO_TRADE_SIZE_USD", "AUTO_TRADE_MAX_POSITIONS",
            "HL_FUNDING_ALERT_THRESHOLD",
        ]}
        trade_env["AUTONOMOUS_MODE"] = "true"

        agent       = AgentTradeSkill(DATA_DIR, BASE_DIR / "memory", trade_env)
        trade_skill = HLTradeSkill(DATA_DIR, BASE_DIR / "memory", trade_env)

        # 获取 Agent 决策
        result    = agent.run(action="decide")
        decisions = result.get("data", {}).get("decisions", [])

        if not decisions:
            log.info("job_agent_trade: 无新开仓决策")
            return

        exit_funding = float(os.getenv("AUTO_TRADE_EXIT_FUNDING", "0.0001"))
        profit_pct   = float(os.getenv("AUTO_TRADE_PROFIT_PCT", "3"))
        stop_pct     = float(os.getenv("AUTO_TRADE_STOP_PCT", "2"))

        # ── 检查已有仓位的退出条件 ────────────────────────────────────────────
        auto_trades_path = BASE_DIR / "memory" / "auto_trades.json"
        auto_trades: list = []
        if auto_trades_path.exists():
            try:
                with open(auto_trades_path) as f:
                    auto_trades = json.load(f)
            except Exception:
                pass

        market   = _load("hl_market.json")
        mkt_map  = {a["symbol"]: a for a in (market or {}).get("assets", [])}
        account  = _load("hl_account.json")
        open_sym = {p["symbol"] for p in (account or {}).get("positions", [])}

        remaining = []
        for at in auto_trades:
            sym = at["symbol"]
            if sym not in open_sym:
                continue  # 已平仓，清理
            asset      = mkt_map.get(sym, {})
            funding    = asset.get("funding_8h", 0)
            price      = asset.get("mark_price", 0)
            entry      = at.get("entry_price", price) or price
            side       = at["side"]
            pnl_pct    = ((price - entry) / entry * (1 if side == "long" else -1) * 100) if entry else 0

            should_exit = False
            reason      = ""
            if abs(funding) < exit_funding:
                should_exit = True
                reason = f"费率恢复（{funding*100:.4f}%）"
            elif pnl_pct >= profit_pct:
                should_exit = True
                reason = f"止盈（约+{pnl_pct:.1f}%）"
            elif pnl_pct <= -stop_pct:
                should_exit = True
                reason = f"止损（约{pnl_pct:.1f}%）"

            if should_exit:
                r = trade_skill.run(action="close", symbol=sym)
                if r.get("success"):
                    send_alert(
                        f"🤖 *Agent 平仓*\n"
                        f"• 标的：`{sym}` | 原因：{reason}\n"
                        f"• {r['text'].split(chr(10))[0]}"
                    )
                    log.info(f"agent_close {sym}: {reason}")
                else:
                    remaining.append(at)
            else:
                remaining.append(at)

        auto_trades   = remaining
        auto_sym_set  = {at["symbol"] for at in auto_trades}
        max_positions = int(os.getenv("AUTO_TRADE_MAX_POSITIONS", "2"))

        # ── 执行新开仓 ────────────────────────────────────────────────────────
        for dec in decisions:
            if len(auto_trades) >= max_positions:
                break
            sym = dec["symbol"]
            if sym in open_sym or sym in auto_sym_set:
                continue

            r = trade_skill.run(
                action="open",
                symbol=sym,
                side=dec["side"],
                size_usd=dec["size_usd"],
            )
            if r.get("success"):
                entry_price = mkt_map.get(sym, {}).get("mark_price", 0)
                auto_trades.append({
                    "symbol":        sym,
                    "side":          dec["side"],
                    "size_usd":      dec["size_usd"],
                    "entry_price":   entry_price,
                    "entry_time":    datetime.now(timezone.utc).isoformat(),
                    "entry_funding": dec.get("funding_rate", 0),
                    "confidence":    dec["confidence"],
                    "reasons":       dec.get("reasons", []),
                })
                auto_sym_set.add(sym)
                open_sym.add(sym)
                send_alert(
                    f"🤖 *Agent 开仓*\n"
                    f"• `{sym}` {'做空 📉' if dec['side'] == 'short' else '做多 📈'} "
                    f"`${dec['size_usd']:.0f}`\n"
                    f"• 置信度：`{dec['confidence']*100:.0f}%`\n"
                    f"• 依据：{' | '.join(dec.get('reasons', []))[:80]}\n"
                    f"• 止盈 +{profit_pct}% / 止损 -{stop_pct}%"
                )
                log.info(f"agent_open {sym} {dec['side']} ${dec['size_usd']} conf={dec['confidence']:.2f}")
            else:
                log.warning(f"agent_open {sym} failed: {r['text']}")

        # 持久化
        auto_trades_path.parent.mkdir(exist_ok=True)
        with open(auto_trades_path, "w") as f:
            json.dump(auto_trades, f, ensure_ascii=False, indent=2)

    except Exception as e:
        log.error(f"job_agent_trade failed: {e}", exc_info=True)


# ── 任务：链上地址监控 ─────────────────────────────────────────────────────────

def job_check_onchain():
    """扫描所有监控地址，发现新交易时推送 Telegram 告警。"""
    try:
        from skills.onchain import OnchainSkill
        skill  = OnchainSkill(DATA_DIR, BASE_DIR / "memory", {})
        result = skill.run(action="scan")
        alerts = result.get("data", {}).get("alerts", [])

        for tx in alerts:
            chain   = tx.get("chain", "?")
            label   = tx.get("label", "?")
            val     = tx.get("value_display", "?")
            direct  = tx.get("direction", "")
            ts      = tx.get("time_display", "")
            status  = tx.get("status", "✅")
            tx_url  = tx.get("tx_url", "")
            addr    = tx.get("address", "")[:12]

            alert_key = f"onchain:{tx.get('hash', '')[:20]}"
            if not db.is_alerted(alert_key):
                msg = (
                    f"⛓️ *链上异动* — {chain} `{label}`\n"
                    f"{status} {ts} {direct} `{val}`\n"
                    f"地址：`{addr}...`\n"
                )
                if tx_url:
                    msg += f"🔗 {tx_url}"
                send_alert(msg)
                db.mark_alerted(alert_key, ttl_hours=24)
                log.info(f"onchain alert: {chain} {label} {val}")

    except Exception as e:
        log.error(f"job_check_onchain failed: {e}", exc_info=True)


# ── 任务：每日报告 ────────────────────────────────────────────────────────────

def job_focus_check():
    """
    检查是否有专项追踪任务（memory/focus.json），
    若到时间则生成并推送报告。每 5 分钟检查一次。
    """
    focus_path = BASE_DIR / "memory" / "focus.json"
    if not focus_path.exists():
        return

    try:
        with open(focus_path) as f:
            focus = json.load(f)
    except Exception:
        return

    token        = focus.get("token", "BTC")
    interval_min = int(focus.get("interval_min", 15))
    chat_id      = focus.get("chat_id") or CHAT_ID
    topic_id     = focus.get("topic_id")

    # 检查距上次报告是否已过 interval_min 分钟
    last_path = BASE_DIR / "memory" / "focus_last.json"
    if last_path.exists():
        try:
            with open(last_path) as f:
                last_data = json.load(f)
            last_ts  = datetime.fromisoformat(last_data["time"])
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            elapsed  = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
            if elapsed < interval_min:
                return
        except Exception:
            pass  # 解析失败就当作从未生成过

    # 生成报告
    try:
        from skills.focus import FocusSkill
        skill  = FocusSkill(DATA_DIR, BASE_DIR / "memory", {})
        result = skill.run(action="report", token=token)
        if result.get("success"):
            send_telegram(result["text"], chat_id=chat_id, thread_id=topic_id)
        else:
            log.warning(f"focus_check: report failed: {result.get('text')}")

        # 更新上次报告时间（无论成功失败都更新，避免频繁重试）
        with open(last_path, "w") as f:
            json.dump({"time": datetime.now(timezone.utc).isoformat(), "token": token}, f)

    except Exception as e:
        log.error(f"job_focus_check failed: {e}", exc_info=True)


def job_daily_report():
    try:
        from skills.crypto_report import CryptoReportSkill
        skill  = CryptoReportSkill(DATA_DIR, BASE_DIR / "memory", {})
        report = skill.run(period="daily")
        send_telegram(report.get("text", "⚠️ 报告生成失败"), thread_id=TOPIC_MARKET)
        log.info("Daily report sent")
    except Exception as e:
        log.error(f"daily_report failed: {e}")
        send_telegram(f"⚠️ 每日报告生成失败：{e}", thread_id=TOPIC_MARKET)


# ── 任务：每周报告 ────────────────────────────────────────────────────────────

def job_weekly_report():
    try:
        from skills.crypto_report import CryptoReportSkill
        skill  = CryptoReportSkill(DATA_DIR, BASE_DIR / "memory", {})
        report = skill.run(period="weekly")
        send_telegram(report.get("text", "⚠️ 周报生成失败"), thread_id=TOPIC_MARKET)
        log.info("Weekly report sent")
    except Exception as e:
        log.error(f"weekly_report failed: {e}")


# ── 主调度器 ──────────────────────────────────────────────────────────────────

def main():
    db.init_db()
    log.info("Clawie scheduler starting...")

    scheduler = BlockingScheduler(timezone="UTC")

    # 数据抓取：立即执行第一次（全量：价格 + FNG + 资金费率 + 账户）
    scheduler.add_job(job_fetch, IntervalTrigger(minutes=FETCH_INTERVAL), id="fetch",
                      next_run_time=datetime.now(timezone.utc))

    # 快速价格刷新：60s 一次，仅更新 Binance 价格（FNG/资金费率/账户不变）
    scheduler.add_job(job_fetch_prices, IntervalTrigger(seconds=60), id="price_refresh",
                      next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10))

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

    # 专项追踪报告：每 5 分钟检查，到时间则推送
    scheduler.add_job(job_focus_check, IntervalTrigger(minutes=5), id="focus_check",
                      next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30))

    # 每小时清理过期预警记录
    scheduler.add_job(db.clear_expired, IntervalTrigger(hours=1), id="db_cleanup")

    # 自动交易（简单阈值）：在 fetch + 预警 后 90s 启动
    auto_trade_enabled  = os.getenv("AUTO_TRADE_ENABLED", "false").lower() == "true"
    agent_trade_enabled = os.getenv("AGENT_TRADE_ENABLED", "false").lower() == "true"
    signal_start        = datetime.now(timezone.utc) + timedelta(seconds=90)

    if agent_trade_enabled:
        scheduler.add_job(job_agent_trade, IntervalTrigger(minutes=FETCH_INTERVAL),
                          id="agent_trade", next_run_time=signal_start)
        log.info(f"  Agent-trade:     ENABLED (multi-factor, every {FETCH_INTERVAL} min)")
    elif auto_trade_enabled:
        scheduler.add_job(job_auto_trade, IntervalTrigger(minutes=FETCH_INTERVAL),
                          id="auto_trade", next_run_time=signal_start)
        log.info(f"  Auto-trade:      ENABLED (threshold, every {FETCH_INTERVAL} min)")
    else:
        log.info("  Auto/Agent-trade: disabled")

    # 链上监控
    onchain_interval = int(os.getenv("ONCHAIN_INTERVAL_MIN", str(FETCH_INTERVAL)))
    watchlist_path   = BASE_DIR / "memory" / "watchlist.json"
    if watchlist_path.exists():
        onchain_start = datetime.now(timezone.utc) + timedelta(seconds=120)
        scheduler.add_job(job_check_onchain, IntervalTrigger(minutes=onchain_interval),
                          id="onchain_check", next_run_time=onchain_start)
        log.info(f"  Onchain monitor: ENABLED (every {onchain_interval} min)")
    else:
        log.info("  Onchain monitor: no watchlist yet (use /watch add to start)")

    log.info(f"  Data fetch:      every {FETCH_INTERVAL} min (full)")
    log.info(f"  Price refresh:   every 60s (Binance prices only)")
    log.info(f"  Focus check:     every 5 min")
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
