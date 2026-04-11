"""
skills/focus — 单币种专项追踪
用法：
  /focus SOL [15]    — 开始追踪 SOL，每 15 分钟推送综合报告
  /focus cancel      — 停止追踪
  /focus status      — 查看当前追踪配置
  /focus report      — 立即生成一份报告

报告内容：
  • 实时价格 + 24h 涨跌 + 资金费率
  • 跨所价格/费率对比
  • 做市商阶段识别（MM Analysis）
  • 链上监控摘要（若已配置该币种相关地址）
  • 相关新闻过滤（按币种名过滤快讯）
  • 综合信号评级
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from skills.base import BaseSkill


class FocusSkill(BaseSkill):

    def run(self, action: str = "report", **kwargs) -> dict:
        dispatch = {
            "set":    self._set,
            "cancel": self._cancel,
            "status": self._status,
            "report": self._report,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：set / cancel / status / report")
        return fn(**kwargs)

    # ── 配置管理 ──────────────────────────────────────────────────────────────

    def _focus_path(self) -> Path:
        return self.memory_dir / "focus.json"

    def _set(self, token: str = "BTC", interval_min: int = 15,
             chat_id: str = None, topic_id: str = None, **_) -> dict:
        token = token.upper()
        config = {
            "token":        token,
            "interval_min": int(interval_min),
            "chat_id":      str(chat_id) if chat_id else None,
            "topic_id":     str(topic_id) if topic_id else None,
            "set_at":       datetime.now(timezone.utc).isoformat(),
        }
        self._focus_path().parent.mkdir(exist_ok=True)
        with open(self._focus_path(), "w") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        # 清空上次报告时间，让调度器立即生成第一份
        last_path = self.memory_dir / "focus_last.json"
        if last_path.exists():
            last_path.unlink()

        return self.ok(
            f"🎯 *专项追踪已启动*\n\n"
            f"• 追踪标的：`{token}`\n"
            f"• 推送间隔：每 `{interval_min}` 分钟\n\n"
            f"调度器将自动推送追踪报告。\n"
            f"发送 `/focus report` 可立即获取一份。\n"
            f"发送 `/focus cancel` 停止追踪。",
            data=config,
        )

    def _cancel(self, **_) -> dict:
        path = self._focus_path()
        if path.exists():
            config = json.load(open(path))
            token  = config.get("token", "?")
            path.unlink()
            last_path = self.memory_dir / "focus_last.json"
            if last_path.exists():
                last_path.unlink()
            return self.ok(f"✅ 已停止对 `{token}` 的专项追踪")
        return self.ok("当前没有进行中的专项追踪")

    def _status(self, **_) -> dict:
        path = self._focus_path()
        if not path.exists():
            return self.ok(
                "当前没有专项追踪。\n"
                "发送 `/focus BTC 15` 开始追踪 BTC，每 15 分钟推送报告。"
            )
        config = json.load(open(path))
        token    = config["token"]
        interval = config["interval_min"]
        set_at   = config.get("set_at", "")[:16].replace("T", " ") + " UTC"

        last_report = "尚未生成"
        last_path   = self.memory_dir / "focus_last.json"
        if last_path.exists():
            last_data   = json.load(open(last_path))
            last_report = last_data.get("time", "")[:16].replace("T", " ") + " UTC"

        return self.ok(
            f"🎯 *专项追踪状态*\n\n"
            f"• 追踪标的：`{token}`\n"
            f"• 推送间隔：每 `{interval}` 分钟\n"
            f"• 启动时间：`{set_at}`\n"
            f"• 上次报告：`{last_report}`\n\n"
            f"发送 `/focus report` 立即获取报告\n"
            f"发送 `/focus cancel` 停止追踪",
            data=config,
        )

    # ── 报告生成 ──────────────────────────────────────────────────────────────

    def _report(self, token: str = None, **_) -> dict:
        """生成综合专项报告。token 可以临时覆盖 focus.json 中的标的。"""
        # 确定标的
        if not token:
            path = self._focus_path()
            if path.exists():
                token = json.load(open(path)).get("token", "BTC")
            else:
                token = "BTC"
        token = token.upper()

        sections = []

        # ── 1. 价格与基本数据 ────────────────────────────────────────────────
        from skills.crypto_data import CryptoDataSkill
        price_result = CryptoDataSkill(self.data_dir, self.memory_dir, self.env).run(
            action="price", symbol=token
        )
        if price_result.get("success"):
            sections.append(f"💰 *价格*\n{price_result['text']}")
        else:
            # 直接从 hl_market 读
            market = self.load("hl_market.json")
            if market:
                asset = next((a for a in market.get("assets", []) if a["symbol"] == token), None)
                if asset:
                    p = asset["mark_price"]
                    c = asset["change_24h_pct"]
                    f = asset["funding_8h"]
                    emoji = "🟢" if c >= 0 else "🔴"
                    sections.append(
                        f"💰 *价格*\n{emoji} `${p:,.2f}` ({c:+.2f}%)\n"
                        f"资金费率：`{f*100:+.4f}%/8h`"
                    )

        # ── 2. 做市商阶段识别 ────────────────────────────────────────────────
        from skills.mm_analysis import MMAnalysisSkill
        mm_result = MMAnalysisSkill(self.data_dir, self.memory_dir, self.env).run(
            action="analyze", symbol=token
        )
        if mm_result.get("success"):
            d = mm_result["data"]
            phase_emoji = {
                "ACCUMULATION": "🟡", "WASH_TRADING": "🔵",
                "DISTRIBUTION": "🔴", "PUMP_SETUP": "🟢",
            }.get(d.get("phase", ""), "⚪")
            conf_pct = int(d.get("confidence", 0) * 100)
            sections.append(
                f"🕵️ *做市商阶段*\n"
                f"{phase_emoji} {d.get('phase_label', '未知')}（置信度 {conf_pct}%）\n"
                f"主要信号：{d['reasons'][0] if d.get('reasons') else '—'}"
            )

        # ── 3. 跨所概况（价差 + 资金费率对比） ──────────────────────────────
        from skills.exchange_agg import ExchangeAggSkill
        agg = ExchangeAggSkill(self.data_dir, self.memory_dir, self.env)

        cmp_result = agg.run(action="compare", symbol=token)
        if cmp_result.get("success") and cmp_result["data"].get("prices"):
            prices = cmp_result["data"]["prices"]
            max_p  = max(prices.values())
            min_p  = min(prices.values())
            spread = (max_p - min_p) / ((max_p + min_p) / 2) * 100
            spread_note = " ⚠️ 价差异常" if spread > 0.15 else ""
            sections.append(
                f"🏦 *跨所价差*\n最大价差 `{spread:.3f}%`{spread_note}\n"
                f"（涵盖 {len(prices)} 家交易所）"
            )

        # ── 4. 相关新闻（过滤关键词） ────────────────────────────────────────
        news = self.load("news_cache.json")
        news_items = []
        if news:
            kw = token.lower()
            # 常见别名映射
            aliases = {
                "BTC": ["bitcoin", "btc"],
                "ETH": ["ethereum", "eth"],
                "SOL": ["solana", "sol"],
                "BNB": ["bnb", "binance coin"],
                "XRP": ["ripple", "xrp"],
            }.get(token, [token.lower()])

            for item in news[:30]:
                text = (item.get("title", "") + item.get("content", "")).lower()
                if any(a in text for a in aliases):
                    news_items.append(item.get("title", ""))
                if len(news_items) >= 3:
                    break

        if news_items:
            lines = [f"📰 *相关快讯（最近 {len(news_items)} 条）*"]
            for n in news_items:
                lines.append(f"• {n[:80]}")
            sections.append("\n".join(lines))
        else:
            sections.append("📰 *快讯*\n暂无相关快讯")

        # ── 5. 链上监控摘要 ──────────────────────────────────────────────────
        watchlist_path = self.memory_dir / "watchlist.json"
        if watchlist_path.exists():
            try:
                watchlist = json.load(open(watchlist_path))
                # 找出与该币种链相关的地址
                chain_map = {"SOL": "SOL", "ETH": "ETH", "BNB": "BNB", "BTC": "BTC"}
                relevant_chain = chain_map.get(token)
                related = [w for w in watchlist if not relevant_chain or w.get("chain") == relevant_chain]
                if related:
                    sections.append(
                        f"⛓️ *链上监控*\n已监控 {len(related)} 个{relevant_chain or ''}地址，"
                        f"发现异动时自动推送"
                    )
                else:
                    sections.append("⛓️ *链上*\n暂无相关链上监控地址（/watch add 可添加）")
            except Exception:
                pass
        else:
            sections.append("⛓️ *链上*\n未配置监控地址（/watch add 可添加）")

        # ── 汇总 ────────────────────────────────────────────────────────────
        now_cst = datetime.utcnow()
        now_str = now_cst.strftime("%H:%M UTC")

        header = f"🎯 *{token} 专项追踪报告* | {now_str}"
        body   = ("\n\n" + "─" * 20 + "\n\n").join(sections)
        full   = f"{header}\n\n{body}"

        return self.ok(full, data={"token": token, "sections": len(sections)})
