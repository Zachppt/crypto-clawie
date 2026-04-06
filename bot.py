"""
bot.py — Telegram 指令机器人
直接响应用户命令，实时调用 skill 返回数据，不经过 LLM。

命令：
  /market            — 市场概览 + 资金费率 Top 5
  /account           — 我的持仓和余额
  /funding [symbol]  — 资金费率排行，或指定币种
  /oi [symbol]       — 未平仓量
  /liq               — 爆仓风险
  /news              — 最新快讯
  /alerts            — 当前异动信号
  /report            — 今日报告
  /BTC /ETH /SOL 等  — 快速查询该币种价格 + 资金费率
"""

import os
import sys
import json
import time
import logging
from pathlib import Path

import requests
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
    "BLOCKBEATS_API_KEY", "AUTONOMOUS_MODE", "MAX_POSITION_SIZE_USD",
]}

# ── Telegram 工具 ─────────────────────────────────────────────────────────────

def send(chat_id: int, text: str, parse_mode: str = "Markdown"):
    """发送消息，超长自动截断。"""
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

# ── Skill 加载 ────────────────────────────────────────────────────────────────

def skill(name: str):
    """动态加载并实例化 skill。"""
    mapping = {
        "hl_monitor":   ("skills.hl_monitor",   "HLMonitorSkill"),
        "hl_trade":     ("skills.hl_trade",      "HLTradeSkill"),
        "crypto_data":  ("skills.crypto_data",   "CryptoDataSkill"),
        "crypto_news":  ("skills.crypto_news",   "CryptoNewsSkill"),
        "crypto_alert": ("skills.crypto_alert",  "CryptoAlertSkill"),
        "crypto_report":("skills.crypto_report", "CryptoReportSkill"),
    }
    module_path, class_name = mapping[name]
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls(DATA_DIR, MEMORY_DIR, env)

# ── 已知交易对缓存 ────────────────────────────────────────────────────────────

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

# ── 命令处理 ──────────────────────────────────────────────────────────────────

def handle(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    if not text.startswith("/"):
        return

    parts = text.split()
    cmd   = parts[0].lstrip("/").split("@")[0].lower()
    args  = parts[1:]

    log.info(f"cmd=/{cmd} args={args} chat={chat_id}")

    try:
        _dispatch(chat_id, cmd, args)
    except Exception as e:
        log.error(f"handle error: {e}", exc_info=True)
        send(chat_id, f"❌ 执行出错：{e}")


def _dispatch(chat_id: int, cmd: str, args: list):
    # /market — 市场概览
    if cmd == "market":
        r = skill("hl_monitor").run(action="overview")
        send(chat_id, r["text"])

    # /account /positions /pos — 持仓和余额
    elif cmd in ("account", "positions", "pos"):
        r = skill("hl_monitor").run(action="account")
        send(chat_id, r["text"])

    # /funding [symbol] — 资金费率
    elif cmd == "funding":
        symbol = args[0].upper() if args else None
        r = skill("hl_monitor").run(action="funding", symbol=symbol)
        send(chat_id, r["text"])

    # /oi [symbol] — 未平仓量
    elif cmd == "oi":
        symbol = args[0].upper() if args else None
        r = skill("hl_monitor").run(action="oi", symbol=symbol)
        send(chat_id, r["text"])

    # /liq — 爆仓风险
    elif cmd in ("liq", "liquidation", "risk"):
        r = skill("hl_monitor").run(action="liquidation")
        send(chat_id, r["text"])

    # /news — 最新快讯
    elif cmd == "news":
        r = skill("crypto_news").run()
        send(chat_id, r["text"])

    # /alerts — 异动信号
    elif cmd in ("alerts", "alert", "signals"):
        r = skill("crypto_alert").run(action="scan")
        send(chat_id, r["text"])

    # /report — 今日报告
    elif cmd == "report":
        r = skill("crypto_report").run(period="daily")
        send(chat_id, r["text"])

    # /BTC /ETH /SOL 等 — 快速查询
    elif cmd.upper() in known_symbols():
        r = skill("hl_monitor").run(action="funding", symbol=cmd.upper())
        send(chat_id, r["text"])

    # /start /help
    elif cmd in ("start", "help"):
        send(chat_id, (
            "🤖 *Clawie 指令列表*\n\n"
            "/market — 市场概览 + 资金费率\n"
            "/account — 持仓和余额\n"
            "/funding \\[symbol\\] — 资金费率排行\n"
            "/oi \\[symbol\\] — 未平仓量\n"
            "/liq — 爆仓风险\n"
            "/news — 最新快讯\n"
            "/alerts — 异动信号\n"
            "/report — 今日报告\n\n"
            "快捷查询：/BTC /ETH /SOL 等"
        ))

# ── 主循环 ────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

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
