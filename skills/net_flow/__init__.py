"""
skills/net_flow — 交易所净流量分析
通过监控主流 CEX 已知充提款热钱包地址，推断市场资金流向：
  • 净流入交易所（Inflow > Outflow）→ 抛压增大，看空信号
  • 净流出交易所（Outflow > Inflow）→ 积累囤币，看多信号

支持链：ETH、BNB（Etherscan 兼容 API）
数据源：Etherscan.io / BscScan.com（需 API Key，免费注册）
"""

from __future__ import annotations

import os
import time
import requests
from skills.base import BaseSkill

_S = requests.Session()
_S.headers.update({"User-Agent": "crypto-clawie/2.0"})
T = 10  # timeout

# ── 已知主流 CEX 热钱包地址（ETH 链） ────────────────────────────────────────
# 来源：Etherscan 标签 / Arkham / Nansen 公开数据
ETH_EXCHANGE_WALLETS: dict[str, list[str]] = {
    "Binance": [
        "0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE",  # Binance 7 (主充值热钱包)
        "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8",  # Binance 8
        "0xF977814e90dA44bFA03b6295A0616a897441aceC",  # Binance 9
        "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance 10
    ],
    "OKX": [
        "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b",  # OKX 热钱包 1
        "0x236F9F97e0E62388479bf9E1130d6d78F9AC52f7",  # OKX 热钱包 2
    ],
    "Bybit": [
        "0xf89d7b9c864f589bbF53a82105107622B35EaA40",  # Bybit 热钱包 1
    ],
    "Coinbase": [
        "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3",  # Coinbase 1
        "0xa090e606e30bd747d4e6245a1517ebe430f0057e",  # Coinbase 2
    ],
}

# ETH 链 Etherscan API
ETH_API = "https://api.etherscan.io/api"
BNB_API = "https://api.bscscan.com/api"

# 24h 内的 USDT/USDC 大额转账阈值（USD）
LARGE_TRANSFER_USD = 1_000_000  # 100 万 USD

# USDT/USDC 合约地址（ETH 链）
ETH_TOKENS = {
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
}


def _get(url: str, params: dict) -> dict | None:
    try:
        r = _S.get(url, params=params, timeout=T)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


