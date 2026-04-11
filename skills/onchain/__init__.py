"""
skills/onchain — 链上地址异动监控
支持：Ethereum (ETH)、BNB Chain (BNB)、Solana (SOL)

ETH/BNB 使用 Etherscan / BscScan 免费 API（需注册 key，或不带 key 限速使用）
Solana 使用公开 JSON-RPC，无需 key

配置：
  ETHERSCAN_API_KEY=  （在 etherscan.io 免费注册）
  BSCSCAN_API_KEY=    （在 bscscan.com 免费注册）
  ONCHAIN_ALERT_THRESHOLD=10  默认预警最小金额 USD
"""
import json
import requests
from datetime import datetime, timezone
from skills.base import BaseSkill

T = 10  # timeout

CHAINS = {
    "ETH": {
        "name":     "Ethereum",
        "api_url":  "https://api.etherscan.io/api",
        "env_key":  "ETHERSCAN_API_KEY",
        "explorer": "https://etherscan.io/address/",
        "tx_url":   "https://etherscan.io/tx/",
        "symbol":   "ETH",
        "decimals": 18,
        "native_usd_symbol": "ETH",
    },
    "BNB": {
        "name":     "BNB Chain",
        "api_url":  "https://api.bscscan.com/api",
        "env_key":  "BSCSCAN_API_KEY",
        "explorer": "https://bscscan.com/address/",
        "tx_url":   "https://bscscan.com/tx/",
        "symbol":   "BNB",
        "decimals": 18,
        "native_usd_symbol": "BNB",
    },
    "SOL": {
        "name":     "Solana",
        "api_url":  "https://api.mainnet-beta.solana.com",
        "env_key":  None,
        "explorer": "https://solscan.io/account/",
        "tx_url":   "https://solscan.io/tx/",
        "symbol":   "SOL",
        "decimals": 9,
        "native_usd_symbol": "SOL",
    },
}


