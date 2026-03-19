"""行程存储模块

复用 bocha_search_service 建好的 trip_planner.db，
新增 trip_plans 表，提供存和查两个接口。

设计原则：
- 存：plan_trip_async 结束后写入，不阻塞主流程
- 查：Planner.generate 调用前查同城同天数历史方案，
  作为上下文注入 prompt，而不是直接返回缓存
  （历史方案是"参考"，不是"答案"）
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class TripPlanStore:
    """
    行程存储，使用与 SearchCache 同一个 SQLite 文件。

    表结构：
    - city / travel_days：用于相似查询的索引字段
    - plan_json：完整 TripPlan 的 JSON 序列化
    - created_at：用于取最近的历史方案
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            project_root = Path(__file__).parent.parent.parent.parent
            db_path = str(project_root / "trip_planner.db")
        self.db_path = db_path
        self._init_table()

    def _init_table(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trip_plans (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    city        TEXT    NOT NULL,
                    travel_days INTEGER NOT NULL,
                    plan_json   TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trip_city_days
                ON trip_plans(city, travel_days)
            """)
            conn.commit()
        logger.info(f"[TripPlanStore] 数据库已就绪: {self.db_path}")

    def save(self, city: str, travel_days: int, plan: Dict[str, Any]) -> int:
        """
        保存一条行程记录。

        Args:
            city: 目的地城市
            travel_days: 旅行天数
            plan: TripPlan.model_dump() 的结果

        Returns:
            新插入记录的 id
        """
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trip_plans (city, travel_days, plan_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (city, travel_days, json.dumps(plan, ensure_ascii=False), now),
            )
            conn.commit()
        logger.info(
            f"[TripPlanStore] 已保存行程: {city} {travel_days}天 (id={cursor.lastrowid})"
        )
        return cursor.lastrowid

    def find_similar(
        self,
        city: str,
        travel_days: int,
        limit: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        查询同城市、同天数的历史行程，按时间倒序取最近几条。

        Args:
            city: 目的地城市
            travel_days: 旅行天数
            limit: 最多返回几条（默认 2，够 Planner 参考了）

        Returns:
            历史 TripPlan 列表（已反序列化），无历史则返回空列表
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT plan_json FROM trip_plans
                WHERE city = ? AND travel_days = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (city, travel_days, limit),
            ).fetchall()

        if not rows:
            logger.info(f"[TripPlanStore] 无历史行程: {city} {travel_days}天")
            return []

        plans = []
        for (plan_json,) in rows:
            try:
                plans.append(json.loads(plan_json))
            except json.JSONDecodeError as e:
                logger.warning(f"[TripPlanStore] 历史行程反序列化失败: {e}")

        logger.info(
            f"[TripPlanStore] 找到 {len(plans)} 条历史行程: {city} {travel_days}天"
        )
        return plans


_store: Optional[TripPlanStore] = None


def get_trip_plan_store() -> TripPlanStore:
    global _store
    if _store is None:
        _store = TripPlanStore()
    return _store
