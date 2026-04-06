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

*快捷查询*
/BTC /ETH /SOL 等任意交易对"""

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
    commands = [
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
        {"command": "help",     "description": "查看所有指令"},
    ]
    try:
        r = requests.post(f"{API_URL}/setMyCommands", json={"commands": commands}, timeout=10)
        if r.json().get("ok"):
            log.info(f"Telegram commands registered ({len(commands)} commands)")
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
