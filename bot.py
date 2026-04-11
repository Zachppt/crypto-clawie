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
    /ta BTC            — 技术分析（RSI/MA/BB/MACD）
    /ta ETH 4h signal  — 指定周期 + 精简信号

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

  数据上下文整理（配合群组内 AI Agent 使用）
    /ask <问题>        — 整理市场数据 + 附问题，@AI Agent 分析
    /deep <TOKEN>      — 整理币种深度数据（MM 阶段 + 跨所费率）
    /advice            — 整理当前持仓数据，@AI Agent 给建议
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
    "TRADING_EXCHANGE",
    "BINANCE_API_KEY", "BINANCE_SECRET_KEY",
    "OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE",
    "BYBIT_API_KEY", "BYBIT_SECRET_KEY",
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

# ── 策略向导状态机 ─────────────────────────────────────────────────────────────
# key: str(chat_id), value: {"step": int, "data": dict, "thread_id": int|None}
_wizard_state: dict = {}

WIZARD_STEPS = [
    ("token",       "📌 *第 1/6 步*\n你想交易哪个币种？\n\n例：`BTC`、`ETH`、`SOL`"),
    ("direction",   "📌 *第 2/6 步*\n交易方向？\n\n• `long` — 只做多\n• `short` — 只做空\n• `both` — 双向均可"),
    ("entry_type",  "📌 *第 3/6 步*\n入场触发条件？\n\n• `funding` — 资金费率超阈值时入场\n• `agent` — 由 Agent 多因子评分决策\n• `manual` — 我自己看时机，Agent 帮我执行"),
    ("size_usd",    "📌 *第 4/6 步*\n每笔仓位金额？（USD）\n\n例：`100`（建议从小额开始）"),
    ("stop_pct",    "📌 *第 5/6 步*\n止损百分比？\n\n例：`2` 表示亏损 2% 自动平仓"),
    ("profit_pct",  "📌 *第 6/6 步*\n止盈百分比？\n\n例：`5` 表示盈利 5% 自动平仓"),
]

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
    "exchange_agg":  ("skills.exchange_agg",  "ExchangeAggSkill"),
    "onchain":       ("skills.onchain",       "OnchainSkill"),
    "agent_trade":   ("skills.agent_trade",   "AgentTradeSkill"),
    "net_flow":      ("skills.net_flow",      "NetFlowSkill"),
    "mm_analysis":   ("skills.mm_analysis",   "MMAnalysisSkill"),
    "focus":         ("skills.focus",          "FocusSkill"),
    "ai_agent":        ("skills.ai_agent",         "AIAgentSkill"),
    "exchange_trade":  ("skills.exchange_trade",   "ExchangeTradeSkill"),
    "ta_analysis":     ("skills.ta_analysis",      "TAAnalysisSkill"),
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

*技术分析*
/ta BTC — BTC 1h 技术全分析（RSI + MA + BB + MACD）
/ta ETH 4h — 指定时间周期
/ta SOL 1d signal — 只看交易信号
/ta BTC 1h ohlcv — 原始 K 线数据

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

*多交易所聚合*
/compare BTC — 跨所价格对比（Binance/OKX/Bybit/Gate/HL）
/exfunding BTC — 跨所资金费率对比
/vol BTC — 跨所成交量对比
/divergence — 扫描主流币跨所价差

*Agent 智能交易*
/agent scan — 全市场多因子分析
/agent status — Agent 状态与近期决策
/agent history — 历史决策记录

*数据上下文整理（配合 AI Agent 使用）*
/ask 现在 SOL 适合做多吗？ — 整理市场数据 + 附问题，供 @AI Agent 分析
/deep BTC — 整理指定币种深度数据（MM 阶段 + 跨所费率）
/advice — 整理当前持仓数据，供 @AI Agent 给出操作建议

