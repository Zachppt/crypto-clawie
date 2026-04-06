"""
skills/base.py — 技能基类
所有 Skill 继承此类，获得数据读取、日志、环境变量等公共能力。
"""

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
            delta = datetime.now(timezone.utc) - ts.replace(tzinfo=timezone.utc)
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

    # ── 子类必须实现 ──────────────────────────────────────────────────────────

    def run(self, **kwargs) -> dict:
        raise NotImplementedError
