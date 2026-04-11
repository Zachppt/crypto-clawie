"""
bot.py — Telegram 指令机器人
直接响应用户命令，实时调用 skill 返回数据，不经过 LLM。

命令完整列表：
  账户
    /position          — 持仓明细和账户余额
    /liq               — 爆仓风险评估

  HL 市场
    /market            — 市场概览（资金费率 + 情绪 + 账户摘要）
    /funding           — 资金费率排行 Top 20
    /funding BTC       — 指定币种资金费率详情
    /oi                — 未平仓量排行 Top 10
    /oi BTC            — 指定币种未平仓量

  行情
    /price BTC         — 实时价格（默认 BTC）
    /fng               — 恐慌贪婪指数

  快讯
    /news              — 最新快讯（前 10 条）
    /hlnews            — HL 相关快讯

  信号
    /alerts            — 全部异动信号扫描

  报告
    /report            — 今日报告
    /weekly            — 本周复盘报告

  快捷查询
    /BTC /ETH /SOL 等  — 价格 + 资金费率（任意 HL 交易对）
"""

import os
import sys
import json
import time
import logging
import importlib
from pathlib import Path

import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── 目录 ──────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
MEMORY_DIR = BASE_DIR / "memory"
LOGS_DIR   = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# ── 日志 ──────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API_URL   = f"https://api.telegram.org/bot{BOT_TOKEN}"

TOPIC_ALERT    = os.getenv("TELEGRAM_TOPIC_ALERT")
TOPIC_MARKET   = os.getenv("TELEGRAM_TOPIC_MARKET")
TOPIC_POSITION = os.getenv("TELEGRAM_TOPIC_POSITION")
TOPIC_TRADE    = os.getenv("TELEGRAM_TOPIC_TRADE")

env = {k: os.getenv(k, "") for k in [
    "HL_PRIVATE_KEY", "HL_WALLET_ADDRESS", "HL_USE_TESTNET",
    "HL_DEFAULT_LEVERAGE", "HL_DEFAULT_MARGIN_MODE",
    "HL_FUNDING_ALERT_THRESHOLD", "HL_LIQ_ALERT_THRESHOLD",
    "BLOCKBEATS_API_KEY", "AUTONOMOUS_MODE", "MAX_POSITION_SIZE_USD",
]}

# ── Telegram 工具 ─────────────────────────────────────────────────────────────

def send(chat_id: int, text: str, parse_mode: str = "Markdown", thread_id: int = None):
    if len(text) > 4000:
        text = text[:4000] + "\n...(内容过长，已截断)"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if thread_id:
        payload["message_thread_id"] = int(thread_id)
    try:
        requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        log.warning(f"send failed: {e}")


