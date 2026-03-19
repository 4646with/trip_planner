"""解析工具 - JSON 提取和 TripPlan 构建"""

import json
import logging
from typing import Union, Optional, Dict, Any, List
from pydantic import ValidationError

from ...models.schemas import TripRequest, TripPlan, Budget
from ...services.bocha_search_service import (
    search_attraction_prices,
    search_hotel_prices,
)

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    从文本中提取并解析 JSON

    Args:
        text: 包含 JSON 的文本

    Returns:
        解析后的字典，如果失败返回 None
    """
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            extracted = text[start : end + 1]
            return json.loads(extracted)
    except Exception as e:
        logger.error(f"提取 JSON 失败: {e}")
    return None


def parse_json_to_dict(json_data: Union[dict, str]) -> Optional[Dict[str, Any]]:
    """
    解析 JSON 数据为字典

    支持多种输入格式：
    - 已经是字典：直接返回
    - JSON 字符串：解析后返回
    - 包含 JSON 的文本：提取后解析

    Args:
        json_data: JSON 数据（字典或字符串）

    Returns:
        解析后的字典，失败返回 None
    """
    try:
        # 情况1: 已经是字典
        if isinstance(json_data, dict):
            logger.info("接收到有效字典数据")
            return json_data

        # 情况2: 是字符串，尝试解析
        elif isinstance(json_data, str):
            try:
                data = json.loads(json_data)
                logger.info("JSON 解析成功")
                return data
            except json.JSONDecodeError:
                logger.info("直接解析失败，尝试从文本中提取 JSON")
                if extracted_data := extract_json_from_text(json_data):
                    logger.info("从文本中提取 JSON 成功")
                    return extracted_data

        # 所有解析尝试失败
        logger.warning(f"无法解析数据: {type(json_data)}")
        return None

    except Exception as e:
        logger.error(f"解析过程发生错误: {e}")
        return None


def calculate_budget(data: Dict[str, Any]) -> Dict[str, int]:
    """
    计算旅行预算

    Args:
        data: 包含 days 数组的旅行数据

    Returns:
        预算字典
    """
    total_attr = 0
    total_meal = 0
    total_hotel = 0
    total_transport = 0

    logger.debug(f"开始计算预算... days 数量: {len(data.get('days', []))}")

    for day in data.get("days", []):
        # 累加景点门票
        for attr in day.get("attractions", []):
            price = attr.get("ticket_price", 0)
            logger.debug(f"景点: {attr.get('name', '未知')}, 门票价格: {price}")
            if isinstance(price, (int, float)) and price >= 0:
                total_attr += price

        # 累加餐饮费用
        for meal in day.get("meals", []):
            cost = meal.get("estimated_cost", 0)
            logger.debug(f"餐饮: {meal.get('name', '未知')}, 费用: {cost}")
            if isinstance(cost, (int, float)) and cost >= 0:
                total_meal += cost

    # 累加酒店费用（从 days 数组中提取）
    hotels = []
    for day in data.get("days", []):
        if day.get("hotel"):
            hotels.append(day["hotel"])
    # 兼容旧格式
    if not hotels:
        hotels = data.get("hotels", [])

    for hotel in hotels:
        price = hotel.get("price", 0) or hotel.get("estimated_cost", 0)
        if isinstance(price, (int, float)) and price >= 0:
            total_hotel += price
            logger.debug(f"酒店: {hotel.get('name', '未知')}, 价格: {price}")

    # 累加交通费用
    for route in data.get("routes", []):
        cost = route.get("distance_cost", 0) or route.get("estimated_cost", 0)
        if isinstance(cost, (int, float)) and cost >= 0:
            total_transport += cost
            logger.debug(f"路线: {route.get('name', '未知')}, 费用: {cost}")

    logger.debug(
        f"景点总费用: {total_attr}, 餐饮总费用: {total_meal}, 酒店总费用: {total_hotel}, 交通总费用: {total_transport}"
    )

    # 构建预算字典
    budget = {
        "total_attractions": int(total_attr),
        "total_hotels": int(total_hotel),
        "total_meals": int(total_meal),
        "total_transportation": int(total_transport),
        "total": int(total_attr + total_meal + total_hotel + total_transport + 100),
    }

    logger.debug(f"最终预算: {budget}")
    return budget


async def calculate_budget_with_search(
    city: str, data: Dict[str, Any]
) -> Dict[str, int]:
    """
    使用博查搜索计算预算（获取实时价格）

    Args:
        city: 城市名称
        data: 包含 days 数组的旅行数据

    Returns:
        预算字典
    """
    total_attr = 0
    total_meal = 0
    total_hotel = 0
    total_transport = 0

    logger.info(f"开始使用博查搜索计算预算... 城市: {city}")

    attractions_to_search = []
    for day in data.get("days", []):
        for attr in day.get("attractions", []):
            name = attr.get("name", "")
            existing_price = attr.get("ticket_price", 0)
            if name and (not existing_price or existing_price <= 0):
                attractions_to_search.append(attr)

    if attractions_to_search:
        logger.info(f"搜索 {len(attractions_to_search)} 个景点价格...")
        prices = await search_attraction_prices(city, attractions_to_search)
        for attr in attractions_to_search:
            name = attr.get("name", "")
            if name in prices:
                attr["ticket_price"] = prices[name]
                logger.info(f"OK '{attr['name']}' price: {prices[name]}")
            else:
                attr["ticket_price"] = attr.get("ticket_price", 0)

    for day in data.get("days", []):
        for attr in day.get("attractions", []):
            price = attr.get("ticket_price", 0)
            if isinstance(price, (int, float)) and price >= 0:
                total_attr += int(price)

    for day in data.get("days", []):
        for meal in day.get("meals", []):
            existing_cost = meal.get("estimated_cost", 0)
            if existing_cost and existing_cost > 0:
                total_meal += int(existing_cost)
            else:
                meal["estimated_cost"] = "*"

    hotels = []
    for day in data.get("days", []):
        if day.get("hotel"):
            hotels.append(day["hotel"])

    if not hotels:
        hotels = data.get("hotels", [])

    hotels_to_search = []
    for hotel in hotels:
        name = hotel.get("name", "")
        existing_price = hotel.get("price", 0) or hotel.get("estimated_cost", 0)
        price_range = hotel.get("price_range", "")

        if isinstance(existing_price, (int, float)) and existing_price > 0:
            total_hotel += int(existing_price)
        elif price_range:
            import re

            numbers = re.findall(r"(\d+)", price_range)
            if numbers:
                max_price = max([int(n) for n in numbers])
                hotel["price"] = max_price
                total_hotel += max_price
            else:
                hotel["price"] = "*"
        elif name:
            hotels_to_search.append(hotel)

    if hotels_to_search:
        hotel_prices = await search_hotel_prices(city, hotels_to_search)
        for hotel in hotels_to_search:
            name = hotel.get("name", "")
            if name in hotel_prices:
                hotel["price"] = hotel_prices[name]
                total_hotel += hotel_prices[name]

    for route in data.get("routes", []):
        cost = route.get("estimated_cost", 0)
        if isinstance(cost, (int, float)) and cost > 0:
            total_transport += int(cost)

    if total_transport == 0:
        for day in data.get("days", []):
            daily_cost = day.get("daily_transport_cost", 0)
            if isinstance(daily_cost, (int, float)) and daily_cost > 0:
                total_transport += int(daily_cost)

    budget = {
        "total_attractions": total_attr,
        "total_hotels": total_hotel,
        "total_meals": total_meal,
        "total_transportation": total_transport,
        "total": total_attr + total_meal + total_hotel + total_transport,
    }

    print(f"Budget calculation completed: {budget}")
    logger.info(f"Budget calculation completed: {budget}")
    return budget


def build_trip_plan(data: Dict[str, Any], request: TripRequest) -> TripPlan:
    """
    构建 TripPlan 对象（同步版本，使用原始价格）

    Args:
        data: 解析后的旅行数据字典
        request: 原始请求对象

    Returns:
        TripPlan 对象
    """
    # 计算预算（不使用搜索）
    budget_data = calculate_budget(data)

    # 处理 overall_suggestions
    overall_suggestions = data.get("overall_suggestions", "")
    if isinstance(overall_suggestions, list):
        overall_suggestions = " ".join(overall_suggestions)

    try:
        plan = TripPlan(
            city=data.get("city", request.city),
            start_date=data.get("start_date", request.start_date),
            end_date=data.get("end_date", request.end_date),
            days=data.get("days", []),
            weather_info=data.get("weather_info", []),
            overall_suggestions=overall_suggestions or "祝您旅途愉快！",
            budget=Budget(**budget_data),
        )
        logger.info("TripPlan 创建成功")
        return plan

    except ValidationError as e:
        logger.error(f"TripPlan 结构验证失败:")
        for error in e.errors():
            logger.error(f"  - 路径: {error['loc']} | 原因: {error['msg']}")

        # 返回安全版本
        return _create_safe_plan(data, request, budget_data)


def _create_safe_plan(
    data: Dict[str, Any], request: TripRequest, budget_data: Dict[str, int]
) -> TripPlan:
    """
    创建安全的 TripPlan（验证失败时的兜底方案）
    """
    safe_data = {
        "city": data.get("city", request.city),
        "start_date": data.get("start_date", request.start_date),
        "end_date": data.get("end_date", request.end_date),
        "days": [],
        "weather_info": [],
        "overall_suggestions": "数据验证失败，但规划完成",
        "budget": Budget(**budget_data),
    }
    logger.warning(f"使用安全版本: {safe_data}")
    return TripPlan(**safe_data)


def create_default_plan(request: TripRequest) -> TripPlan:
    """
    创建默认空计划（解析完全失败时的兜底）

    Args:
        request: 原始请求对象

    Returns:
        空的 TripPlan 对象
    """
    logger.warning("使用默认空计划")
    return TripPlan(
        city=request.city,
        start_date=request.start_date,
        end_date=request.end_date,
        days=[],
        overall_suggestions="无法生成详细计划",
        budget=Budget(),
    )


def parse_and_build_plan(json_data: Union[dict, str], request: TripRequest) -> TripPlan:
    """
    一站式解析和构建 TripPlan

    Args:
        json_data: JSON 数据（字典或字符串）
        request: 原始请求对象

    Returns:
        TripPlan 对象
    """
    # 尝试解析 JSON
    data = parse_json_to_dict(json_data)

    if data is None:
        return create_default_plan(request)

    # 确保必要字段存在
    if "city" not in data:
        data["city"] = request.city
    if "start_date" not in data:
        data["start_date"] = request.start_date
    if "end_date" not in data:
        data["end_date"] = request.end_date
    if "overall_suggestions" not in data:
        data["overall_suggestions"] = "祝您旅途愉快！"
    if "days" not in data:
        data["days"] = []
    if "weather_info" not in data:
        data["weather_info"] = []

    # 构建 TripPlan
    return build_trip_plan(data, request)


async def build_trip_plan_async(data: Dict[str, Any], request: TripRequest) -> TripPlan:
    """
    构建 TripPlan 对象（异步版本，使用博查搜索实时价格）
    """
    city = data.get("city", request.city)

    budget_data = await calculate_budget_with_search(city, data)
    print(f"Budget result: {budget_data}")

    # 处理 overall_suggestions
    overall_suggestions = data.get("overall_suggestions", "")
    if isinstance(overall_suggestions, list):
        overall_suggestions = " ".join(overall_suggestions)

    try:
        plan = TripPlan(
            city=city,
            start_date=data.get("start_date", request.start_date),
            end_date=data.get("end_date", request.end_date),
            days=data.get("days", []),
            weather_info=data.get("weather_info", []),
            overall_suggestions=overall_suggestions or "祝您旅途愉快！",
            budget=Budget(**budget_data),
        )
        logger.info("TripPlan（异步版）创建成功")
        return plan

    except ValidationError as e:
        logger.error(f"TripPlan 结构验证失败:")
        for error in e.errors():
            logger.error(f"  - 路径: {error['loc']} | 原因: {error['msg']}")

        # 返回安全版本
        return _create_safe_plan(data, request, budget_data)


async def parse_and_build_plan_async(
    json_data: Union[dict, str], request: TripRequest
) -> TripPlan:
    """
    一站式解析和构建 TripPlan（异步版本，使用博查搜索）
    """
    # 尝试解析 JSON
    data = parse_json_to_dict(json_data)

    if data is None:
        return create_default_plan(request)

    # 确保必要字段存在
    if "city" not in data:
        data["city"] = request.city
    if "start_date" not in data:
        data["start_date"] = request.start_date
    if "end_date" not in data:
        data["end_date"] = request.end_date
    if "overall_suggestions" not in data:
        data["overall_suggestions"] = "祝您旅途愉快！"
    if "days" not in data:
        data["days"] = []
    if "weather_info" not in data:
        data["weather_info"] = []

    # 构建 TripPlan（异步版本）
    return await build_trip_plan_async(data, request)
