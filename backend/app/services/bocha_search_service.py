"""博查搜索服务 - 替换原百度搜索服务

主要变更：
1. API 从百度 chat completions 格式 -> 博查 Web Search API
2. 响应解析从 choices[0].message.content -> webPages.value[].summary/snippet
3. 新增 SQLite 缓存层（SearchCache），避免重复调用相同查询

博查 API 端点：https://api.bochaai.com/v1/web-search
认证方式：Bearer token（环境变量 BOCHA_API_KEY）
"""

import os
import re
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)


class SearchCache:
    """
    搜索结果 SQLite 缓存。

    设计原则：
    - 景点/酒店价格缓存 7 天（变化慢）
    - 天气等实时信息不缓存（走高德 MCP）
    - cache_key 格式："{type}:{city}:{name}"，如 "price:北京:故宫"
    - 缓存未命中返回 None，调用方自行决定是否回退到网络请求
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            project_root = Path(__file__).parent.parent.parent
            db_path = str(project_root / "trip_planner.db")

        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表结构"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT    UNIQUE NOT NULL,
                    result    TEXT    NOT NULL,
                    created_at TEXT   NOT NULL,
                    expires_at TEXT   NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_key ON search_cache(cache_key)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires_at ON search_cache(expires_at)
            """)
            conn.commit()
        logger.info(f"[SearchCache] 数据库已就绪: {self.db_path}")

    def get(self, cache_key: str) -> Optional[Any]:
        """
        读取缓存，过期自动返回 None。
        """
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT result, expires_at FROM search_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()

        if row is None:
            return None

        result_json, expires_at = row
        if expires_at < now:
            self.delete(cache_key)
            logger.debug(f"[SearchCache] 缓存已过期: {cache_key}")
            return None

        logger.info(f"[SearchCache] 命中缓存: {cache_key}")
        return json.loads(result_json)

    def set(self, cache_key: str, value: Any, ttl_days: int = 7):
        """
        写入缓存。
        """
        now = datetime.now()
        expires_at = (now + timedelta(days=ttl_days)).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO search_cache (cache_key, result, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    result     = excluded.result,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    cache_key,
                    json.dumps(value, ensure_ascii=False),
                    now.isoformat(),
                    expires_at,
                ),
            )
            conn.commit()
        logger.debug(f"[SearchCache] 已写入缓存: {cache_key}（TTL {ttl_days}d）")

    def delete(self, cache_key: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM search_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()

    def cleanup_expired(self):
        """清理所有过期缓存"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM search_cache WHERE expires_at < ?", (now,)
            )
            conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"[SearchCache] 已清理 {cursor.rowcount} 条过期缓存")


class BochaSearchService:
    """
    博查 Web Search API 服务

    API 文档：https://open.bochaai.com
    端点：POST https://api.bochaai.com/v1/web-search
    认证：Authorization: Bearer <BOCHA_API_KEY>
    """

    BASE_URL = "https://api.bochaai.com/v1/web-search"

    def __init__(
        self,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("BOCHA_API_KEY")
        self._cache = SearchCache(db_path)

        if not self.api_key:
            logger.warning("[博查搜索] BOCHA_API_KEY 未设置，搜索功能不可用")
        else:
            masked = self.api_key[:8] + "***"
            logger.info(f"[博查搜索] 服务初始化成功 (Key: {masked})")

    async def search(
        self,
        query: str,
        count: int = 5,
        freshness: str = "oneYear",
        summary: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        调用博查 Web Search API。
        """
        if not self.api_key:
            logger.error("[博查搜索] API Key 未配置")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "count": count,
            "freshness": freshness,
            "summary": summary,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.BASE_URL,
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )
            if resp.status_code == 200:
                logger.info(f"[博查搜索] 查询成功: {query!r}")
                return resp.json()
            else:
                logger.error(
                    f"[博查搜索] API 错误 {resp.status_code}: {resp.text[:300]}"
                )
                return None
        except httpx.ConnectError as e:
            logger.error(f"[博查搜索] 连接失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[博查搜索] 请求异常: {e}", exc_info=True)
            return None

    def _extract_content(self, result: Dict[str, Any]) -> str:
        """
        从博查响应中提取可用于价格分析的文本。
        """
        pages = result.get("webPages", {}).get("value", [])
        if not pages:
            return ""

        parts = []
        for page in pages[:5]:
            text = page.get("summary") or page.get("snippet") or ""
            if text:
                parts.append(text)

        return "\n".join(parts)

    def _extract_price(
        self,
        text: str,
        min_price: int = 10,
        max_price: int = 5000,
    ) -> Optional[int]:
        """从文本中提取第一个在合理范围内的价格数字"""
        if not text:
            return None

        patterns = [
            r"[¥￥]\s*(\d+)",
            r"(\d+)\s*元/人",
            r"门票\s*[¥￥]?\s*(\d+)",
            r"价格\s*[¥￥]?\s*(\d+)",
            r"人均\s*[¥￥]?\s*(\d+)",
            r"每晚\s*[¥￥]?\s*(\d+)",
            r"大约\s*(\d+)\s*元",
            r"(\d+)\s*元",
        ]

        for pattern in patterns:
            for match in re.findall(pattern, text):
                try:
                    price = int(float(match))
                    if min_price <= price <= max_price:
                        return price
                except ValueError:
                    continue
        return None

    async def search_attraction_price(
        self, attraction_name: str, city: str
    ) -> Optional[int]:
        """搜索景点门票价格，带缓存"""
        cache_key = f"price:{city}:{attraction_name}"

        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        free_keywords = [
            "公园",
            "广场",
            "海滩",
            "沙滩",
            "湿地",
            "绿道",
            "古镇",
            "老街",
            "步行街",
        ]
        is_likely_free = any(kw in attraction_name for kw in free_keywords)

        result = await self.search(
            query=f"{city} {attraction_name} 门票价格",
            count=5,
            freshness="oneYear",
            summary=True,
        )
        if not result:
            price = 0 if is_likely_free else None
            if price is not None:
                self._cache.set(cache_key, price, ttl_days=7)
            return price

        content = self._extract_content(result)

        free_indicators = ["免费", "无需门票", "免票", "开放式", "免门票", "不收门票"]
        is_confirmed_free = any(kw in content for kw in free_indicators)

        if is_confirmed_free or is_likely_free:
            logger.info(f"[博查搜索] '{attraction_name}' 确认免费")
            self._cache.set(cache_key, 0, ttl_days=14)
            return 0

        price = self._extract_price(content, min_price=10, max_price=500)
        if price is not None:
            logger.info(f"[博查搜索] '{attraction_name}' 门票: {price}元")
            self._cache.set(cache_key, price, ttl_days=7)
        else:
            logger.warning(f"[博查搜索] 未找到 '{attraction_name}' 门票价格")

        return price

    async def search_hotel_price(self, hotel_name: str, city: str) -> Optional[int]:
        """搜索酒店价格，带缓存"""
        cache_key = f"hotel:{city}:{hotel_name}"

        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result = await self.search(
            query=f"{city} {hotel_name} 酒店价格 预订",
            count=5,
            freshness="oneMonth",
            summary=True,
        )
        if not result:
            return None

        content = self._extract_content(result)
        price = self._extract_price(content, min_price=100, max_price=3000)

        if price is not None:
            logger.info(f"[博查搜索] '{hotel_name}' 酒店均价: {price}元/晚")
            self._cache.set(cache_key, price, ttl_days=3)
        else:
            logger.warning(f"[博查搜索] 未找到 '{hotel_name}' 价格")

        return price

    async def search_meal_price(
        self, restaurant_name: str, city: str, meal_type: str = "人均"
    ) -> Optional[int]:
        """搜索餐饮人均价格，带缓存"""
        cache_key = f"meal:{city}:{restaurant_name}"

        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result = await self.search(
            query=f"{city} {restaurant_name} 餐厅 人均消费",
            count=5,
            freshness="oneYear",
            summary=True,
        )
        if not result:
            return None

        content = self._extract_content(result)
        price = self._extract_price(content, min_price=20, max_price=1000)

        if price is not None:
            logger.info(f"[博查搜索] '{restaurant_name}' 人均: {price}元")
            self._cache.set(cache_key, price, ttl_days=7)
        else:
            logger.warning(f"[博查搜索] 未找到 '{restaurant_name}' 价格")

        return price


_service: Optional[BochaSearchService] = None


def get_bocha_search_service() -> BochaSearchService:
    """获取博查搜索服务单例"""
    global _service
    if _service is None:
        _service = BochaSearchService()
    return _service


async def search_attraction_prices(
    city: str, attractions: List[Dict[str, Any]]
) -> Dict[str, int]:
    """批量搜索景点价格"""
    svc = get_bocha_search_service()
    prices = {}
    for attraction in attractions:
        name = attraction.get("name", "")
        if name:
            price = await svc.search_attraction_price(name, city)
            if price is not None:
                prices[name] = price
    return prices


async def search_hotel_prices(
    city: str, hotels: List[Dict[str, Any]]
) -> Dict[str, int]:
    """批量搜索酒店价格"""
    svc = get_bocha_search_service()
    prices = {}
    for hotel in hotels:
        name = hotel.get("name", "")
        if name:
            price = await svc.search_hotel_price(name, city)
            if price is not None:
                prices[name] = price
    return prices