class OnchainSkill(BaseSkill):

    WATCHLIST_FILE = "watchlist.json"

    def run(self, action: str = "list", **kwargs) -> dict:
        dispatch = {
            "add":    self._add_watch,
            "remove": self._remove_watch,
            "list":   self._list_watches,
            "scan":   self._scan_all,
            "recent": self._recent_activity,
            "chains": self._chain_overview,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：add / remove / list / scan / recent / chains")
        return fn(**kwargs)

    # ── 监控列表管理 ──────────────────────────────────────────────────────────

    def _load_watchlist(self) -> list:
        path = self.memory_dir / self.WATCHLIST_FILE
        if not path.exists():
            return []
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return []

    def _save_watchlist(self, watchlist: list):
        path = self.memory_dir / self.WATCHLIST_FILE
        path.parent.mkdir(exist_ok=True)
        with open(path, "w") as f:
            json.dump(watchlist, f, ensure_ascii=False, indent=2)

    def _add_watch(self, chain: str = "ETH", address: str = None,
                   label: str = None, alert_threshold: float = None, **_) -> dict:
        chain = chain.upper()
        if chain not in CHAINS:
            return self.err(f"不支持的链：`{chain}`\n支持：ETH / BNB / SOL")
        if not address:
            return self.err("请提供钱包地址\n格式：`/watch add ETH 0x1234... 标签`")

        watchlist = self._load_watchlist()
        for w in watchlist:
            if w["chain"] == chain and w["address"].lower() == address.lower():
                return self.err(f"{chain} `{address[:8]}...` 已在监控列表中")

        threshold = alert_threshold or float(self.getenv("ONCHAIN_ALERT_THRESHOLD", "10"))
        entry = {
            "chain":           chain,
            "address":         address,
            "label":           label or f"{chain}_{address[:6]}",
            "alert_threshold": threshold,
            "added_at":        datetime.now(timezone.utc).isoformat(),
            "last_tx_hash":    None,
            "last_checked":    None,
        }
        watchlist.append(entry)
        self._save_watchlist(watchlist)

        info = CHAINS[chain]
        return self.ok(
            f"✅ *已添加链上监控*\n\n"
            f"• 链：`{info['name']}`\n"
            f"• 地址：`{address}`\n"
            f"• 标签：`{entry['label']}`\n"
            f"• 预警阈值：> {threshold:.0f} {info['symbol']}\n\n"
            f"🔍 浏览器：{info['explorer']}{address}\n\n"
            f"调度器将每 {self.getenv('FETCH_INTERVAL_MIN','5')} 分钟扫描一次"
        )

    def _remove_watch(self, chain: str = "ETH", address: str = None, **_) -> dict:
        if not address:
            return self.err("请提供钱包地址")
        chain = chain.upper()
        watchlist = self._load_watchlist()
        before    = len(watchlist)
        watchlist = [w for w in watchlist
                     if not (w["chain"] == chain and w["address"].lower() == address.lower())]
        if len(watchlist) == before:
            return self.err(f"未找到 {chain} `{address[:12]}...`")
        self._save_watchlist(watchlist)
        return self.ok(f"✅ 已移除 {chain} `{address[:12]}...`")

    def _list_watches(self, **_) -> dict:
        watchlist = self._load_watchlist()
        if not watchlist:
            return self.ok(
                "📋 *监控列表为空*\n\n"
                "添加地址监控：\n"
                "`/watch add ETH 0x1234... 鲸鱼1`\n"
                "`/watch add BNB 0x5678...`\n"
                "`/watch add SOL AbcDef... 交易所热钱包`"
            )
        lines = [f"📋 *链上监控列表（{len(watchlist)} 个）*\n"]
        by_chain: dict = {}
        for w in watchlist:
            by_chain.setdefault(w["chain"], []).append(w)
        for chain in sorted(by_chain):
            info = CHAINS.get(chain, {})
            lines.append(f"*{info.get('name', chain)}*")
            for w in by_chain[chain]:
                last = w.get("last_checked") or "从未"
                if last != "从未":
                    last = last[:10]
                lines.append(
                    f"• `{w['address'][:10]}...{w['address'][-4:]}` — *{w['label']}*\n"
                    f"  阈值：{w.get('alert_threshold', 10):.0f} {info.get('symbol','')} | 上次检查：{last}"
                )
        lines.append("\n`/watch ETH 0x...` — 查看地址近期交易")
        return self.ok("\n".join(lines), data={"watchlist": watchlist})

    # ── 扫描所有监控地址（调度器调用）────────────────────────────────────────

    def _scan_all(self, **_) -> dict:
        watchlist = self._load_watchlist()
        if not watchlist:
            return self.ok("监控列表为空")

        all_alerts  = []
        updated_wl  = list(watchlist)

        for i, w in enumerate(watchlist):
            chain = w["chain"]
            try:
                txs, new_hash = self._get_recent_txs(
                    chain, w["address"], since_hash=w.get("last_tx_hash"), limit=10
                )
                if new_hash and new_hash != w.get("last_tx_hash"):
                    updated_wl[i] = {
                        **w,
                        "last_tx_hash": new_hash,
                        "last_checked": datetime.now(timezone.utc).isoformat(),
                    }
                threshold = w.get("alert_threshold", 10)
                for tx in txs:
                    # 按阈值过滤
                    raw_val = tx.get("value_native", 0)
                    # 粗略用原生代币数量作为阈值（精确USD需调价格API，此处简化）
                    if raw_val >= threshold or threshold <= 0:
                        all_alerts.append({
                            **tx,
                            "label":     w["label"],
                            "address":   w["address"],
                            "threshold": threshold,
                        })
            except Exception as e:
                self.log.warning(f"onchain scan {chain} {w.get('address','')[:12]}: {e}")

        self._save_watchlist(updated_wl)
        return self.ok(
            f"链上扫描完成，{len(all_alerts)} 条新交易",
            data={"alerts": all_alerts}
        )

    # ── 查询特定地址近期活动 ──────────────────────────────────────────────────

    def _recent_activity(self, chain: str = "ETH", address: str = None,
                         limit: int = 5, **_) -> dict:
        chain = chain.upper()
        if chain not in CHAINS:
            return self.err(f"不支持的链：{chain}")
        if not address:
            return self.err("请提供钱包地址\n格式：`/watch ETH 0x1234...`")

        try:
            txs, _ = self._get_recent_txs(chain, address, limit=limit)
        except Exception as e:
            return self.err(f"查询失败：{e}")

        info    = CHAINS[chain]
        short_a = f"{address[:10]}...{address[-4:]}"

        if not txs:
            return self.ok(
                f"📭 *{info['name']}*\n`{short_a}`\n近期无交易记录\n\n"
                f"🔍 {info['explorer']}{address}"
            )

        lines = [f"🔍 *{info['name']} 近期交易*\n`{short_a}`\n"]
        for tx in txs:
            status = tx.get("status", "✅")
            lines.append(
                f"{status} {tx.get('time_display', '?')}\n"
                f"  {tx.get('direction', '')} `{tx.get('value_display', '?')}`\n"
                f"  `{tx.get('hash', '')[:16]}...`"
            )
        lines.append(f"\n🔗 {info['explorer']}{address}")
        return self.ok("\n".join(lines), data={"txs": txs})

    # ── 链上总览 ──────────────────────────────────────────────────────────────

    def _chain_overview(self, **_) -> dict:
        watchlist = self._load_watchlist()
        by_chain: dict = {}
        for w in watchlist:
            by_chain.setdefault(w["chain"], []).append(w)

        lines = ["⛓️ *链上监控概览*\n"]
        if not watchlist:
            lines.append("暂无监控地址")
        else:
            lines.append(f"共监控 {len(watchlist)} 个地址\n")
            for chain in sorted(by_chain):
                info    = CHAINS.get(chain, {})
                entries = by_chain[chain]
                lines.append(f"*{info.get('name', chain)}* — {len(entries)} 个")
                for e in entries[:3]:
                    addr = e["address"]
                    lines.append(f"  • {e['label']} `{addr[:10]}...{addr[-4:]}`")
                if len(entries) > 3:
                    lines.append(f"  ...还有 {len(entries)-3} 个")

        lines.extend([
            "\n*支持的链*",
            "• `ETH` — Ethereum（推荐配置 ETHERSCAN_API_KEY）",
            "• `BNB` — BNB Chain（推荐配置 BSCSCAN_API_KEY）",
            "• `SOL` — Solana（无需 Key，使用公开 RPC）",
            "\n*命令*",
            "`/watch add ETH 0x... 标签` — 添加监控",
            "`/watch list` — 查看列表",
            "`/watch ETH 0x...` — 查最近交易",
            "`/watch remove ETH 0x...` — 移除",
        ])
        return self.ok("\n".join(lines))

    # ── 内部：获取近期交易 ────────────────────────────────────────────────────

    def _get_recent_txs(self, chain: str, address: str,
                        since_hash: str = None, limit: int = 5) -> tuple:
        if chain in ("ETH", "BNB"):
            return self._get_evm_txs(chain, address, since_hash, limit)
        elif chain == "SOL":
            return self._get_sol_txs(address, since_hash, limit)
        return [], None

    def _get_evm_txs(self, chain: str, address: str,
                     since_hash: str, limit: int) -> tuple:
        info    = CHAINS[chain]
        api_key = self.getenv(info["env_key"], "") if info["env_key"] else ""
        params  = {
            "module":     "account",
            "action":     "txlist",
            "address":    address,
            "startblock": 0,
            "endblock":   "latest",
            "page":       1,
            "offset":     min(limit + 5, 20),
            "sort":       "desc",
        }
        if api_key:
            params["apikey"] = api_key

        try:
            r       = requests.get(info["api_url"], params=params, timeout=T)
            result  = r.json()
            raw_txs = result.get("result", [])
            if isinstance(raw_txs, str):
                raise RuntimeError(raw_txs)
        except Exception as e:
            raise RuntimeError(f"EVM API ({chain}): {e}")

        if not raw_txs:
            return [], None

        latest_hash = raw_txs[0]["hash"]
        decimals    = info["decimals"]
        symbol      = info["symbol"]

        txs = []
        for tx in raw_txs:
            if since_hash and tx["hash"].lower() == since_hash.lower():
                break
            if len(txs) >= limit:
                break

            value_wei  = int(tx.get("value", 0))
            value_nat  = value_wei / 10**decimals
            ts         = int(tx.get("timeStamp", 0))
            dt         = datetime.fromtimestamp(ts, tz=timezone.utc)
            is_out     = tx.get("from", "").lower() == address.lower()
            is_error   = tx.get("isError", "0") == "1"

            txs.append({
                "hash":         tx["hash"],
                "chain":        chain,
                "from":         tx.get("from", ""),
                "to":           tx.get("to", ""),
                "value_native": value_nat,
                "value_display": f"{value_nat:.4f} {symbol}",
                "direction":    "↗ 发出" if is_out else "↘ 收到",
                "time_display": dt.strftime("%m-%d %H:%M"),
                "timestamp":    ts,
                "status":       "❌" if is_error else "✅",
                "tx_url":       info["tx_url"] + tx["hash"],
            })

        return txs, latest_hash

    def _get_sol_txs(self, address: str, since_sig: str, limit: int) -> tuple:
        RPC = CHAINS["SOL"]["api_url"]
        try:
            r = requests.post(RPC, json={
                "jsonrpc": "2.0",
                "id":      1,
                "method":  "getSignaturesForAddress",
                "params":  [address, {"limit": min(limit + 5, 20)}],
            }, timeout=T)
            sigs = r.json().get("result", [])
        except Exception as e:
            raise RuntimeError(f"Solana RPC: {e}")

        if not sigs:
            return [], None

        latest_sig = sigs[0]["signature"]
        txs        = []

        for sig_info in sigs:
            sig = sig_info["signature"]
            if since_sig and sig == since_sig:
                break
            if len(txs) >= limit:
                break

            ts  = sig_info.get("blockTime") or 0
            dt  = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            err = sig_info.get("err")
            memo = sig_info.get("memo", "")

            txs.append({
                "hash":          sig,
                "chain":         "SOL",
                "value_native":  0,
                "value_display": f"SOL 交易{(' — ' + memo[:30]) if memo else ''}",
                "direction":     "交易",
                "time_display":  dt.strftime("%m-%d %H:%M") if dt else "?",
                "timestamp":     ts,
                "status":        "❌" if err else "✅",
                "tx_url":        CHAINS["SOL"]["tx_url"] + sig,
            })

        return txs, latest_sig
