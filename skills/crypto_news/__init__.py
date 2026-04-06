"""
skills/crypto_news — 加密新闻与快讯
数据来源：BlockBeats 本地缓存
"""

from skills.base import BaseSkill


class CryptoNewsSkill(BaseSkill):

    HL_KEYWORDS = ["hyperliquid", "HL", "永续", "合约", "资金费率", "清算", "爆仓", "DEX", "衍生品"]
    NEGATIVE_KEYWORDS = ["hack", "黑客", "漏洞", "跑路", "rug", "监管", "SEC", "处罚", "暂停", "崩盘"]
    POSITIVE_KEYWORDS = ["上线", "合作", "ETF", "批准", "升级", "突破", "新高"]

    def run(self, action: str = "latest", **kwargs) -> dict:
        dispatch = {
            "latest":  self._latest,
            "hl":      self._hl_news,
            "search":  self._search,
        }
        fn = dispatch.get(action.lower(), self._latest)
        return fn(**kwargs)

    def _latest(self, limit: int = 10, **_) -> dict:
        news = self.load("news_cache.json")
        age  = self.data_age_minutes("news_cache.json")

        if not news:
            return self.err("新闻数据未缓存，请检查 BLOCKBEATS_API_KEY 和 fetcher")

        items = news[:limit] if isinstance(news, list) else []
        if not items:
            return self.ok("暂无最新快讯")

        stale = f"\n⚠️ _数据更新于 {age:.0f} 分钟前_" if age > 30 else ""
        lines = [f"📰 *最新快讯 (前 {len(items)} 条)*\n"]

        for item in items:
            sentiment = self._sentiment(item.get("title", "") + item.get("content", ""))
            emoji     = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(sentiment, "⚪")
            title     = item.get("title", "").strip()
            content   = item.get("content", "").strip()
            lines.append(f"{emoji} {title}")
            if content and content != title:
                lines.append(f"   _{content[:80]}{'...' if len(content) > 80 else ''}_")

        return self.ok("\n".join(lines) + stale, data={"count": len(items)})

    def _hl_news(self, **_) -> dict:
        news = self.load("news_cache.json")
        if not news:
            return self.err("新闻数据未缓存")

        items = [
            item for item in (news if isinstance(news, list) else [])
            if any(kw.lower() in (item.get("title", "") + item.get("content", "")).lower()
                   for kw in self.HL_KEYWORDS)
        ]

        if not items:
            return self.ok("暂无 Hyperliquid 相关快讯")

        lines = [f"💹 *HL 相关快讯 ({len(items)} 条)*\n"]
        for item in items[:10]:
            lines.append(f"• {item.get('title', '').strip()}")
            content = item.get("content", "").strip()
            if content and len(content) > 10:
                lines.append(f"  _{content[:100]}..._" if len(content) > 100 else f"  _{content}_")

        return self.ok("\n".join(lines))

    def _search(self, keyword: str = "", **_) -> dict:
        if not keyword:
            return self.err("请提供搜索关键词")

        news = self.load("news_cache.json")
        if not news:
            return self.err("新闻数据未缓存")

        kw    = keyword.lower()
        items = [
            item for item in (news if isinstance(news, list) else [])
            if kw in (item.get("title", "") + item.get("content", "")).lower()
        ]

        if not items:
            return self.ok(f"未找到包含 `{keyword}` 的快讯")

        lines = [f"🔍 *搜索：{keyword}* ({len(items)} 条)\n"]
        for item in items[:10]:
            lines.append(f"• {item.get('title', '').strip()}")

        return self.ok("\n".join(lines))

    def _sentiment(self, text: str) -> str:
        t = text.lower()
        neg = sum(1 for kw in self.NEGATIVE_KEYWORDS if kw.lower() in t)
        pos = sum(1 for kw in self.POSITIVE_KEYWORDS if kw.lower() in t)
        if neg > pos:
            return "negative"
        if pos > neg:
            return "positive"
        return "neutral"
