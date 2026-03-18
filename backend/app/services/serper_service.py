"""Serper 搜索服务 - 使用 Serper.dev API 搜索实时价格信息"""

import os
import logging
from typing import List, Dict, Any, Optional
import httpx
import re

logger = logging.getLogger(__name__)


class SerperSearchService:
    """Serper (serper.dev) 搜索服务类"""

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 Serper 搜索服务

        Args:
            api_key: Serper API Key，如果不提供则从环境变量获取
        """
        self.api_key = api_key or os.getenv("SERPER_API_KEY")
        self.base_url = "https://google.serper.dev/search"

        if not self.api_key:
            logger.warning("SERPER_API_KEY 未设置，Serper 搜索功能将不可用")
        else:
            # 隐藏大部分 key，只显示前8位用于调试
            masked_key = self.api_key[:8] + "***" if len(self.api_key) > 8 else "***"
            logger.info(f"Serper 搜索服务初始化成功 (Key: {masked_key})")

    async def _search(self, query: str) -> Optional[Dict[str, Any]]:
        """
        执行搜索请求

        Args:
            query: 搜索查询

        Returns:
            搜索结果字典
        """
        if not self.api_key:
            return None

        try:
            headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

            payload = {"q": query, "hl": "zh-cn", "gl": "cn", "num": 5}

            logger.debug(f"[Serper] 发送请求: {query}")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.base_url, headers=headers, json=payload, timeout=10.0
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(
                        f"[Serper] API 错误: {response.status_code} - {response.text}"
                    )
                    return None

        except Exception as e:
            logger.error(f"[Serper] 搜索请求失败: {e}")
            return None

    async def search_attraction_price(
        self, attraction_name: str, city: str
    ) -> Optional[int]:
        """
        搜索景点门票价格

        Args:
            attraction_name: 景点名称
            city: 城市名称

        Returns:
            门票价格（元），免费景点返回 0，搜索失败返回 None
        """
        if not self.api_key:
            return None

        try:
            # 判断是否为免费景点
            free_keywords = ["公园", "广场", "海滩", "沙滩", "湿地", "绿道", "免费"]
            is_likely_free = any(
                keyword in attraction_name for keyword in free_keywords
            )

            # 构建搜索查询
            query = f"{city} {attraction_name} 门票价格"
            logger.info(f"[Serper] 搜索景点价格: {query}")

            results = await self._search(query)
            if not results:
                return None

            # 提取搜索结果文本
            all_text = ""

            # 从知识图谱中提取
            if "knowledgeGraph" in results:
                kg = results["knowledgeGraph"]
                all_text += kg.get("description", "") + " "

            # 从搜索结果中提取
            for result in results.get("organic", [])[:3]:
                all_text += result.get("title", "") + " "
                all_text += result.get("snippet", "") + " "

            # 判断是否免费
            free_indicators = [
                "免费",
                "无需门票",
                "免票",
                "开放式",
                "免门票",
                "不收门票",
            ]
            is_confirmed_free = any(
                indicator in all_text for indicator in free_indicators
            )

            if is_confirmed_free or is_likely_free:
                logger.info(f"景点 '{attraction_name}' 确认免费")
                return 0

            # 提取价格
            price = self._extract_price(all_text, min_price=10, max_price=500)
            if price:
                logger.info(
                    f"[Serper] 找到景点 '{attraction_name}' 门票价格: {price}元"
                )
                return price
            else:
                # 可能是免费景点
                if is_likely_free:
                    logger.info(f"景点 '{attraction_name}' 可能是免费的，返回0")
                    return 0
                logger.warning(f"[Serper] 未找到景点 '{attraction_name}' 的门票价格")
                return None

        except Exception as e:
            logger.error(f"[Serper] 搜索景点 '{attraction_name}' 价格失败: {e}")
            return None

    async def search_hotel_price(self, hotel_name: str, city: str) -> Optional[int]:
        """
        搜索酒店价格

        Args:
            hotel_name: 酒店名称
            city: 城市名称

        Returns:
            酒店每晚价格（元），如果搜索失败返回 None
        """
        if not self.api_key:
            return None

        try:
            # 针对奢华酒店搜索
            query = f"{city} {hotel_name} 五星级酒店 预订价格 每晚多少钱"
            logger.info(f"[Serper] 搜索酒店价格: {query}")

            results = await self._search(query)
            if not results:
                return None

            # 提取文本
            all_text = ""
            for result in results.get("organic", [])[:3]:
                all_text += result.get("title", "") + " "
                all_text += result.get("snippet", "") + " "

            # 酒店价格范围（奢华标准 500-10000元）
            price = self._extract_price(all_text, min_price=500, max_price=10000)
            if price:
                logger.info(f"[Serper] 找到酒店 '{hotel_name}' 价格: {price}元/晚")
                return price
            else:
                logger.warning(f"[Serper] 未找到酒店 '{hotel_name}' 的价格")
                return None

        except Exception as e:
            logger.error(f"[Serper] 搜索酒店 '{hotel_name}' 价格失败: {e}")
            return None

    async def search_meal_price(
        self, restaurant_name: str, city: str, meal_type: str = "人均"
    ) -> Optional[int]:
        """
        搜索餐饮价格

        Args:
            restaurant_name: 餐厅名称或餐饮类型
            city: 城市名称
            meal_type: 餐饮类型

        Returns:
            人均消费（元），如果搜索失败返回 None
        """
        if not self.api_key:
            return None

        try:
            # 针对奢华体验，搜索高端餐厅价格
            query = f"{city} {restaurant_name} 高端餐厅 人均消费 价格"
            logger.info(f"[Serper] 搜索餐饮价格: {query}")

            results = await self._search(query)
            if not results:
                return None

            # 提取文本
            all_text = ""
            for result in results.get("organic", [])[:3]:
                all_text += result.get("title", "") + " "
                all_text += result.get("snippet", "") + " "

            # 餐饮人均价格（奢华标准 100-2000元）
            price = self._extract_price(all_text, min_price=100, max_price=2000)
            if price:
                logger.info(f"[Serper] 找到餐饮 '{restaurant_name}' 价格: {price}元")
                return price
            else:
                logger.warning(f"[Serper] 未找到餐饮 '{restaurant_name}' 的价格")
                return None

        except Exception as e:
            logger.error(f"[Serper] 搜索餐饮 '{restaurant_name}' 价格失败: {e}")
            return None

        try:
            query = f"{city} {restaurant_name} 人均消费 价格"
            logger.info(f"[Serper] 搜索餐饮价格: {query}")

            results = await self._search(query)
            if not results:
                return None

            # 提取文本
            all_text = ""
            for result in results.get("organic", [])[:3]:
                all_text += result.get("title", "") + " "
                all_text += result.get("snippet", "") + " "

            # 餐饮人均价格
            price = self._extract_price(all_text, min_price=20, max_price=1000)
            if price:
                logger.info(f"[Serper] 找到餐饮 '{restaurant_name}' 价格: {price}元")
                return price
            else:
                logger.warning(f"[Serper] 未找到餐饮 '{restaurant_name}' 的价格")
                return None

        except Exception as e:
            logger.error(f"[Serper] 搜索餐饮 '{restaurant_name}' 价格失败: {e}")
            return None

    def _extract_price(
        self, text: str, min_price: int = 10, max_price: int = 5000
    ) -> Optional[int]:
        """
        从文本中提取价格

        Args:
            text: 搜索文本
            min_price: 最小合理价格
            max_price: 最大合理价格

        Returns:
            提取到的价格
        """
        if not text:
            return None

        # 价格匹配模式
        patterns = [
            r"[¥￥]\s*(\d+)",  # ¥100
            r"(\d+)\s*元",  # 100元
            r"门票\s*[¥￥]?\s*(\d+)",  # 门票100
            r"价格.*?[¥￥]?\s*(\d+)",  # 价格 100
            r"人均\s*[¥￥]?\s*(\d+)",  # 人均100
            r"每晚\s*[¥￥]?\s*(\d+)",  # 每晚100
            r"([\d\.]+)\s*元",  # 100.5元
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                try:
                    price = float(match)
                    if min_price <= price <= max_price:
                        return int(price)
                except ValueError:
                    continue

        return None


# 全局服务实例
serper_service = SerperSearchService()


# 为了保持向后兼容，提供与 Tavily 相同的接口
async def search_attraction_prices(
    city: str, attractions: List[Dict[str, Any]]
) -> Dict[str, int]:
    """批量搜索景点价格"""
    prices = {}
    for attraction in attractions:
        name = attraction.get("name", "")
        if name:
            price = await serper_service.search_attraction_price(name, city)
            if price is not None:
                prices[name] = price
    return prices


async def search_hotel_prices(
    city: str, hotels: List[Dict[str, Any]]
) -> Dict[str, int]:
    """批量搜索酒店价格"""
    prices = {}
    for hotel in hotels:
        name = hotel.get("name", "")
        if name:
            price = await serper_service.search_hotel_price(name, city)
            if price is not None:
                prices[name] = price
    return prices