def send_with_keyboard(chat_id: int, text: str, keyboard: list,
                       parse_mode: str = "Markdown", thread_id: int = None):
    """发送带 InlineKeyboard 的消息。keyboard 是二维按钮列表。"""
    payload = {
        "chat_id":      chat_id,
        "text":         text,
        "parse_mode":   parse_mode,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    if thread_id:
        payload["message_thread_id"] = int(thread_id)
    try:
        requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        log.warning(f"send_with_keyboard failed: {e}")


def answer_callback(callback_id: str, text: str = ""):
    """应答 callback query，防止客户端转圈。"""
    try:
        requests.post(f"{API_URL}/answerCallbackQuery",
                      json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except Exception:
        pass


# ── 待确认交易暂存（内联键盘确认流程）────────────────────────────────────────
# key: str(chat_id), value: dict with trade params + expires_at
_pending_trades: dict = {}

# ── Skill 工厂 ────────────────────────────────────────────────────────────────

_skill_map = {
    "hl_monitor":    ("skills.hl_monitor",    "HLMonitorSkill"),
    "crypto_data":   ("skills.crypto_data",   "CryptoDataSkill"),
    "crypto_news":   ("skills.crypto_news",   "CryptoNewsSkill"),
    "crypto_alert":  ("skills.crypto_alert",  "CryptoAlertSkill"),
    "crypto_report": ("skills.crypto_report", "CryptoReportSkill"),
    "funding_arb":   ("skills.funding_arb",   "FundingArbSkill"),
    "hl_grid":       ("skills.hl_grid",       "HLGridSkill"),
    "hl_trade":      ("skills.hl_trade",      "HLTradeSkill"),
}

def skill(name: str, override_env: dict = None):
    module_path, class_name = _skill_map[name]
    mod = importlib.import_module(module_path)
    merged_env = {**env, **(override_env or {})}
    return getattr(mod, class_name)(DATA_DIR, MEMORY_DIR, merged_env)

# ── 已知交易对 ────────────────────────────────────────────────────────────────

_symbols_cache: set = set()

def known_symbols() -> set:
    global _symbols_cache
    path = DATA_DIR / "hl_market.json"
    if not path.exists():
        return _symbols_cache
    try:
        with open(path) as f:
            raw = json.load(f)
        data = raw.get("data") if "data" in raw else raw
        _symbols_cache = {a["symbol"] for a in data.get("assets", [])}
    except Exception:
        pass
    return _symbols_cache

# ── 帮助 & 引导文本 ───────────────────────────────────────────────────────────

ONBOARD_TEXT = r"""👋 *欢迎使用 Clawie！*

我是一个 Hyperliquid 永续合约交易助手，可以帮你：
• 监控资金费率、持仓风险、市场异动
• 自动发送预警通知
• 手动或自动执行永续合约交易

─────────────────────────
*第一步：查看市场*

/market — 市场总览（资金费率 + 情绪 + 账户）
/funding — 资金费率排行（做多前必看！）
/alerts — 当前异动信号扫描

─────────────────────────
*第二步：查看我的账户*

/position — 我的持仓和余额
/liq — 爆仓风险评估

─────────────────────────
*第三步：手动下单*

点击下方按钮或直接发命令：

`/trade open ETH long 100` — 做多 ETH $100
`/trade open BTC short 200 3` — 做空 BTC $200，3倍杠杆
`/trade close ETH` — 平仓 ETH

⚠️ 首次使用建议先在 .env 设置 `HL_USE_TESTNET=true` 用测试网练习

─────────────────────────
*第四步（进阶）：自动交易*

让 Bot 根据资金费率信号自动开平仓：
/autotrade — 查看自动交易状态和开启方式

─────────────────────────
/status — 查看 Bot 运行状态
/help — 完整指令列表"""

HELP_TEXT = r"""🤖 *Clawie 指令列表*

*账户*
/position — 持仓明细和余额
/liq — 爆仓风险评估

*HL 市场*
/market — 市场概览
/funding — 资金费率排行
/funding BTC — 指定币种费率
/oi — 未平仓量排行
/oi BTC — 指定币种 OI

*行情*
/price — 价格（默认 BTC）
/price ETH — 指定币种价格
/fng — 恐慌贪婪指数

*快讯*
/news — 最新快讯
/hlnews — HL 相关快讯

*信号与报告*
/alerts — 全部异动信号
/report — 今日报告
/weekly — 本周复盘

*交易*
/trade open ETH long 100 — 开多（币种 方向 金额USD）
/trade open BTC short 200 3 — 开空（含杠杆倍数）
/trade close ETH — 平仓
/trade cancel ETH 12345 — 撤单（需 order\_id）
/trade leverage ETH 5 cross — 设置杠杆
/trade — 查看当前持仓
/override\_circuit — 临时覆盖当日亏损熔断（1小时）

*自动交易*
/autotrade — 查看自动交易状态
/autotrade on — 启用自动交易
/autotrade off — 关闭自动交易

*系统*
/status — Bot 运行状态一览

*套利与策略*
/arb scan — 扫描资金费套利机会
/arb open BTC 500 — 记录套利仓位（币种 金额USD）
/arb status — 查看当前套利仓位
/arb close BTC — 关闭套利记录
/grid BTC 90000 100000 10 50 — 创建网格（低价 高价 格数 每格USD）
/grid — 查看网格状态
/grid cancel <grid\_id> — 取消网格
/backtest — 运行资金费策略回测

*快捷查询*
/BTC /ETH /SOL 等任意交易对 — 价格 + 资金费率 + OI"""

# ── 命令路由 ──────────────────────────────────────────────────────────────────

def handle(update: dict):
    # ── 内联键盘回调 ─────────────────────────────────────────────────────────
    if "callback_query" in update:
        _handle_callback(update["callback_query"])
        return

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat_id   = msg["chat"]["id"]
    thread_id = msg.get("message_thread_id")
    text      = msg.get("text", "").strip()

    if not text.startswith("/"):
        return

    parts = text.split()
    cmd   = parts[0].lstrip("/").split("@")[0].lower()
    args  = parts[1:]

    log.info(f"cmd=/{cmd} args={args} chat={chat_id} thread={thread_id}")

    try:
        _route(chat_id, cmd, args, thread_id=thread_id)
    except Exception as e:
        log.error(f"route error: {e}", exc_info=True)
        send(chat_id, f"❌ 执行出错：{e}", thread_id=thread_id)


def _handle_callback(cq: dict):
    """处理内联键盘按钮点击。"""
    cq_id     = cq["id"]
    chat_id   = cq["message"]["chat"]["id"]
    thread_id = cq["message"].get("message_thread_id")
    data      = cq.get("data", "")

    answer_callback(cq_id)

    key = str(chat_id)
    pending = _pending_trades.get(key)

    if data == "trade_confirm" and pending:
        # 检查是否过期（5分钟）
        expires = datetime.fromisoformat(pending["expires_at"])
        if datetime.now(timezone.utc) > expires:
            _pending_trades.pop(key, None)
            send(chat_id, "⏰ 确认已超时（5分钟），请重新发送交易指令", thread_id=thread_id)
            return

        _pending_trades.pop(key, None)
        sym      = pending["symbol"]
        side     = pending["side"]
        size_usd = pending["size_usd"]
        lev      = pending.get("leverage")

        # 以 AUTONOMOUS_MODE=true 执行
        trade_env = {k: os.getenv(k, "") for k in [
            "HL_PRIVATE_KEY", "HL_WALLET_ADDRESS", "HL_USE_TESTNET",
            "HL_DEFAULT_LEVERAGE", "HL_DEFAULT_MARGIN_MODE",
            "MAX_POSITION_SIZE_USD", "MAX_DAILY_LOSS_PCT",
        ]}
        trade_env["AUTONOMOUS_MODE"] = "true"
        r = skill("hl_trade", override_env=trade_env).run(
            action="open", symbol=sym, side=side, size_usd=size_usd, leverage=lev
        )
        send(chat_id, r["text"], thread_id=thread_id)

    elif data == "trade_cancel":
        _pending_trades.pop(key, None)
        send(chat_id, "❌ 已取消开仓", thread_id=thread_id)

    elif data == "close_confirm" and pending:
        expires = datetime.fromisoformat(pending["expires_at"])
        if datetime.now(timezone.utc) > expires:
            _pending_trades.pop(key, None)
            send(chat_id, "⏰ 确认已超时，请重新发送指令", thread_id=thread_id)
            return

        _pending_trades.pop(key, None)
        sym = pending["symbol"]
        trade_env = {k: os.getenv(k, "") for k in [
            "HL_PRIVATE_KEY", "HL_WALLET_ADDRESS", "HL_USE_TESTNET",
            "HL_DEFAULT_LEVERAGE", "HL_DEFAULT_MARGIN_MODE",
            "MAX_POSITION_SIZE_USD", "MAX_DAILY_LOSS_PCT",
        ]}
        trade_env["AUTONOMOUS_MODE"] = "true"
        r = skill("hl_trade", override_env=trade_env).run(action="close", symbol=sym)
        send(chat_id, r["text"], thread_id=thread_id)

    elif data == "close_cancel":
        _pending_trades.pop(key, None)
        send(chat_id, "❌ 已取消平仓", thread_id=thread_id)


def _route(chat_id: int, cmd: str, args: list, thread_id: int = None):
    # 辅助：按命令类型选择目标 topic（用户在哪个 topic 发就回哪里，
    # thread_id 为 None 时退回默认 topic）
    def _tid(default_topic):
        return thread_id if thread_id is not None else default_topic

    # ── 账户 ────────────────────────────────────────────────────────────────
    if cmd in ("position", "positions", "pos", "账户", "持仓"):
        r = skill("hl_monitor").run(action="account")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_POSITION))

    elif cmd in ("liq", "liquidation", "risk", "爆仓"):
        r = skill("hl_monitor").run(action="liquidation")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_POSITION))

    # ── HL 市场 ──────────────────────────────────────────────────────────────
    elif cmd == "market":
        r = skill("hl_monitor").run(action="overview")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd == "funding":
        symbol = args[0].upper() if args else None
        r = skill("hl_monitor").run(action="funding", symbol=symbol)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd == "oi":
        symbol = args[0].upper() if args else None
        r = skill("hl_monitor").run(action="oi", symbol=symbol)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 行情 ─────────────────────────────────────────────────────────────────
    elif cmd == "price":
        symbol = args[0].upper() if args else "BTC"
        r = skill("crypto_data").run(action="price", symbol=symbol)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("fng", "fear", "greed", "情绪"):
        r = skill("crypto_data").run(action="fng")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 快讯 ─────────────────────────────────────────────────────────────────
    elif cmd == "news":
        r = skill("crypto_news").run(action="latest")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd == "hlnews":
        r = skill("crypto_news").run(action="hl")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 信号 ─────────────────────────────────────────────────────────────────
    elif cmd in ("alerts", "alert", "signals", "信号"):
        r = skill("crypto_alert").run(action="scan")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_ALERT))

    # ── 报告 ─────────────────────────────────────────────────────────────────
    elif cmd in ("report", "日报"):
        r = skill("crypto_report").run(period="daily")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("weekly", "周报"):
        r = skill("crypto_report").run(period="weekly")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 套利策略 ─────────────────────────────────────────────────────────────
    elif cmd in ("arb",):
        sub = args[0].lower() if args else "scan"
        sym = args[1].upper() if len(args) > 1 else None
        try:
            size = float(args[2]) if len(args) > 2 else 100.0
        except ValueError:
            send(chat_id, f"❌ 金额格式错误：`{args[2]}`，请输入数字，例如：`/arb open BTC 500`",
                 thread_id=_tid(TOPIC_TRADE))
            return
        r = skill("funding_arb").run(action=sub, symbol=sym, size_usd=size)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))

    # ── 网格交易 ─────────────────────────────────────────────────────────────
    elif cmd in ("grid",):
        if not args:
            r = skill("hl_grid").run(action="status")
        elif args[0].lower() == "cancel" and len(args) > 1:
            r = skill("hl_grid").run(action="cancel", grid_id=args[1])
        elif args[0].upper() in known_symbols() and len(args) >= 3:
            r = skill("hl_grid").run(action="create", args=args)
        else:
            r = skill("hl_grid").run(action="status")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))

    # ── 回测 ─────────────────────────────────────────────────────────────────
    elif cmd in ("backtest", "bt"):
        send(chat_id, "⏳ 正在运行回测（合成数据）...", thread_id=_tid(TOPIC_TRADE))
        try:
            from backtest.engine import BacktestEngine, FundingArbStrategy
            engine = BacktestEngine()
            engine.load_sample_data(n_periods=300)
            result = engine.run(FundingArbStrategy(entry_threshold=0.0005))
            send(chat_id, result.summary(), thread_id=_tid(TOPIC_TRADE))
        except Exception as e:
            send(chat_id, f"❌ 回测失败：{e}", thread_id=_tid(TOPIC_TRADE))

    # ── 交易指令 ─────────────────────────────────────────────────────────────
    elif cmd == "trade":
        sub = args[0].lower() if args else "positions"
        if sub in ("open", "做多", "做空"):
            sym     = args[1].upper() if len(args) > 1 else "ETH"
            side    = args[2].lower() if len(args) > 2 else "long"
            try:
                size_usd = float(args[3]) if len(args) > 3 else 100.0
            except ValueError:
                send(chat_id, "❌ 格式：`/trade open <币种> <long|short> <金额USD>`",
                     thread_id=_tid(TOPIC_TRADE))
                return
            lev = int(args[4]) if len(args) > 4 else None

            autonomous = os.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
            if autonomous:
                r = skill("hl_trade").run(action="open", symbol=sym, side=side,
                                          size_usd=size_usd, leverage=lev)
                send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))
            else:
                # 显示内联键盘确认
                lev_display = lev or os.getenv("HL_DEFAULT_LEVERAGE", "3")
                _pending_trades[str(chat_id)] = {
                    "type":       "open",
                    "symbol":     sym,
                    "side":       side,
                    "size_usd":   size_usd,
                    "leverage":   lev,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                }
                keyboard = [[
                    {"text": "✅ 确认开仓", "callback_data": "trade_confirm"},
                    {"text": "❌ 取消",     "callback_data": "trade_cancel"},
                ]]
                send_with_keyboard(
                    chat_id,
                    f"⏳ *确认开仓*\n\n"
                    f"• 标的：`{sym}`\n"
                    f"• 方向：{'做多 📈' if side == 'long' else '做空 📉'}\n"
                    f"• 金额：`${size_usd}` USDC\n"
                    f"• 杠杆：`{lev_display}x`\n\n"
                    f"_点击确认后将立即以市价下单_",
                    keyboard,
                    thread_id=_tid(TOPIC_TRADE),
                )
            return

        elif sub in ("close", "平仓"):
            sym = args[1].upper() if len(args) > 1 else None
            if not sym:
                send(chat_id, "❌ 格式：`/trade close <币种>`", thread_id=_tid(TOPIC_TRADE))
                return

            autonomous = os.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
            if autonomous:
                r = skill("hl_trade").run(action="close", symbol=sym)
                send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))
            else:
                _pending_trades[str(chat_id)] = {
                    "type":       "close",
                    "symbol":     sym,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                }
                keyboard = [[
                    {"text": "✅ 确认平仓", "callback_data": "close_confirm"},
                    {"text": "❌ 取消",     "callback_data": "close_cancel"},
                ]]
                send_with_keyboard(
                    chat_id,
                    f"⏳ *确认平仓*\n\n• 标的：`{sym}`\n\n_将以市价全部平仓_",
                    keyboard,
                    thread_id=_tid(TOPIC_TRADE),
                )
            return

        elif sub in ("cancel", "撤单"):
            sym = args[1].upper() if len(args) > 1 else None
            oid = int(args[2]) if len(args) > 2 else None
            if not sym or not oid:
                send(chat_id, "❌ 格式：`/trade cancel <币种> <order_id>`",
                     thread_id=_tid(TOPIC_TRADE))
                return
            r = skill("hl_trade").run(action="cancel", symbol=sym, order_id=oid)
        elif sub in ("leverage", "杠杆"):
            sym  = args[1].upper() if len(args) > 1 else "ETH"
            lev  = int(args[2]) if len(args) > 2 else 3
            mode = args[3].lower() if len(args) > 3 else "cross"
            r = skill("hl_trade").run(action="leverage", symbol=sym,
                                      leverage=lev, margin_mode=mode)
        else:
            r = skill("hl_trade").run(action="positions")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))

    # ── 熔断覆盖 ─────────────────────────────────────────────────────────────
    elif cmd == "override_circuit":
        import json as _json
        from datetime import timedelta
        override_path = MEMORY_DIR / "circuit_override.json"
        MEMORY_DIR.mkdir(exist_ok=True)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        override_path.write_text(_json.dumps({"expires_at": expires}))
        send(chat_id,
             f"⚠️ *熔断已临时覆盖*\n"
             f"有效期：1 小时（至 {expires[:16]} UTC）\n"
             f"此期间新开仓不受每日亏损限制，请谨慎操作。",
             thread_id=_tid(TOPIC_TRADE))

    # ── 快捷查询任意交易对 ────────────────────────────────────────────────────
    elif cmd.upper() in known_symbols():
        r = skill("hl_monitor").run(action="funding", symbol=cmd.upper())
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 自动交易管理 ──────────────────────────────────────────────────────────
    elif cmd == "autotrade":
        sub = args[0].lower() if args else "status"
        auto_enabled   = os.getenv("AUTO_TRADE_ENABLED", "false").lower() == "true"
        autonomous     = os.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
        min_conf       = os.getenv("AUTO_TRADE_MIN_CONFIDENCE", "0.7")
        size_usd       = os.getenv("AUTO_TRADE_SIZE_USD", "50")
        max_pos        = os.getenv("AUTO_TRADE_MAX_POSITIONS", "2")
        profit_pct     = os.getenv("AUTO_TRADE_PROFIT_PCT", "3")
        stop_pct       = os.getenv("AUTO_TRADE_STOP_PCT", "2")
        exit_funding   = os.getenv("AUTO_TRADE_EXIT_FUNDING", "0.0001")

        if sub in ("on", "enable", "开启"):
            send(chat_id,
                 "⚠️ *开启自动交易*\n\n"
                 "需要在服务器的 `.env` 文件中设置以下配置：\n\n"
                 "```\n"
                 "AUTONOMOUS_MODE=true        # 允许自动执行交易\n"
                 "AUTO_TRADE_ENABLED=true     # 开启自动交易任务\n"
                 "AUTO_TRADE_SIZE_USD=50      # 每笔金额（建议从小值开始）\n"
                 "AUTO_TRADE_MIN_CONFIDENCE=0.7  # 触发阈值\n"
                 "AUTO_TRADE_PROFIT_PCT=3     # 止盈 %\n"
                 "AUTO_TRADE_STOP_PCT=2       # 止损 %\n"
                 "```\n\n"
                 "修改后重启调度器：`pm2 restart clawie-scheduler`\n\n"
                 "⚡ *建议先用测试网（`HL_USE_TESTNET=true`）验证！*",
                 thread_id=_tid(TOPIC_TRADE))
        elif sub in ("off", "disable", "关闭"):
            send(chat_id,
                 "在 `.env` 中设置 `AUTO_TRADE_ENABLED=false`，"
                 "然后 `pm2 restart clawie-scheduler` 生效",
                 thread_id=_tid(TOPIC_TRADE))
        else:
            # 显示状态
            auto_trades_path = MEMORY_DIR / "auto_trades.json"
            auto_trades = []
            if auto_trades_path.exists():
                try:
                    with open(auto_trades_path) as f:
                        auto_trades = json.load(f)
                except Exception:
                    pass

            status_icon = "✅" if (auto_enabled and autonomous) else ("⚠️" if auto_enabled else "⏸️")
            lines = [
                f"🤖 *自动交易状态*\n",
                f"{status_icon} 自动交易：{'开启' if auto_enabled else '关闭'}",
                f"{'✅' if autonomous else '⚠️'} 自主模式（AUTONOMOUS_MODE）：{'开启' if autonomous else '关闭'}",
            ]
            if auto_enabled and not autonomous:
                lines.append("\n⚠️ _需同时开启 `AUTONOMOUS_MODE=true` 才能自动执行_")

            lines.append(f"\n*配置*")
            lines.append(f"• 每笔金额：`${size_usd}` USDC")
            lines.append(f"• 最低置信度：`{float(min_conf)*100:.0f}%`")
            lines.append(f"• 最多仓位：`{max_pos}` 个")
            lines.append(f"• 止盈：`+{profit_pct}%` / 止损：`-{stop_pct}%`")
            lines.append(f"• 退出费率：`< {float(exit_funding)*100:.4f}%/8h`")

            if auto_trades:
                lines.append(f"\n*当前自动仓位（{len(auto_trades)} 个）*")
                for at in auto_trades:
                    ann = abs(at.get("entry_funding", 0)) * 3 * 365 * 100
                    lines.append(
                        f"• `{at['symbol']}` {at['side']} ${at.get('size_usd', '?')} "
                        f"| 入场费率 ~{ann:.0f}% 年化"
                    )
            else:
                lines.append("\n当前无自动持仓")

            lines.append("\n/autotrade on — 查看开启方法")
            send(chat_id, "\n".join(lines), thread_id=_tid(TOPIC_TRADE))

    # ── 系统状态 ──────────────────────────────────────────────────────────────
    elif cmd in ("status", "健康", "状态"):
        has_key    = bool(os.getenv("HL_PRIVATE_KEY"))
        has_wallet = bool(os.getenv("HL_WALLET_ADDRESS"))
        autonomous = os.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
        auto_trade = os.getenv("AUTO_TRADE_ENABLED", "false").lower() == "true"
        testnet    = os.getenv("HL_USE_TESTNET", "false").lower() == "true"

        # 数据新鲜度
        from skills.base import BaseSkill
        _bs = BaseSkill(DATA_DIR, MEMORY_DIR, env)
        mkt_age = _bs.data_age_minutes("hl_market.json")
        acc_age = _bs.data_age_minutes("hl_account.json")

        if mkt_age < 6:
            data_status = f"✅ 正常（{mkt_age:.0f} 分钟前）"
        elif mkt_age < 30:
            data_status = f"⚠️ 偏旧（{mkt_age:.0f} 分钟前）"
        else:
            data_status = f"❌ 过期（{mkt_age:.0f} 分钟前，调度器可能未运行）"

        # 自动交易仓位
        auto_trades_path = MEMORY_DIR / "auto_trades.json"
        auto_count = 0
        if auto_trades_path.exists():
            try:
                with open(auto_trades_path) as f:
                    auto_count = len(json.load(f))
            except Exception:
                pass

        lines = [
            "📊 *系统状态*\n",
            "*配置*",
            f"{'✅' if has_key    else '❌'} HL 私钥",
            f"{'✅' if has_wallet else '❌'} 钱包地址",
            f"{'🧪' if testnet    else '🌐'} 网络：{'测试网' if testnet else '主网'}",
            f"{'✅' if autonomous else '⚠️'} 自主模式（手动交易确认）：{'开启' if autonomous else '关闭'}",
            f"{'✅' if auto_trade else '⏸️'} 自动交易：{'开启' if auto_trade else '关闭'}",
            f"\n*数据*",
            f"市场数据：{data_status}",
            f"账户数据：{acc_age:.0f} 分钟前",
            f"\n*自动仓位*：{auto_count} 个",
            f"\n*快速操作*",
            f"/market — 市场概览",
            f"/position — 我的持仓",
            f"/alerts — 异动信号",
            f"/autotrade — 自动交易配置",
        ]
        send(chat_id, "\n".join(lines), thread_id=thread_id)

    # ── 帮助 ─────────────────────────────────────────────────────────────────
    elif cmd == "start":
        send(chat_id, ONBOARD_TEXT, thread_id=thread_id)

    elif cmd in ("help", "帮助"):
        send(chat_id, HELP_TEXT, thread_id=thread_id)

    else:
        send(chat_id, f"未知指令 `/{cmd}`，发送 /help 查看所有命令", thread_id=thread_id)

