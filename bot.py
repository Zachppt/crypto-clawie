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
from datetime import datetime, timezone
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

env = {k: os.getenv(k, "") for k in [
    "HL_PRIVATE_KEY", "HL_WALLET_ADDRESS", "HL_USE_TESTNET",
    "HL_DEFAULT_LEVERAGE", "HL_DEFAULT_MARGIN_MODE",
    "HL_FUNDING_ALERT_THRESHOLD", "HL_LIQ_ALERT_THRESHOLD",
    "BLOCKBEATS_API_KEY", "AUTONOMOUS_MODE", "MAX_POSITION_SIZE_USD",
]}

# ── Telegram 工具 ─────────────────────────────────────────────────────────────

def send(chat_id: int, text: str, parse_mode: str = "Markdown"):
    if len(text) > 4000:
        text = text[:4000] + "\n...(内容过长，已截断)"
    try:
        requests.post(f"{API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }, timeout=10)
    except Exception as e:
        log.warning(f"send failed: {e}")

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

def skill(name: str):
    module_path, class_name = _skill_map[name]
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)(DATA_DIR, MEMORY_DIR, env)

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

# ── 帮助文本 ──────────────────────────────────────────────────────────────────

HELP_TEXT = """🤖 *Clawie 指令列表*

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
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    if not text.startswith("/"):
        return

    parts  = text.split()
    cmd    = parts[0].lstrip("/").split("@")[0].lower()
    args   = parts[1:]

    log.info(f"cmd=/{cmd} args={args} chat={chat_id}")

    try:
        _route(chat_id, cmd, args)
    except Exception as e:
        log.error(f"route error: {e}", exc_info=True)
        send(chat_id, f"❌ 执行出错：{e}")


def _route(chat_id: int, cmd: str, args: list):

    # ── 账户 ────────────────────────────────────────────────────────────────
    if cmd in ("position", "positions", "pos", "账户", "持仓"):
        r = skill("hl_monitor").run(action="account")
        send(chat_id, r["text"])

    elif cmd in ("liq", "liquidation", "risk", "爆仓"):
        r = skill("hl_monitor").run(action="liquidation")
        send(chat_id, r["text"])

    # ── HL 市场 ──────────────────────────────────────────────────────────────
    elif cmd == "market":
        r = skill("hl_monitor").run(action="overview")
        send(chat_id, r["text"])

    elif cmd == "funding":
        symbol = args[0].upper() if args else None
        r = skill("hl_monitor").run(action="funding", symbol=symbol)
        send(chat_id, r["text"])

    elif cmd == "oi":
        symbol = args[0].upper() if args else None
        r = skill("hl_monitor").run(action="oi", symbol=symbol)
        send(chat_id, r["text"])

    # ── 行情 ─────────────────────────────────────────────────────────────────
    elif cmd == "price":
        symbol = args[0].upper() if args else "BTC"
        r = skill("crypto_data").run(action="price", symbol=symbol)
        send(chat_id, r["text"])

    elif cmd in ("fng", "fear", "greed", "情绪"):
        r = skill("crypto_data").run(action="fng")
        send(chat_id, r["text"])

    # ── 快讯 ─────────────────────────────────────────────────────────────────
    elif cmd == "news":
        r = skill("crypto_news").run(action="latest")
        send(chat_id, r["text"])

    elif cmd == "hlnews":
        r = skill("crypto_news").run(action="hl")
        send(chat_id, r["text"])

    # ── 信号 ─────────────────────────────────────────────────────────────────
    elif cmd in ("alerts", "alert", "signals", "信号"):
        r = skill("crypto_alert").run(action="scan")
        send(chat_id, r["text"])

    # ── 报告 ─────────────────────────────────────────────────────────────────
    elif cmd in ("report", "日报"):
        r = skill("crypto_report").run(period="daily")
        send(chat_id, r["text"])

    elif cmd in ("weekly", "周报"):
        r = skill("crypto_report").run(period="weekly")
        send(chat_id, r["text"])

    # ── 套利策略 ─────────────────────────────────────────────────────────────
    elif cmd in ("arb",):
        sub = args[0].lower() if args else "scan"
        sym = args[1].upper() if len(args) > 1 else None
        try:
            size = float(args[2]) if len(args) > 2 else 100.0
        except ValueError:
            send(chat_id, f"❌ 金额格式错误：`{args[2]}`，请输入数字，例如：`/arb open BTC 500`")
            return
        r = skill("funding_arb").run(action=sub, symbol=sym, size_usd=size)
        send(chat_id, r["text"])

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
        send(chat_id, r["text"])

    # ── 回测 ─────────────────────────────────────────────────────────────────
    elif cmd in ("backtest", "bt"):
        send(chat_id, "⏳ 正在运行回测（合成数据）...")
        try:
            from backtest.engine import BacktestEngine, FundingArbStrategy
            engine = BacktestEngine()
            engine.load_sample_data(n_periods=300)
            result = engine.run(FundingArbStrategy(entry_threshold=0.0005))
            send(chat_id, result.summary())
        except Exception as e:
            send(chat_id, f"❌ 回测失败：{e}")

    # ── 交易指令 ─────────────────────────────────────────────────────────────
    elif cmd == "trade":
        sub = args[0].lower() if args else "positions"
        if sub in ("open", "做多", "做空"):
            # /trade open ETH long 100 [leverage]
            sym     = args[1].upper() if len(args) > 1 else "ETH"
            side    = args[2].lower() if len(args) > 2 else "long"
            try:
                size_usd = float(args[3]) if len(args) > 3 else 100.0
            except ValueError:
                send(chat_id, "❌ 格式：`/trade open <币种> <long|short> <金额USD>`")
                return
            lev = int(args[4]) if len(args) > 4 else None
            r = skill("hl_trade").run(action="open", symbol=sym, side=side,
                                      size_usd=size_usd, leverage=lev)
        elif sub in ("close", "平仓"):
            sym = args[1].upper() if len(args) > 1 else None
            if not sym:
                send(chat_id, "❌ 格式：`/trade close <币种>`")
                return
            r = skill("hl_trade").run(action="close", symbol=sym)
        elif sub in ("cancel", "撤单"):
            sym = args[1].upper() if len(args) > 1 else None
            oid = int(args[2]) if len(args) > 2 else None
            if not sym or not oid:
                send(chat_id, "❌ 格式：`/trade cancel <币种> <order_id>`")
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
        send(chat_id, r["text"])

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
             f"此期间新开仓不受每日亏损限制，请谨慎操作。")

    # ── 快捷查询任意交易对 ────────────────────────────────────────────────────
    elif cmd.upper() in known_symbols():
        r = skill("hl_monitor").run(action="funding", symbol=cmd.upper())
        send(chat_id, r["text"])

    # ── 帮助 ─────────────────────────────────────────────────────────────────
    elif cmd in ("start", "help", "帮助"):
        send(chat_id, HELP_TEXT)

    else:
        send(chat_id, f"未知指令 `/{cmd}`，发送 /help 查看所有命令")

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
                "allowed_updates": ["message"],
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
