"""
skills/exchange_agg — 多交易所聚合行情与资金费率
数据来源：
  - Binance / OKX / Bybit / Gate.io / Bitget — ccxt 统一接口（无需 API Key）
  - Hyperliquid — 公开 REST API 实时获取（无需缓存文件）
"""
from __future__ import annotations

import time
import requests as _req
from concurrent.futures import ThreadPoolExecutor, as_completed
from skills.base import BaseSkill

SPOT_EXCHANGES = ["Binance", "OKX", "Bybit", "Gate.io", "Bitget"]
PERP_EXCHANGES = ["Binance", "OKX", "Bybit", "Gate.io", "Bitget", "Hyperliquid"]

_CCXT_NAME = {
    "Binance": "binance",
    "OKX":     "okx",
    "Bybit":   "bybit",
    "Gate.io": "gateio",
    "Bitget":  "bitget",
}

_PERP_TYPE = {
    "binance": "future",
    "okx":     "swap",
    "bybit":   "linear",
    "gateio":  "swap",
    "bitget":  "swap",
}

# ── Hyperliquid 实时缓存（进程内 TTL 10s，同一命令内只请求一次）──────────────

_hl_cache: dict[str, dict] = {}   # {SYMBOL: {"price": float, "funding": float}}
_hl_cache_ts: float = 0.0

def _hl_refresh() -> None:
    global _hl_cache, _hl_cache_ts
    if time.time() - _hl_cache_ts < 10:
        return
    try:
        r = _req.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "metaAndAssetCtxs"},
            timeout=7,
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        result = r.json()
        if not result or len(result) < 2:
            return
        universe, ctxs = result[0].get("universe", []), result[1]
        cache: dict[str, dict] = {}
        for i, asset in enumerate(universe):
            if i >= len(ctxs):
                break
            ctx  = ctxs[i]
            mark = float(ctx.get("markPx") or ctx.get("midPx") or 0)
            cache[asset["name"]] = {
                "price":   mark,
                "funding": float(ctx.get("funding", 0)),
            }
        _hl_cache    = cache
        _hl_cache_ts = time.time()
    except Exception:
        pass


def _hl_price(symbol: str, _=None) -> float | None:
    """取 HL 实时标记价格（第二参数兼容旧调用，忽略）。"""
    _hl_refresh()
    d = _hl_cache.get(symbol.upper())
    p = d["price"] if d else None
    return p if p else None


def _hl_funding(symbol: str, _=None) -> float | None:
    """取 HL 实时资金费率（第二参数兼容旧调用，忽略）。"""
    _hl_refresh()
    d = _hl_cache.get(symbol.upper())
    return d["funding"] if d is not None else None


# ── ccxt 工厂 ──────────────────────────────────────────────────────────────────

def _spot_ex(name: str):
    import ccxt
    return getattr(ccxt, name)({"options": {"defaultType": "spot"}, "timeout": 8000})


def _perp_ex(name: str):
    import ccxt
    return getattr(ccxt, name)({"options": {"defaultType": _PERP_TYPE[name]}, "timeout": 8000})


# ── 单交易所取数函数（返回 (label, value | None)）────────────────────────────

def _fetch_spot_price(label: str, symbol: str) -> tuple[str, float | None]:
    try:
        ticker = _spot_ex(_CCXT_NAME[label]).fetch_ticker(f"{symbol}/USDT")
        v = ticker.get("last")
        return label, float(v) if v else None
    except Exception:
        return label, None


def _fetch_perp_funding(label: str, symbol: str) -> tuple[str, float | None]:
    try:
        name = _CCXT_NAME[label]
        fr   = _perp_ex(name).fetch_funding_rate(f"{symbol}/USDT:USDT")
        v    = fr.get("fundingRate")
        return label, float(v) if v is not None else None
    except Exception:
        return label, None


def _fetch_spot_volume(label: str, symbol: str) -> tuple[str, float | None]:
    try:
        ticker = _spot_ex(_CCXT_NAME[label]).fetch_ticker(f"{symbol}/USDT")
        v = ticker.get("quoteVolume")
        return label, float(v) if v else None
    except Exception:
        return label, None


def _fetch_perp_volume(label: str, symbol: str) -> tuple[str, float | None]:
    try:
        name   = _CCXT_NAME[label]
        ticker = _perp_ex(name).fetch_ticker(f"{symbol}/USDT:USDT")
        v = ticker.get("quoteVolume")
        return label, float(v) if v else None
    except Exception:
        return label, None


# ── 带键值的并行辅助（供跨类型并行使用）─────────────────────────────────────

def _keyed_spot_price(label: str, symbol: str) -> tuple[str, float | None]:
    _, v = _fetch_spot_price(label, symbol)
    return f"p:{label}", v