# ── 注册 Telegram 命令提示 ────────────────────────────────────────────────────

def register_commands():
    our_commands = [
        {"command": "market",   "description": "市场概览（资金费率+情绪+账户摘要）"},
        {"command": "position", "description": "我的持仓明细和余额"},
        {"command": "funding",  "description": "资金费率排行，/funding BTC 查指定币种"},
        {"command": "oi",       "description": "未平仓量排行，/oi BTC 查指定币种"},
        {"command": "liq",      "description": "爆仓风险评估"},
        {"command": "price",    "description": "实时价格，/price ETH 查指定币种"},
        {"command": "fng",      "description": "恐慌贪婪指数"},
        {"command": "news",     "description": "最新快讯（前10条）"},
        {"command": "hlnews",   "description": "Hyperliquid 相关快讯"},
        {"command": "alerts",   "description": "全部异动信号扫描"},
        {"command": "report",   "description": "今日市场报告"},
        {"command": "weekly",   "description": "本周复盘报告"},
        {"command": "arb",      "description": "套利 — /arb scan | /arb open BTC 500 | /arb status"},
        {"command": "grid",     "description": "网格 — /grid BTC 90000 100000 10 50 | /grid status"},
        {"command": "backtest",          "description": "策略回测（合成数据快速验证）"},
        {"command": "trade",             "description": "交易 — /trade open ETH long 100 | /trade close ETH"},
        {"command": "override_circuit",  "description": "临时覆盖每日亏损熔断（1小时有效）"},
        {"command": "autotrade",         "description": "自动交易状态 / on / off"},
        {"command": "status",            "description": "Bot 运行状态一览"},
        {"command": "help",              "description": "查看所有指令"},
    ]
    our_names = {c["command"] for c in our_commands}

    try:
        # 获取现有命令
        existing = requests.get(f"{API_URL}/getMyCommands", timeout=10).json().get("result", [])
        # 保留不属于我们的命令，避免覆盖其他功能
        kept = [c for c in existing if c["command"] not in our_names]
        merged = kept + our_commands
        r = requests.post(f"{API_URL}/setMyCommands", json={"commands": merged}, timeout=10)
        if r.json().get("ok"):
            log.info(f"Telegram commands registered ({len(our_commands)} new, {len(kept)} kept, {len(merged)} total)")
        else:
            log.warning(f"setMyCommands failed: {r.text}")
    except Exception as e:
        log.warning(f"register_commands error: {e}")

# ── 主循环 ────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    register_commands()
    log.info("Clawie bot starting (long polling)...")
    offset = 0

    while True:
        try:
            r = requests.get(f"{API_URL}/getUpdates", params={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=40)
            updates = r.json().get("result", [])
            for u in updates:
                handle(u)
                offset = u["update_id"] + 1
        except Exception as e:
            log.warning(f"polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
