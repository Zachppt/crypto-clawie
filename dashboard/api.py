"""
dashboard/api.py — Clawie Dashboard Backend
FastAPI server that reads local JSON cache files (zero extra API calls).

Start:
  uvicorn dashboard.api:app --host 0.0.0.0 --port 8080

Or via pm2:
  pm2 start "venv/bin/uvicorn dashboard.api:app --host 0.0.0.0 --port 8080" \
    --name clawie-dashboard
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# ── Paths ─────────────────────────────────────────────────────────────────

ROOT   = Path(__file__).parent.parent
DATA   = ROOT / "data"
MEMORY = ROOT / "memory"
HERE   = Path(__file__).parent

sys.path.insert(0, str(ROOT))

# Load .env so os.getenv() works (e.g. MAX_DAILY_LOSS_PCT)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Clawie Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])


# ── Helpers ───────────────────────────────────────────────────────────────

def _load(path: Path, default=None):
    """
    Load JSON from path, auto-unwrapping fetcher's envelope:
      {"_updated": "...", "data": <actual-data>}
    """
    if not path.exists():
        return default
    try:
        raw = json.loads(path.read_text())
        if isinstance(raw, dict) and "data" in raw:
            return raw["data"]
        return raw
    except Exception:
        return default


def _data_age_seconds(path: Path) -> float:
    """Seconds since the file was last written by the fetcher."""
    if not path.exists():
        return float("inf")
    try:
        raw = json.loads(path.read_text())
        ts  = raw.get("_updated") if isinstance(raw, dict) else None
        if ts:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return float("inf")


def _env() -> dict:
    """Return current .env as a dict (for skill imports)."""
    try:
        from dotenv import dotenv_values
        return dict(dotenv_values(ROOT / ".env"))
    except Exception:
        return {}


# ── Pages ─────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


# ── API: Summary ──────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary():
    account = _load(DATA / "hl_account.json") or {}
    market  = _load(DATA / "hl_market.json")  or {}
    snap    = _load(DATA / "market_snapshot.json") or {}
    history = _load(MEMORY / "trade_history.json") or []

    positions    = account.get("positions", [])
    balance      = account.get("account_value_usdc", 0)
    margin_used  = account.get("margin_used_usdc", 0)
    margin_ratio = account.get("margin_ratio", 0)
    unrealized   = sum(p.get("unrealized_pnl", 0) for p in positions)

    # Daily realized PnL (UTC day boundary)
    today = datetime.now(timezone.utc).date().isoformat()
    daily_pnl = sum(
        t.get("realized_pnl", 0) for t in history
        if isinstance(t, dict) and t.get("timestamp", "")[:10] == today
    )

    # Circuit breaker status
    max_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "5"))
    circuit_ok   = not (balance > 0 and daily_pnl < 0 and
                        abs(daily_pnl) / balance * 100 >= max_loss_pct)

    # Data freshness (use market file as proxy)
    age_s = _data_age_seconds(DATA / "hl_market.json")

    return {
        "balance":           round(balance, 2),
        "margin_used":       round(margin_used, 2),
        "margin_ratio":      round(margin_ratio, 2),
        "unrealized_pnl":    round(unrealized, 2),
        "daily_pnl":         round(daily_pnl, 2),
        "position_count":    len(positions),
        "liq_alert_count":   len(account.get("liq_alerts", [])),
        "circuit_ok":        circuit_ok,
        "circuit_threshold": max_loss_pct,
        "fng":               snap.get("fear_greed", {}),
        "data_age_s":        round(age_s) if age_s != float("inf") else -1,
    }


# ── API: Funding Rates ────────────────────────────────────────────────────

@app.get("/api/funding")
def api_funding():
    market = _load(DATA / "hl_market.json") or {}
    assets = market.get("assets", [])

    top30 = sorted(assets, key=lambda x: abs(x.get("funding_8h", 0)), reverse=True)[:30]

    return {
        "total":  len(assets),
        "assets": [
            {
                "symbol":     a["symbol"],
                "rate":       round(a.get("funding_8h", 0) * 100, 4),      # in %
                "annualized": round(a.get("funding_annualized", 0), 1),
                "price":      a.get("mark_price", 0),
                "oi_usd_m":   round(a.get("open_interest", 0) * a.get("mark_price", 0) / 1e6, 1),
                "change_24h": round(a.get("change_24h_pct", 0), 2),
            }
            for a in top30
        ],
    }


# ── API: Positions ────────────────────────────────────────────────────────

@app.get("/api/positions")
def api_positions():
    account    = _load(DATA / "hl_account.json") or {}
    market     = _load(DATA / "hl_market.json")  or {}
    assets_map = {a["symbol"]: a for a in market.get("assets", [])}

    enriched = []
    for p in account.get("positions", []):
        sym   = p.get("symbol", "")
        asset = assets_map.get(sym, {})
        enriched.append({
            **p,
            "mark_price":   asset.get("mark_price", p.get("entry_price", 0)),
            "funding_rate": round(asset.get("funding_8h", 0) * 100, 4),
        })

    return {
        "positions":  enriched,
        "liq_alerts": account.get("liq_alerts", []),
    }


# ── API: Signals ──────────────────────────────────────────────────────────

@app.get("/api/signals")
def api_signals():
    try:
        from skills.crypto_alert import CryptoAlertSkill
        skill  = CryptoAlertSkill(DATA, MEMORY, _env())
        result = skill.run(action="scan")
        signals = result.get("data", {}).get("signals", [])
    except Exception as e:
        signals = []
    return {"signals": signals}


# ── API: Arb Positions ────────────────────────────────────────────────────

@app.get("/api/arb")
def api_arb():
    positions = _load(MEMORY / "arb_positions.json") or {}
    market    = _load(DATA / "hl_market.json") or {}
    assets    = {a["symbol"]: a for a in market.get("assets", [])}
    now_ts    = datetime.now(timezone.utc).timestamp()

    result = []
    for sym, pos in positions.items():
        asset      = assets.get(sym, {})
        cur_rate   = asset.get("funding_8h", 0)
        entry_rate = pos.get("entry_funding", 0)
        hours_held = (now_ts - pos.get("opened_at", now_ts)) / 3600
        est_income = abs(entry_rate) * pos.get("size_usd", 0) * (hours_held / 8)

        result.append({
            "symbol":      sym,
            "side":        pos.get("side", "short"),
            "size_usd":    pos.get("size_usd", 0),
            "entry_rate":  round(entry_rate * 100, 4),
            "cur_rate":    round(cur_rate * 100, 4),
            "hours_held":  round(hours_held, 1),
            "est_income":  round(est_income, 2),
            "exit_signal": abs(cur_rate) <= 0.0001,
        })

    return {
        "positions":       result,
        "total_est_income": round(sum(r["est_income"] for r in result), 2),
    }


# ── API: PnL History ─────────────────────────────────────────────────────

@app.get("/api/pnl_history")
def api_pnl_history():
    history = _load(MEMORY / "trade_history.json") or []

    daily: dict = {}
    for t in history:
        if not isinstance(t, dict):
            continue
        day = t.get("timestamp", "")[:10]
        if not day:
            continue
        daily[day] = daily.get(day, 0) + t.get("realized_pnl", 0)

    today = datetime.now(timezone.utc).date()
    days  = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]

    cumulative = 0.0
    points = []
    for d in days:
        pnl = daily.get(d, 0)
        cumulative += pnl
        points.append({
            "date":       d,
            "daily":      round(pnl, 2),
            "cumulative": round(cumulative, 2),
        })

    return {"points": points}


# ── API: News ─────────────────────────────────────────────────────────────

@app.get("/api/news")
def api_news():
    items = _load(DATA / "news_cache.json") or []
    return {"items": items[:8] if isinstance(items, list) else []}


# ── API: Grid ─────────────────────────────────────────────────────────────

@app.get("/api/grid")
def api_grid():
    grids   = _load(MEMORY / "grid_positions.json") or {}
    market  = _load(DATA / "hl_market.json") or {}
    assets  = {a["symbol"]: a for a in market.get("assets", [])}

    result = []
    for gid, g in grids.items():
        sym    = g.get("symbol", "")
        cur_px = assets.get(sym, {}).get("mark_price", g.get("current_price", 0))
        result.append({
            "grid_id":       gid,
            "symbol":        sym,
            "price_low":     g.get("price_low", 0),
            "price_high":    g.get("price_high", 0),
            "grid_count":    g.get("grid_count", 0),
            "total_capital": g.get("total_capital", 0),
            "current_price": round(cur_px, 2),
            "in_range":      g.get("price_low", 0) <= cur_px <= g.get("price_high", 0),
            "created_at":    g.get("created_at", "")[:10],
        })

    return {"grids": result}