def _keyed_spot_volume(label: str, symbol: str) -> tuple[str, float | None]:
    _, v = _fetch_spot_volume(label, symbol)
    return f"v:{label}", v


# ── 并行执行 ───────────────────────────────────────────────────────────────────

def _parallel(tasks: list, workers: int = 10, timeout: float = 15) -> dict:
    """
    tasks: [(callable, arg1, arg2, ...), ...]
    callable 返回 (key, value)。
    返回 {key: value}（忽略异常任务）。
    """
    results: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fn, *args): None for fn, *args in tasks}
        for fut in as_completed(futs, timeout=timeout):
            try:
                k, v = fut.result()
                results[k] = v
            except Exception:
                pass
    return results


# ── 上架情况行格式化 ───────────────────────────────────────────────────────────

def _listing_line(label: str, found: dict[str, bool], all_exchanges: list[str]) -> str:
    parts  = [("✅" if found.get(ex) else "❌") + ex for ex in all_exchanges]
    listed = sum(1 for v in found.values() if v)
    return f"_{label}：{' | '.join(parts)}（{listed}/{len(all_exchanges)}）_"


# ── Skill ──────────────────────────────────────────────────────────────────────

class ExchangeAggSkill(BaseSkill):

    def run(self, action: str = "compare", symbol: str = "BTC", **kwargs) -> dict:
        symbol = symbol.upper()
        dispatch = {
            "compare":    self._compare,
            "funding":    self._funding_compare,
            "divergence": self._divergence,
            "volume":     self._volume,
            "listings":   self._listings,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：compare / funding / divergence / volume / listings")
        return fn(symbol=symbol, **kwargs)

    # ── 跨所价格对比（含成交量）──────────────────────────────────────────────

    def _compare(self, symbol: str = "BTC", **_) -> dict:
        # 并行：现货价格 + 现货成交量（10个请求，约3-4s）
        tasks = (
            [(_keyed_spot_price,  label, symbol) for label in SPOT_EXCHANGES] +
            [(_keyed_spot_volume, label, symbol) for label in SPOT_EXCHANGES]
        )
        results = _parallel(tasks, workers=10)

        spot_prices = {label: results.get(f"p:{label}") for label in SPOT_EXCHANGES}
        spot_vols   = {label: results.get(f"v:{label}") for label in SPOT_EXCHANGES}

        hl_px      = _hl_price(symbol)
        all_prices = {k: v for k, v in spot_prices.items() if v is not None}
        if hl_px:
            all_prices["Hyperliquid"] = hl_px
        spot_ok = {ex: (spot_prices.get(ex) is not None) for ex in SPOT_EXCHANGES}

        if not all_prices:
            return self.err(f"所有交易所均无法获取 {symbol} 价格")

        avg   = sum(all_prices.values()) / len(all_prices)
        max_p = max(all_prices.values())
        min_p = min(all_prices.values())

        lines = [f"📊 *{symbol} 跨所对比*\n"]

        # ── 价格 ──────────────────────────────────────────────────────────────
        lines.append("*价格*")
        for ex, price in sorted(all_prices.items(), key=lambda x: x[1], reverse=True):
            diff_pct = (price - avg) / avg * 100
            sign     = "+" if diff_pct >= 0 else ""
            marker   = " ⬆️" if price == max_p and len(all_prices) > 1 else (
                       " ⬇️" if price == min_p and len(all_prices) > 1 else "")
            lines.append(f"• *{ex}*：`${price:,.2f}` ({sign}{diff_pct:.3f}%){marker}")

        spread     = (max_p - min_p) / avg * 100
        high_ex    = max(all_prices, key=all_prices.get)
        low_ex     = min(all_prices, key=all_prices.get)
        lines.append(f"\n价差：`{spread:.3f}%` [{high_ex} vs {low_ex}]")
        if spread >= 0.1:
            lines.append("⚡ _价差较大，可能存在套利机会_")

        # ── 成交量 ────────────────────────────────────────────────────────────
        valid_vols = {k: v for k, v in spot_vols.items() if v}
        if valid_vols:
            total_vol = sum(valid_vols.values())
            lines.append("\n*24h 现货成交量*")
            for ex, vol in sorted(valid_vols.items(), key=lambda x: x[1], reverse=True):
                share = vol / total_vol * 100 if total_vol else 0
                lines.append(f"• *{ex}*：`${vol/1e9:.2f}B` ({share:.1f}%)")
            lines.append(f"合计：`${total_vol/1e9:.2f}B`")

        # ── 上架状态 ──────────────────────────────────────────────────────────
        lines.append("")
        lines.append(_listing_line("现货上架", spot_ok, SPOT_EXCHANGES))
        lines.append(f"_合约 Hyperliquid：{'✅ 有永续合约' if hl_px else '❌ 无永续合约'}_")

        return self.ok("\n".join(lines), data={
            "prices":      all_prices,
            "volumes":     valid_vols,
            "avg":         avg,
            "spot_listed": [ex for ex, ok in spot_ok.items() if ok],
            "hl_listed":   hl_px is not None,
        })

    # ── 跨所资金费率对比 ──────────────────────────────────────────────────────

    def _funding_compare(self, symbol: str = "BTC", **_) -> dict:
        tasks = [(_fetch_perp_funding, label, symbol) for label in SPOT_EXCHANGES]
        rates = _parallel(tasks)

        hl_rate = _hl_funding(symbol)
        if hl_rate is not None:
            rates["Hyperliquid"] = hl_rate

        all_ok = {ex: (rates.get(ex) is not None) for ex in PERP_EXCHANGES}

        if not rates:
            return self.err(f"获取 {symbol} 资金费率失败，该币种可能无合约或网络异常")

        lines = [f"💹 *{symbol} 跨所资金费率对比*\n"]
        for ex, rate in sorted(rates.items(), key=lambda x: abs(x[1]), reverse=True):
            ann       = rate * 3 * 365 * 100
            direction = "多付空 📈" if rate > 0 else "空付多 📉"
            emoji     = "🔴" if abs(rate) >= 0.001 else "🟡" if abs(rate) >= 0.0005 else "🟢"
            lines.append(
                f"{emoji} *{ex}*：`{rate*100:+.4f}%/8h` ({direction}) | 年化 `{ann:+.1f}%`"
            )

        if len(rates) >= 2:
            max_ex = max(rates, key=rates.get)
            min_ex = min(rates, key=rates.get)
            spread = rates[max_ex] - rates[min_ex]
            if spread >= 0.0003:
                ann_arb = spread * 3 * 365 * 100
                lines.append(
                    f"\n💡 *跨所套利机会*\n"
                    f"费率差：`{spread*100:+.4f}%/8h`（年化约 `{ann_arb:.0f}%`）\n"
                    f"做空 {max_ex}（高费率）+ 做多 {min_ex}（低费率）\n"
                    f"⚠️ 需评估对冲成本和手续费"
                )

        lines.append("")
        lines.append(_listing_line("合约上架", all_ok, PERP_EXCHANGES))

        return self.ok("\n".join(lines), data={
            "rates":       rates,
            "perp_listed": [ex for ex, ok in all_ok.items() if ok],
        })

    # ── 价差异动扫描 ──────────────────────────────────────────────────────────

    def _divergence(self, symbol: str = "BTC", threshold_pct: float = 0.15, **_) -> dict:
        scan_symbols   = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "ARB"]
        scan_exchanges = ["Binance", "OKX", "Bitget"]

        # HL 实时价格（一次批量刷新）
        _hl_refresh()
        hl_map = {sym: d["price"] for sym, d in _hl_cache.items() if d["price"]}

        def _keyed(sym: str, label: str) -> tuple[tuple, float | None]:
            _, v = _fetch_spot_price(label, sym)
            return (sym, label), v

        sym_prices: dict[str, dict[str, float]] = {s: {} for s in scan_symbols}
        with ThreadPoolExecutor(max_workers=12) as pool:
            futs = {
                pool.submit(_keyed, sym, label): None
                for sym in scan_symbols
                for label in scan_exchanges
            }
            for fut in as_completed(futs, timeout=15):
                try:
                    (sym, label), v = fut.result()
                    if v is not None:
                        sym_prices[sym][label] = v
                except Exception:
                    pass

        for sym in scan_symbols:
            if sym in hl_map:
                sym_prices[sym]["HL"] = hl_map[sym]

        divergences = []
        for sym, prices in sym_prices.items():
            if len(prices) < 2:
                continue
            avg        = sum(prices.values()) / len(prices)
            spread_pct = (max(prices.values()) - min(prices.values())) / avg * 100
            if spread_pct >= threshold_pct:
                max_ex = max(prices, key=prices.get)
                min_ex = min(prices, key=prices.get)
                divergences.append({
                    "symbol":     sym,
                    "spread_pct": round(spread_pct, 4),
                    "prices":     prices,
                    "high_ex":    max_ex,
                    "low_ex":     min_ex,
                })

        divergences.sort(key=lambda x: x["spread_pct"], reverse=True)

        if not divergences:
            return self.ok(f"✅ 无明显跨所价差（阈值 {threshold_pct}%）")

        lines = [f"⚡ *跨所价差扫描*（>{threshold_pct}%）\n"]
        for d in divergences:
            lines.append(
                f"• `{d['symbol']}` 价差 `{d['spread_pct']:.3f}%`\n"
                f"  高：{d['high_ex']} `${d['prices'][d['high_ex']]:,.2f}`  "
                f"低：{d['low_ex']} `${d['prices'][d['low_ex']]:,.2f}`"
            )
        lines.append("\n💡 价差 > 0.3% 可能存在套利空间（考虑手续费和滑点）")

        return self.ok("\n".join(lines), data={"divergences": divergences})

    # ── 成交量对比（内部保留，/compare 已包含此数据）─────────────────────────

    def _volume(self, symbol: str = "BTC", **_) -> dict:
        spot_tasks = [(_fetch_spot_volume, label, symbol) for label in SPOT_EXCHANGES]
        perp_tasks = [(_fetch_perp_volume, label, symbol) for label in SPOT_EXCHANGES]

        spot_vols, perp_vols = {}, {}
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs_spot = {pool.submit(fn, *args): "spot" for fn, *args in spot_tasks}
            futs_perp = {pool.submit(fn, *args): "perp" for fn, *args in perp_tasks}
            all_futs  = {**futs_spot, **futs_perp}
            for fut in as_completed(all_futs, timeout=15):
                kind = all_futs[fut]
                try:
                    label, v = fut.result()
                    if v is not None:
                        if kind == "spot":
                            spot_vols[f"{label} 现货"] = v
                        else:
                            perp_vols[f"{label} 合约"] = v
                except Exception:
                    pass

        volumes = {**spot_vols, **perp_vols}
        if not volumes:
            return self.err(f"获取 {symbol} 成交量失败")

        total = sum(volumes.values())
        lines = [f"📈 *{symbol} 24h 成交量对比*\n"]
        for ex, vol in sorted(volumes.items(), key=lambda x: x[1], reverse=True):
            share = vol / total * 100 if total else 0
            lines.append(f"• *{ex}*：`${vol/1e9:.2f}B` ({share:.1f}%)")

        lines.append(f"\n合计：`${total/1e9:.2f}B`")
        return self.ok("\n".join(lines), data={"volumes": volumes, "total": total})

    # ── 上架情况查询 ──────────────────────────────────────────────────────────

    def _listings(self, symbol: str = "BTC", **_) -> dict:
        spot_tasks = [(_fetch_spot_price,   label, symbol) for label in SPOT_EXCHANGES]
        perp_tasks = [(_fetch_perp_funding, label, symbol) for label in SPOT_EXCHANGES]

        spot_results, perp_results = {}, {}
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs_spot = {pool.submit(fn, *args): "spot" for fn, *args in spot_tasks}
            futs_perp = {pool.submit(fn, *args): "perp" for fn, *args in perp_tasks}
            all_futs  = {**futs_spot, **futs_perp}
            for fut in as_completed(all_futs, timeout=15):
                kind = all_futs[fut]
                try:
                    label, v = fut.result()
                    if kind == "spot":
                        spot_results[label] = v
                    else:
                        perp_results[label] = v
                except Exception:
                    pass

        hl_rate = _hl_funding(symbol)
        perp_results["Hyperliquid"] = hl_rate

        spot_listed = [ex for ex, v in spot_results.items() if v is not None]
        perp_listed = [ex for ex, v in perp_results.items() if v is not None]
        spot_miss   = [ex for ex in SPOT_EXCHANGES if spot_results.get(ex) is None]
        perp_miss   = [ex for ex in PERP_EXCHANGES if perp_results.get(ex) is None]

        lines = [f"🔍 *{symbol} 交易所上架情况*\n"]

        lines.append("*现货（Spot）*")
        for ex in SPOT_EXCHANGES:
            p = spot_results.get(ex)
            if p is not None:
                p_str = f"${p:,.2f}" if p >= 0.01 else f"${p:.8f}".rstrip("0")
                lines.append(f"  ✅ {ex}  `{p_str}`")
            else:
                lines.append(f"  ❌ {ex}  未上架或暂不可用")

        lines.append("\n*永续合约（Perp）*")
        for ex in PERP_EXCHANGES:
            r = perp_results.get(ex)
            if r is not None:
                ann = r * 3 * 365 * 100
                lines.append(f"  ✅ {ex}  资金费率 `{r*100:+.4f}%/8h`（年化 `{ann:+.1f}%`）")
            else:
                lines.append(f"  ❌ {ex}  无合约或暂不可用")

        lines.append(
            f"\n现货：{len(spot_listed)}/{len(SPOT_EXCHANGES)} 家  "
            f"合约：{len(perp_listed)}/{len(PERP_EXCHANGES)} 家"
        )
        if spot_miss:
            lines.append(f"_现货未上架：{', '.join(spot_miss)}_")
        if perp_miss:
            lines.append(f"_合约未上架：{', '.join(perp_miss)}_")

        return self.ok("\n".join(lines), data={
            "symbol":       symbol,
            "spot_listed":  spot_listed,
            "perp_listed":  perp_listed,
            "spot_missing": spot_miss,
            "perp_missing": perp_miss,
        })