*链上监控*
/watch add ETH 0x1234... 标签 — 添加地址监控
/watch list — 查看监控列表
/watch ETH 0x1234... — 查地址近期交易
/watch remove ETH 0x1234... — 移除监控
/chains — 链上监控概览

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

    if not text:
        return

    # ── 策略向导拦截（优先于命令路由） ───────────────────────────────────────
    cid_key = str(chat_id)
    if cid_key in _wizard_state and not text.startswith("/cancel"):
        try:
            _handle_wizard(chat_id, text, thread_id)
        except Exception as e:
            log.error(f"wizard error: {e}", exc_info=True)
            send(chat_id, f"❌ 向导出错：{e}", thread_id=thread_id)
        return

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


def _handle_wizard(chat_id: int, text: str, thread_id: int = None):
    """处理策略向导多步对话。"""
    cid_key = str(chat_id)
    state   = _wizard_state.get(cid_key)
    if not state:
        return

    step     = state["step"]
    data     = state["data"]
    field, _ = WIZARD_STEPS[step]
    answer   = text.strip()

    # 简单校验
    validators = {
        "token":       lambda v: v.upper() if v.isalpha() and len(v) <= 10 else None,
        "direction":   lambda v: v.lower() if v.lower() in ("long", "short", "both") else None,
        "entry_type":  lambda v: v.lower() if v.lower() in ("funding", "agent", "manual") else None,
        "size_usd":    lambda v: str(float(v)) if float(v) >= 1 else None,
        "stop_pct":    lambda v: str(float(v)) if 0 < float(v) <= 50 else None,
        "profit_pct":  lambda v: str(float(v)) if 0 < float(v) <= 100 else None,
    }

    try:
        validated = validators[field](answer)
    except Exception:
        validated = None

    if validated is None:
        send(chat_id, f"⚠️ 输入无效，请重新输入。", thread_id=thread_id)
        return

    data[field] = validated
    next_step   = step + 1

    if next_step < len(WIZARD_STEPS):
        # 继续下一步
        _wizard_state[cid_key]["step"] = next_step
        _, prompt = WIZARD_STEPS[next_step]
        send(chat_id, prompt + "\n\n_发送 /cancel 退出向导_", thread_id=thread_id)
    else:
        # 向导完成
        del _wizard_state[cid_key]
        _save_strategy(data)

        token     = data["token"].upper()
        direction = data["direction"]
        size      = float(data["size_usd"])
        stop      = float(data["stop_pct"])
        profit    = float(data["profit_pct"])
        etype     = data["entry_type"]

        etype_label = {"funding": "资金费率触发", "agent": "Agent 评分决策", "manual": "我手动确认"}.get(etype, etype)

        summary = (
            f"✅ *策略配置已保存！*\n\n"
            f"• 标的：`{token}`\n"
            f"• 方向：`{direction}`\n"
            f"• 入场方式：`{etype_label}`\n"
            f"• 每笔仓位：`${size:.0f}` USDC\n"
            f"• 止损：`-{stop}%`\n"
            f"• 止盈：`+{profit}%`\n\n"
            f"Agent 会在满足条件时自动执行此策略。\n"
            f"发送 `/strategy show` 查看，`/strategy off` 暂停。"
        )
        send(chat_id, summary, thread_id=thread_id)
        log.info(f"Strategy wizard completed for chat {chat_id}: {data}")


