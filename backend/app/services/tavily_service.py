"""Tavily 搜索服务 - 用于搜索实时价格信息"""

import os
import logging
from typing import List, Dict, Any, Optional
from tavily import TavilyClient

logger = logging.getLogger(__name__)


class TavilySearchService:
    """Tavily 搜索服务类"""

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 Tavily 搜索服务

        Args:
            api_key: Tavily API Key，如果不提供则从环境变量获取
        """
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            logger.warning("TAVILY_API_KEY 未设置，Tavily 搜索功能将不可用")
            self.client = None
        else:
            self.client = TavilyClient(api_key=self.api_key)
            logger.info("Tavily 搜索服务初始化成功")

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
        if not self.client:
            return None

        try:
            # 判断是否为免费景点关键词
            free_keywords = [
                "公园",
                "广场",
                "海滩",
                "沙滩",
                "湿地",
                "绿道",
                "免费",
                "无需门票",
            ]
            is_likely_free = any(
                keyword in attraction_name for keyword in free_keywords
            )

            # 改进搜索查询
            query = f"{city}{attraction_name}门票价格 收费"
            logger.info(f"搜索景点价格: {query}")

            response = self.client.search(
                query=query,
                search_depth="advanced",
                max_results=5,
                include_answer=True,
                include_raw_content=True,
            )

            # 检查搜索结果中是否有"免费"关键词
            results_text = ""
            for result in response.get("results", []):
                results_text += (
                    result.get("content", "") + " " + result.get("title", "") + " "
                )

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
                indicator in results_text for indicator in free_indicators
            )

            if is_confirmed_free or is_likely_free:
                logger.info(f"景点 '{attraction_name}' 确认免费")
                return 0

            # 尝试从搜索结果中提取价格
            price = self._extract_price_from_search_result(response)
            if price:
                logger.info(f"找到景点 '{attraction_name}' 门票价格: {price}元")
                return price
            else:
                # 如果无法确定价格，但看起来像是免费景点，返回0
                if is_likely_free:
                    logger.info(f"景点 '{attraction_name}' 可能是免费的，返回0")
                    return 0
                logger.warning(f"未找到景点 '{attraction_name}' 的门票价格")
                return None

        except Exception as e:
            logger.error(f"搜索景点 '{attraction_name}' 价格失败: {e}")
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
        if not self.client:
            return None

        try:
            # 优化搜索查询
            query = f"{city}{hotel_name}酒店住宿预订价格 每晚 多少钱 2024 2025"
            logger.info(f"搜索酒店价格: {query}")

            response = self.client.search(
                query=query,
                search_depth="advanced",
                max_results=5,
                include_answer=True,
                include_raw_content=True,
            )

            # 酒店价格通常较高，调整合理价格范围
            price = self._extract_price_with_range(
                response, min_price=100, max_price=10000
            )
            if price:
                logger.info(f"找到酒店 '{hotel_name}' 价格: {price}元/晚")
                return price
            else:
                logger.warning(f"未找到酒店 '{hotel_name}' 的价格")
                return None

        except Exception as e:
            logger.error(f"搜索酒店 '{hotel_name}' 价格失败: {e}")
            return None

    async def search_meal_price(
        self, restaurant_name: str, city: str, meal_type: str = "人均"
    ) -> Optional[int]:
        """
        搜索餐饮价格

        Args:
            restaurant_name: 餐厅名称或餐饮类型
            city: 城市名称
            meal_type: 餐饮类型，如"人均"、"早餐"等

        Returns:
            人均消费（元），如果搜索失败返回 None
        """
        if not self.client:
            return None

        try:
            query = f"{city}{restaurant_name}{meal_type}消费多少钱"
            logger.info(f"搜索餐饮价格: {query}")

            response = self.client.search(
                query=query, search_depth="basic", max_results=3, include_answer=True
            )

            price = self._extract_price_from_search_result(response)
            if price:
                logger.info(f"找到餐饮 '{restaurant_name}' 价格: {price}元")
                return price
            else:
                logger.warning(f"未找到餐饮 '{restaurant_name}' 的价格")
                return None

        except Exception as e:
            logger.error(f"搜索餐饮 '{restaurant_name}' 价格失败: {e}")
            return None

    async def search_city_prices(self, city: str) -> Dict[str, Any]:
        """
        搜索城市的整体消费水平

        Args:
            city: 城市名称

        Returns:
            包含各类消费参考价格的字典
        """
        if not self.client:
            return {}

        try:
            query = f"{city}旅游消费预算 景点门票 酒店住宿 餐饮人均费用参考"
            logger.info(f"搜索城市整体价格水平: {query}")

            response = self.client.search(
                query=query, search_depth="advanced", max_results=5, include_answer=True
            )

            result = {
                "city": city,
                "search_answer": response.get("answer", ""),
                "sources": [r.get("url") for r in response.get("results", [])],
            }

            return result

        except Exception as e:
            logger.error(f"搜索城市 '{city}' 价格水平失败: {e}")
            return {}

    def _extract_price_from_search_result(
        self, response: Dict[str, Any]
    ) -> Optional[int]:
        """
        从搜索结果中提取价格信息

        Args:
            response: Tavily 搜索结果

        Returns:
            提取到的价格，如果失败返回 None
        """
        import re

        # 定义价格匹配模式
        price_patterns = [
            r"[¥￥]\s*(\d+)",  # ¥100
            r"(\d+)\s*元",  # 100元
            r"门票\s*[¥￥]?\s*(\d+)",  # 门票100
            r"票价\s*[¥￥]?\s*(\d+)",  # 票价100
            r"价格.*?[¥￥]?\s*(\d+)",  # 价格 100
            r"人均\s*[¥￥]?\s*(\d+)",  # 人均100
            r"(?:成人|学生|儿童)?票.*?([\d\.]+)",  # 成人票 100
            r"([\d\.]+)\s*元.*?票",  # 100元门票
        ]

        # 调试：记录搜索返回的内容
        logger.debug(f"Tavily 搜索结果: {len(response.get('results', []))} 条")

        # 1. 首先检查 AI 生成的答案
        answer = response.get("answer", "")
        if answer:
            logger.debug(f"AI 回答: {answer[:200]}...")
            for pattern in price_patterns:
                matches = re.findall(pattern, answer)
                if matches:
                    # 返回第一个匹配的数字
                    try:
                        price = float(matches[0])
                        if 5 <= price <= 5000:  # 合理的价格范围
                            logger.debug(f"从 AI 回答中提取到价格: {price}")
                            return int(price)
                    except ValueError:
                        continue

        # 2. 从搜索结果内容中提取
        results = response.get("results", [])
        for result in results:
            content = result.get("content", "")
            title = result.get("title", "")
            url = result.get("url", "")
            text = content + " " + title

            logger.debug(f"处理搜索结果: {title[:50]}...")

            # 同样的价格匹配逻辑
            for pattern in price_patterns:
                matches = re.findall(pattern, text)
                if matches:
                    try:
                        price = float(matches[0])
                        if 5 <= price <= 5000:
                            logger.debug(
                                f"从搜索结果中提取到价格: {price}, 来源: {url}"
                            )
                            return int(price)
                    except ValueError:
                        continue

        logger.debug("未找到有效价格信息")
        return None


# 全局服务实例
tavily_service = TavilySearchService()


async def search_attraction_prices(
    city: str, attractions: List[Dict[str, Any]]
) -> Dict[str, int]:
    """
    批量搜索景点价格

    Args:
        city: 城市名称
        attractions: 景点列表

    Returns:
        景点名称到价格的映射
    """
    prices = {}
    for attraction in attractions:
        name = attraction.get("name", "")
        if name:
            price = await tavily_service.search_attraction_price(name, city)
            if price:
                prices[name] = price
    return prices


async def search_hotel_prices(
    city: str, hotels: List[Dict[str, Any]]
) -> Dict[str, int]:
    """
    批量搜索酒店价格

    Args:
        city: 城市名称
        hotels: 酒店列表

    Returns:
        酒店名称到价格的映射
    """
    prices = {}
    for hotel in hotels:
        name = hotel.get("name", "")
        if name:
            price = await tavily_service.search_hotel_price(name, city)
            if price:
                prices[name] = price
    return prices