class NetFlowSkill(BaseSkill):
    """
    交易所净流量分析。
    用法：
      action="analyze"  — 分析过去 24h ETH 链大额净流量
      action="signal"   — 综合净流量 + 资金费率给出信号强度
      action="wallets"  — 列出当前监控的 CEX 钱包地址
    """

    def run(self, action: str = "analyze", **kwargs) -> dict:
        dispatch = {
            "analyze": self._analyze,
            "signal":  self._signal,
            "wallets": self._wallets,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：analyze / signal / wallets")
        return fn(**kwargs)

    # ── 净流量分析 ─────────────────────────────────────────────────────────────

    def _analyze(self, hours: int = 24, token: str = "USDT", **_) -> dict:
        api_key = self.getenv("ETHERSCAN_API_KEY", "")
        if not api_key:
            return self.err(
                "需要 ETHERSCAN_API_KEY\n"
                "免费注册：https://etherscan.io/register\n"
                "填入 .env 后重启 scheduler"
            )

        token_upper = token.upper()
        if token_upper not in ETH_TOKENS:
            return self.err(f"仅支持 USDT / USDC，收到：{token}")
        contract = ETH_TOKENS[token_upper]

        # 时间窗口：过去 N 小时的 Unix 时间戳
        now       = int(time.time())
        start_ts  = now - hours * 3600

        # 每家交易所的净流量
        exchange_flows: dict[str, dict] = {}
        all_inflow  = 0.0
        all_outflow = 0.0

        for ex_name, addresses in ETH_EXCHANGE_WALLETS.items():
            ex_in  = 0.0
            ex_out = 0.0

            for addr in addresses:
                # 获取该地址的 ERC-20 转账记录
                data = _get(ETH_API, {
                    "module":          "account",
                    "action":          "tokentx",
                    "contractaddress": contract,
                    "address":         addr,
                    "starttime":       start_ts,
                    "endtime":         now,
                    "sort":            "desc",
                    "apikey":          api_key,
                    "page":            1,
                    "offset":          200,
                })
                if not data or data.get("status") != "1":
                    continue

                for tx in data.get("result", []):
                    try:
                        decimals = int(tx.get("tokenDecimal", 6))
                        amount   = int(tx["value"]) / (10 ** decimals)
                        if amount < 10_000:   # 忽略小额（< $10k）
                            continue
                        to_addr   = tx.get("to", "").lower()
                        from_addr = tx.get("from", "").lower()
                        addr_lower = addr.lower()

                        if to_addr == addr_lower:
                            ex_in += amount   # 资金流入交易所（充值 → 可能卖出）
                        elif from_addr == addr_lower:
                            ex_out += amount  # 资金流出交易所（提现 → 可能囤币）
                    except (ValueError, KeyError):
                        continue

            net = ex_in - ex_out
            exchange_flows[ex_name] = {
                "inflow":  round(ex_in,  2),
                "outflow": round(ex_out, 2),
                "net":     round(net,    2),   # 正 = 净流入（看空）, 负 = 净流出（看多）
            }
            all_inflow  += ex_in
            all_outflow += ex_out

        total_net = all_inflow - all_outflow

        # ── 信号判断 ──────────────────────────────────────────────────────────
        if total_net > 50_000_000:        # 净流入 > 5000 万
            signal, signal_emoji = "BEARISH",  "🔴"
        elif total_net > 10_000_000:      # 净流入 > 1000 万
            signal, signal_emoji = "WEAK_BEARISH", "🟡"
        elif total_net < -50_000_000:     # 净流出 > 5000 万
            signal, signal_emoji = "BULLISH",  "🟢"
        elif total_net < -10_000_000:     # 净流出 > 1000 万
            signal, signal_emoji = "WEAK_BULLISH", "🟡"
        else:
            signal, signal_emoji = "NEUTRAL",  "⚪"

        # ── 格式化输出 ────────────────────────────────────────────────────────
        lines = [
            f"🏦 *交易所净流量分析*（过去 {hours}h，{token_upper}，ETH 链）\n",
            f"合计流入：`${all_inflow/1e6:.1f}M` | 流出：`${all_outflow/1e6:.1f}M`",
            f"净流量：`${total_net/1e6:+.1f}M` {signal_emoji} *{signal}*\n",
        ]

        for ex, flow in sorted(exchange_flows.items(), key=lambda x: abs(x[1]["net"]), reverse=True):
            net    = flow["net"]
            n_emoji = "🔴" if net > 5_000_000 else "🟢" if net < -5_000_000 else "⚪"
            lines.append(
                f"{n_emoji} *{ex}*\n"
                f"  流入 `${flow['inflow']/1e6:.1f}M` | 流出 `${flow['outflow']/1e6:.1f}M` | 净 `${net/1e6:+.1f}M`"
            )

        lines.append(f"\n💡 *解读*")
        if signal == "BULLISH":
            lines.append("大量资金流出交易所 → 囤币积累，看多信号较强")
        elif signal == "WEAK_BULLISH":
            lines.append("资金偏流出交易所 → 轻度积累，结合其他指标")
        elif signal == "BEARISH":
            lines.append("大量资金流入交易所 → 抛售压力较大，谨慎看空")
        elif signal == "WEAK_BEARISH":
            lines.append("资金偏流入交易所 → 轻度抛压，结合其他指标")
        else:
            lines.append("资金流进流出基本平衡，无明显方向")

        return self.ok("\n".join(lines), data={
            "signal":         signal,
            "total_net_usd":  round(total_net, 2),
            "total_inflow":   round(all_inflow, 2),
            "total_outflow":  round(all_outflow, 2),
            "exchange_flows": exchange_flows,
            "hours":          hours,
            "token":          token_upper,
        })

    # ── 综合信号 ──────────────────────────────────────────────────────────────

    def _signal(self, symbol: str = "BTC", **_) -> dict:
        """
        综合净流量 + HL 资金费率，输出两维信号评级：
          强度：STRONG / MODERATE / WEAK
          方向：BULLISH / BEARISH / NEUTRAL
        """
        # 净流量信号
        flow_result = self._analyze()
        if not flow_result.get("success"):
            return flow_result

        flow_signal = flow_result["data"]["signal"]
        total_net   = flow_result["data"]["total_net_usd"]

        # 资金费率信号
        market = self.load("hl_market.json")
        funding = 0.0
        price   = 0.0
        if market:
            asset = next((a for a in market.get("assets", []) if a["symbol"] == symbol.upper()), None)
            if asset:
                funding = asset.get("funding_8h", 0)
                price   = asset.get("mark_price", 0)

        # 信号评分（-2 到 +2）
        score = 0
        reasons = []

        # 净流量得分
        if flow_signal == "BULLISH":
            score += 2; reasons.append("交易所大幅净流出（囤币）")
        elif flow_signal == "WEAK_BULLISH":
            score += 1; reasons.append("交易所小幅净流出")
        elif flow_signal == "BEARISH":
            score -= 2; reasons.append("交易所大幅净流入（抛压）")
        elif flow_signal == "WEAK_BEARISH":
            score -= 1; reasons.append("交易所小幅净流入")

        # 资金费率得分（正费率=多头过热→偏空，负费率=空头过热→偏多）
        if funding > 0.001:
            score -= 2; reasons.append(f"资金费率极端正值（{funding*100:.3f}%），多头过热")
        elif funding > 0.0005:
            score -= 1; reasons.append(f"资金费率偏高（{funding*100:.3f}%），多头偏多")
        elif funding < -0.001:
            score += 2; reasons.append(f"资金费率极端负值（{funding*100:.3f}%），空头过热")
        elif funding < -0.0005:
            score += 1; reasons.append(f"资金费率偏低（{funding*100:.3f}%），空头偏多")
        else:
            reasons.append(f"资金费率中性（{funding*100:.3f}%）")

        # 综合判断
        if score >= 3:
            direction, strength = "BULLISH",  "STRONG"
        elif score >= 1:
            direction, strength = "BULLISH",  "MODERATE"
        elif score <= -3:
            direction, strength = "BEARISH",  "STRONG"
        elif score <= -1:
            direction, strength = "BEARISH",  "MODERATE"
        else:
            direction, strength = "NEUTRAL",  "WEAK"

        d_emoji = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "⚪"
        s_map   = {"STRONG": "强烈", "MODERATE": "中等", "WEAK": "弱"}

        text = (
            f"📊 *{symbol.upper()} 综合信号评级*\n\n"
            f"{d_emoji} *{s_map[strength]}{direction}*（评分 {score:+d}）\n\n"
            f"*依据：*\n"
            + "\n".join(f"  • {r}" for r in reasons) +
            f"\n\n💡 信号仅供参考，请结合技术面和风险管理判断"
        )

        return self.ok(text, data={
            "direction":    direction,
            "strength":     strength,
            "score":        score,
            "reasons":      reasons,
            "flow_signal":  flow_signal,
            "funding_8h":   funding,
            "net_flow_usd": total_net,
        })

    # ── 查看监控地址 ──────────────────────────────────────────────────────────

    def _wallets(self, **_) -> dict:
        lines = ["🏦 *监控的 CEX 热钱包地址（ETH 链）*\n"]
        for ex, addrs in ETH_EXCHANGE_WALLETS.items():
            lines.append(f"*{ex}*")
            for a in addrs:
                lines.append(f"  `{a}`")
        lines.append("\n数据来源：Etherscan API（需配置 ETHERSCAN_API_KEY）")
        return self.ok("\n".join(lines), data=ETH_EXCHANGE_WALLETS)