def _save_strategy(data: dict):
    """将策略配置写入 memory/my_strategy.json。"""
    path = MEMORY_DIR / "my_strategy.json"
    path.parent.mkdir(exist_ok=True)
    strategy = {
        **data,
        "enabled":    True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w") as f:
        json.dump(strategy, f, ensure_ascii=False, indent=2)


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

    # ── 技术分析 ──────────────────────────────────────────────────────────────
    # 用法：/ta [symbol] [timeframe] [action] [exchange]
    # 例：  /ta BTC 4h       /ta ETH 1d signal    /ta SOL 1h ohlcv binance
    elif cmd == "ta":
        _VALID_TF      = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
        _VALID_ACTIONS = {"analysis", "signal", "ohlcv"}
        _VALID_EX      = {"binance", "okx", "bybit", "gateio", "bitget"}
        ta_sym    = "BTC"
        ta_tf     = "1h"
        ta_action = "analysis"
        ta_ex     = "binance"
        for a in args:
            al = a.lower()
            if al in _VALID_TF:
                ta_tf = al
            elif al in _VALID_ACTIONS:
                ta_action = al
            elif al in _VALID_EX:
                ta_ex = al
            elif a.upper().isalpha():
                ta_sym = a.upper()
        r = skill("ta_analysis").run(action=ta_action, symbol=ta_sym,
                                     exchange=ta_ex, timeframe=ta_tf)
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
        send(chat_id, "🤖 正在扫描市场...", thread_id=_tid(TOPIC_ALERT))
        r = skill("agent_trade").run(action="analyze")
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

        # 检测交易所：最后一个参数若是已知交易所名则提取，否则用默认值
        _known_exchanges = ("binance", "okx", "bybit", "hyperliquid", "hl")
        _ex_arg = args[-1].lower() if args and args[-1].lower() in _known_exchanges else None
        if _ex_arg:
            args = args[:-1]
        _exchange = (_ex_arg or os.getenv("TRADING_EXCHANGE", "hyperliquid")).lower()
        _exchange = "hyperliquid" if _exchange == "hl" else _exchange

        # 非 HL 交易所 → 路由到 exchange_trade
        if _exchange != "hyperliquid":
            _ex_label = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}.get(_exchange, _exchange)
            if sub in ("open", "做多", "做空"):
                sym     = args[1].upper() if len(args) > 1 else "BTC"
                side    = args[2].lower() if len(args) > 2 else "long"
                try:
                    size_usd = float(args[3]) if len(args) > 3 else 100.0
                except ValueError:
                    send(chat_id, "❌ 格式：`/trade open BTC long 100 binance`", thread_id=_tid(TOPIC_TRADE))
                    return
                lev = int(args[4]) if len(args) > 4 else None
                send(chat_id, f"⏳ 正在 {_ex_label} 开仓...", thread_id=_tid(TOPIC_TRADE))
                r = skill("exchange_trade").run(
                    action="open", exchange=_exchange,
                    symbol=sym, side=side, size_usd=size_usd, leverage=lev,
                )
            elif sub in ("close", "平仓"):
                sym = args[1].upper() if len(args) > 1 else None
                if not sym:
                    send(chat_id, "❌ 格式：`/trade close BTC binance`", thread_id=_tid(TOPIC_TRADE))
                    return
                send(chat_id, f"⏳ 正在 {_ex_label} 平仓...", thread_id=_tid(TOPIC_TRADE))
                r = skill("exchange_trade").run(action="close", exchange=_exchange, symbol=sym)
            elif sub in ("leverage", "杠杆"):
                sym = args[1].upper() if len(args) > 1 else "BTC"
                lev = int(args[2]) if len(args) > 2 else 3
                r = skill("exchange_trade").run(action="leverage", exchange=_exchange, symbol=sym, leverage=lev)
            else:
                r = skill("exchange_trade").run(action="positions", exchange=_exchange)
            send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))
            return

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

    # ── 专项追踪 ─────────────────────────────────────────────────────────────
    elif cmd in ("track", "追踪"):  # 注意：/focus 被 OpenClaw Agent 占用，改用 /track
        sub = args[0].lower() if args else "status"

        if sub in ("cancel", "stop", "取消", "停止"):
            r = skill("focus").run(action="cancel")

        elif sub in ("status", "状态"):
            r = skill("focus").run(action="status")

        elif sub in ("report", "报告", "now"):
            send(chat_id, "⏳ 正在生成专项报告...", thread_id=_tid(TOPIC_MARKET))
            # /track report [TOKEN] — 立即生成，token 可选
            token = args[1].upper() if len(args) > 1 else None
            r = skill("focus").run(action="report", token=token)

        else:
            # /track SOL [15]  — sub 就是 token
            token        = sub.upper()
            interval_min = int(args[1]) if len(args) > 1 and args[1].isdigit() else 15
            r = skill("focus").run(
                action="set", token=token, interval_min=interval_min,
                chat_id=str(chat_id), topic_id=str(_tid(TOPIC_MARKET)) if TOPIC_MARKET else None,
            )

        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 做市商阶段分析 ────────────────────────────────────────────────────────
    elif cmd in ("mm", "phase", "阶段"):
        sub = args[0].upper() if args else None

        if sub in ("scan", "all", "全局") or not sub:
            send(chat_id, "🕵️ 正在扫描做市商阶段...", thread_id=_tid(TOPIC_MARKET))
            r = skill("mm_analysis").run(action="scan")
        else:
            # /mm SOL — 分析指定币种
            send(chat_id, f"🕵️ 正在分析 {sub} 做市商阶段...", thread_id=_tid(TOPIC_MARKET))
            r = skill("mm_analysis").run(action="analyze", symbol=sub)

        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 用户策略配置 ──────────────────────────────────────────────────────────
    elif cmd in ("strategy", "策略"):
        sub = args[0].lower() if args else "show"

        if sub in ("new", "set", "新建", "配置"):
            # 启动向导
            cid_key = str(chat_id)
            _wizard_state[cid_key] = {"step": 0, "data": {}, "thread_id": thread_id}
            _, prompt = WIZARD_STEPS[0]
            send(chat_id,
                 "🎯 *策略配置向导*\n\n"
                 "我将引导你设置一个 24h 自动执行的交易策略。\n"
                 "共 6 步，随时发送 /cancel 退出。\n\n" + prompt,
                 thread_id=thread_id)
            return  # 不执行后续 send

        elif sub in ("show", "view", "查看"):
            path = MEMORY_DIR / "my_strategy.json"
            if not path.exists():
                send(chat_id,
                     "⚠️ 还没有配置策略。\n发送 `/strategy new` 启动配置向导。",
                     thread_id=thread_id)
                return
            s = json.load(open(path))
            etype_label = {"funding": "资金费率触发", "agent": "Agent 评分决策", "manual": "我手动确认"}.get(
                s.get("entry_type", ""), s.get("entry_type", "—"))
            lines = [
                "🎯 *我的交易策略*\n",
                f"• 标的：`{s.get('token', '—')}`",
                f"• 方向：`{s.get('direction', '—')}`",
                f"• 入场方式：`{etype_label}`",
                f"• 每笔仓位：`${float(s.get('size_usd', 0)):.0f}` USDC",
                f"• 止损：`-{s.get('stop_pct', '—')}%`",
                f"• 止盈：`+{s.get('profit_pct', '—')}%`",
                f"• 状态：{'✅ 启用' if s.get('enabled') else '⏸️ 暂停'}",
                f"\n配置时间：`{s.get('created_at', '')[:16]} UTC`",
                f"\n`/strategy new` — 重新配置",
                f"`/strategy off` — 暂停策略",
            ]
            send(chat_id, "\n".join(lines), thread_id=thread_id)
            return

        elif sub in ("off", "pause", "暂停"):
            path = MEMORY_DIR / "my_strategy.json"
            if path.exists():
                s = json.load(open(path))
                s["enabled"] = False
                json.dump(s, open(path, "w"), ensure_ascii=False, indent=2)
                send(chat_id, "⏸️ 策略已暂停，Agent 不再自动执行。\n`/strategy on` 可重新启用。", thread_id=thread_id)
            else:
                send(chat_id, "⚠️ 没有策略配置。", thread_id=thread_id)
            return

        elif sub in ("on", "resume", "启用"):
            path = MEMORY_DIR / "my_strategy.json"
            if path.exists():
                s = json.load(open(path))
                s["enabled"] = True
                json.dump(s, open(path, "w"), ensure_ascii=False, indent=2)
                send(chat_id, "✅ 策略已重新启用，Agent 会继续监控并执行。", thread_id=thread_id)
            else:
                send(chat_id, "⚠️ 没有策略配置，请先发送 `/strategy new`。", thread_id=thread_id)
            return

        elif sub in ("delete", "删除"):
            path = MEMORY_DIR / "my_strategy.json"
            if path.exists():
                path.unlink()
                send(chat_id, "🗑️ 策略已删除。", thread_id=thread_id)
            else:
                send(chat_id, "⚠️ 没有策略配置。", thread_id=thread_id)
            return

        else:
            send(chat_id,
                 "🎯 *策略向导指令*\n\n"
                 "`/strategy new` — 配置新策略（向导模式）\n"
                 "`/strategy show` — 查看当前策略\n"
                 "`/strategy on/off` — 启用/暂停\n"
                 "`/strategy delete` — 删除策略",
                 thread_id=thread_id)
            return

    # ── /cancel — 退出任意向导 ────────────────────────────────────────────────
    elif cmd == "cancel":
        cid_key = str(chat_id)
        if cid_key in _wizard_state:
            del _wizard_state[cid_key]
            send(chat_id, "✅ 向导已退出。", thread_id=thread_id)
        else:
            send(chat_id, "当前没有进行中的向导。", thread_id=thread_id)

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

    # ── 多交易所聚合查询 ──────────────────────────────────────────────────────
    elif cmd in ("compare", "cmp", "对比"):
        sym = args[0].upper() if args else "BTC"
        r   = skill("exchange_agg").run(action="compare", symbol=sym)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("exfunding", "xfunding", "跨所费率"):
        sym = args[0].upper() if args else "BTC"
        r   = skill("exchange_agg").run(action="funding", symbol=sym)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("vol", "volume", "成交量"):
        sym = args[0].upper() if args else "BTC"
        r   = skill("exchange_agg").run(action="volume", symbol=sym)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("divergence", "div", "价差"):
        r = skill("exchange_agg").run(action="divergence")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("listings", "listed", "上架"):
        sym = args[0].upper() if args else "BTC"
        send(chat_id, f"⏳ 正在查询 {sym} 上架情况...", thread_id=_tid(TOPIC_MARKET))
        r = skill("exchange_agg").run(action="listings", symbol=sym)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── 交易所净流量 ──────────────────────────────────────────────────────────
    elif cmd in ("netflow", "flow", "净流量"):
        sub = args[0].lower() if args else "analyze"
        if sub in ("signal", "信号"):
            sym = args[1].upper() if len(args) > 1 else "BTC"
            send(chat_id, "⏳ 正在分析综合信号...", thread_id=_tid(TOPIC_MARKET))
            r = skill("net_flow").run(action="signal", symbol=sym)
        elif sub in ("wallets", "地址"):
            r = skill("net_flow").run(action="wallets")
        else:
            # /netflow [24|12|48] [USDT|USDC]
            try:
                hours = int(sub) if sub.isdigit() else 24
            except ValueError:
                hours = 24
            token = args[1].upper() if len(args) > 1 else "USDT"
            send(chat_id, f"⏳ 正在分析过去 {hours}h 交易所净流量...", thread_id=_tid(TOPIC_MARKET))
            r = skill("net_flow").run(action="analyze", hours=hours, token=token)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    # ── Agent 智能交易 ────────────────────────────────────────────────────────
    elif cmd == "agent":
        sub = args[0].lower() if args else "status"
        if sub in ("scan", "analyze", "分析"):
            send(chat_id, "🤖 Agent 正在分析市场...", thread_id=_tid(TOPIC_TRADE))
            r = skill("agent_trade").run(action="analyze")
        elif sub in ("status", "状态"):
            r = skill("agent_trade").run(action="status")
        elif sub in ("history", "历史"):
            r = skill("agent_trade").run(action="history")
        elif sub in ("decide", "决策"):
            send(chat_id, "🤖 Agent 正在生成决策...", thread_id=_tid(TOPIC_TRADE))
            r = skill("agent_trade").run(action="decide")
        else:
            r = skill("agent_trade").run(action="status")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))

    # ── 数据上下文整理（供群组内 AI Agent 分析）─────────────────────────────
    elif cmd in ("ask", "分析"):
        question = " ".join(args)
        if not question:
            send(chat_id, "❓ 请提供问题，例如：`/ask 现在 SOL 适合做多吗？`", thread_id=_tid(TOPIC_MARKET))
            return
        r = skill("ai_agent").run(action="ask", question=question)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("deep", "深度"):
        sym = args[0].upper() if args else "BTC"
        send(chat_id, f"⏳ 正在整理 {sym} 深度数据...", thread_id=_tid(TOPIC_MARKET))
        r = skill("ai_agent").run(action="deep", symbol=sym)
        send(chat_id, r["text"], thread_id=_tid(TOPIC_MARKET))

    elif cmd in ("advice", "建议"):
        r = skill("ai_agent").run(action="advice")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_TRADE))

    # ── 链上监控 ─────────────────────────────────────────────────────────────
    elif cmd in ("watch", "链上", "onchain"):
        sub = args[0].lower() if args else "list"

        if sub == "add":
            # /watch add ETH 0x... [标签] [阈值]
            chain   = args[1].upper() if len(args) > 1 else "ETH"
            address = args[2] if len(args) > 2 else None
            label   = args[3] if len(args) > 3 else None
            try:
                threshold = float(args[4]) if len(args) > 4 else None
            except ValueError:
                threshold = None
            r = skill("onchain").run(action="add", chain=chain, address=address,
                                     label=label, alert_threshold=threshold)

        elif sub == "remove":
            chain   = args[1].upper() if len(args) > 1 else "ETH"
            address = args[2] if len(args) > 2 else None
            r = skill("onchain").run(action="remove", chain=chain, address=address)

        elif sub == "list":
            r = skill("onchain").run(action="list")

        elif sub == "scan":
            send(chat_id, "⏳ 扫描链上活动...", thread_id=_tid(TOPIC_ALERT))
            r = skill("onchain").run(action="scan")

        elif sub in ("chains", "overview"):
            r = skill("onchain").run(action="chains")

        elif sub.upper() in ("ETH", "BNB", "SOL"):
            # /watch ETH 0x... 直接查询
            chain   = sub.upper()
            address = args[1] if len(args) > 1 else None
            r = skill("onchain").run(action="recent", chain=chain, address=address)

        else:
            # /watch <address> — 尝试猜测链（SOL 地址较短无0x前缀）
            address = sub
            chain   = "SOL" if not address.startswith("0x") else "ETH"
            r = skill("onchain").run(action="recent", chain=chain, address=address)

        send(chat_id, r["text"], thread_id=_tid(TOPIC_ALERT))

    elif cmd == "chains":
        r = skill("onchain").run(action="chains")
        send(chat_id, r["text"], thread_id=_tid(TOPIC_ALERT))

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
        ws_age  = _bs.data_age_minutes("ws_prices.json") * 60  # 转秒

        if mkt_age < 6:
            data_status = f"✅ 正常（{mkt_age:.0f} 分钟前）"
        elif mkt_age < 30:
            data_status = f"⚠️ 偏旧（{mkt_age:.0f} 分钟前）"
        else:
            data_status = f"❌ 过期（{mkt_age:.0f} 分钟前，调度器可能未运行）"

        ws_status = f"⚡ 在线（{ws_age:.0f}s 前）" if ws_age <= 10 else "⏸️ 未运行（可选）"

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
            f"WebSocket 推送：{ws_status}",
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
        {"command": "ta",       "description": "技术分析，/ta BTC 4h signal"},
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
        {"command": "compare",   "description": "跨所价格对比 — /compare BTC"},
        {"command": "exfunding", "description": "跨所资金费率对比 — /exfunding BTC"},
        {"command": "vol",       "description": "跨所成交量对比 — /vol BTC"},
        {"command": "divergence","description": "跨所价差扫描"},
        {"command": "listings",  "description": "查询代币上架情况（现货+合约）— /listings SOL"},
        {"command": "agent",     "description": "Agent 分析 — /agent scan | status | history"},
        {"command": "netflow",   "description": "交易所净流量 — /netflow [24h] | signal BTC | wallets"},
        {"command": "track",     "description": "专项追踪 — /track SOL [15min] | report | cancel"},
        {"command": "mm",        "description": "做市商阶段分析 — /mm SOL | /mm scan"},
        {"command": "strategy",  "description": "策略向导 — /strategy new | show | on | off"},
        {"command": "ask",    "description": "整理市场数据上下文 — /ask 现在 SOL 适合做多吗？"},
        {"command": "deep",   "description": "整理币种深度数据 — /deep BTC（MM阶段+跨所费率）"},
        {"command": "advice", "description": "整理持仓上下文 — 供 AI Agent 给出操作建议"},
        {"command": "watch",     "description": "链上监控 — /watch add ETH 0x... | list | remove"},
        {"command": "chains",    "description": "链上监控概览"},
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
