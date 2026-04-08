"""
skills/base.py — 技能基类
所有 Skill 继承此类，获得数据读取、日志、环境变量等公共能力。
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class BaseSkill:
    """所有技能的基类。"""

    def __init__(self, data_dir: Path, memory_dir: Path, env: dict):
        self.data_dir   = Path(data_dir)
        self.memory_dir = Path(memory_dir)
        self.env        = env
        self.log        = logging.getLogger(self.__class__.__name__)

    # ── 数据读取 ──────────────────────────────────────────────────────────────

    def load(self, filename: str) -> dict | list | None:
        """从 data/ 目录读取缓存 JSON。"""
        path = self.data_dir / filename
        if not path.exists():
            return None
        try:
            with open(path) as f:
                raw = json.load(f)
            # 兼容带时间戳包装格式 {"_updated": ..., "data": ...}
            return raw.get("data") if isinstance(raw, dict) and "data" in raw else raw
        except Exception as e:
            self.log.warning(f"load {filename} failed: {e}")
            return None

    def data_age_minutes(self, filename: str) -> float:
        """返回缓存文件距今多少分钟（不存在则返回 9999）。"""
        path = self.data_dir / filename
        if not path.exists():
            return 9999.0
        try:
            with open(path) as f:
                raw = json.load(f)
            updated = raw.get("_updated")
            if not updated:
                return 9999.0
            ts = datetime.fromisoformat(updated)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - ts
            return delta.total_seconds() / 60
        except Exception:
            return 9999.0

    # ── 环境变量 ──────────────────────────────────────────────────────────────

    def getenv(self, key: str, default: str = "") -> str:
        return self.env.get(key) or os.getenv(key, default)

    # ── 标准响应格式 ──────────────────────────────────────────────────────────

    @staticmethod
    def ok(text: str, data: dict = None) -> dict:
        return {"success": True, "text": text, "data": data or {}}

    @staticmethod
    def err(message: str) -> dict:
        return {"success": False, "text": f"❌ {message}", "data": {}}

    @staticmethod
    def pending(text: str, data: dict = None) -> dict:
        """等待用户确认的操作响应，不是错误。"""
        return {"status": "pending", "success": False, "text": text, "data": data or {}}

    # ── 熔断检查（所有子类共用）─────────────────────────────────────────────────

    def _check_circuit_breaker(self) -> tuple:
        """
        检查每日亏损熔断。返回 (blocked: bool, reason: str)。
        当日亏损超过账户 MAX_DAILY_LOSS_PCT% 时禁止新开仓。
        若存在有效的 override 标志文件则跳过检查。
        """
        # 检查 override 标志（/override_circuit 命令写入）
        override_path = self.memory_dir / "circuit_override.json"
        if override_path.exists():
            try:
                with open(override_path) as f:
                    flag = json.load(f)
                expires = datetime.fromisoformat(flag.get("expires_at", ""))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < expires:
                    return False, ""   # override 有效，放行
            except Exception:
                pass

        max_loss_pct = float(self.getenv("MAX_DAILY_LOSS_PCT", "5"))
        account = self.load("hl_account.json")
        if not account:
            return False, ""
        acct_val = account.get("account_value_usdc", 0)
        if not acct_val:
            return False, ""

        history_path = self.memory_dir / "trade_history.json"
        if not history_path.exists():
            return False, ""

        today = datetime.now(timezone.utc).date().isoformat()
        try:
            with open(history_path) as f:
                history = json.load(f)
        except Exception:
            return False, ""

        today_loss = sum(
            t.get("realized_pnl", 0)
            for t in history
            if t.get("timestamp", "")[:10] == today and t.get("realized_pnl", 0) < 0
        )

        loss_pct = abs(today_loss) / acct_val * 100
        if loss_pct >= max_loss_pct:
            return True, (
                f"今日亏损 `${abs(today_loss):.2f}` ({loss_pct:.1f}%) "
                f"已达熔断阈值 {max_loss_pct}%，新开仓已禁止。\n"
                f"如需强制继续，请发送 `/override_circuit`"
            )
        return False, ""

    # ── 子类必须实现 ──────────────────────────────────────────────────────────

    def run(self, **kwargs) -> dict:
        raise NotImplementedError
