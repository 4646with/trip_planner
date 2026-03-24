"""Planner 模块 - 行程汇总生成器

职责：从 Worker Agents 获取结构化数据，生成最终旅行计划
"""

import json
import logging
from typing import Dict, Any, List

from langchain_core.messages import SystemMessage, HumanMessage

from .schemas.state import AgentState
from .prompts.agents import AgentPrompts
from ..models.schemas import TripPlan

logger = logging.getLogger(__name__)


ROUTE_ALIAS_MAP = {
    "origin": ["origin", "origin_name", "start", "start_point", "起点"],
    "destination": ["destination", "dest_name", "end", "end_point", "终点"],
    "transportation": ["transportation", "transit_mode", "mode", "出行方式"],
    "duration": ["duration", "time", "cost_time", "耗时"],
    "route_detail": ["route_detail", "steps", "instruction", "路线详情"],
}


def _normalize_route_dict(raw_dict: dict) -> dict:
    """将任意字典的 Key 归一化为标准 Key"""
    normalized = {}
    for standard_key, aliases in ROUTE_ALIAS_MAP.items():
        for alias in aliases:
            if alias in raw_dict:
                normalized[standard_key] = raw_dict[alias]
                break
    return normalized


ROUTE_ESSENTIAL_KEYS = {
    "origin",
    "destination",
    "transportation",
    "duration",
    "route_detail",
}

ESSENTIAL_KEYS = {
    "name",
    "address",
    "location",
    "visit_duration",
    "description",
    "category",
    "ticket_price",
    "rating",
    "price_range",
    "distance",
}


def _extract_planner_fields(items: list, max_items: int = 5) -> list:
    """带归一化容错的数据清洗器"""
    if not items:
        return []

    first_item = items[0]
    if not isinstance(first_item, dict):
        return []

    route_identifiers = {"origin", "start", "destination", "end", "transportation"}
    is_route_data = any(key in first_item for key in route_identifiers)

    cleaned = []
    for item in items[:max_items]:
        if not isinstance(item, dict):
            continue

        if is_route_data:
            normalized_item = _normalize_route_dict(item)
            if normalized_item:
                cleaned.append(normalized_item)
        else:
            cleaned_item = {k: v for k, v in item.items() if k in ESSENTIAL_KEYS}
            if cleaned_item:
                cleaned.append(cleaned_item)

    return cleaned


class Planner:
    """
    Planner Agent - 行程汇总生成器

    职责：
    1. 接收 Worker Agents 的结构化数据（景点、酒店、天气、路线）
    2. 根据 trip_intent 中的用户意图和约束生成最终行程
    3. 使用 with_structured_output 确保输出符合 TripPlan schema
    """

    def __init__(self, llm):
        self.structured_llm = llm.with_structured_output(TripPlan)

    async def generate(self, state: AgentState) -> Dict[str, Any]:
        logger.info("Planner 正在生成最终方案...")

        attractions_context = _extract_planner_fields(
            state.get("attractions", []), max_items=5
        )
        hotels_context = _extract_planner_fields(state.get("hotels", []), max_items=3)
        weather_context = state.get("weather_info", [])
        routes_context = _extract_planner_fields(state.get("routes", []), max_items=5)

        logger.info(
            f"Planner 清洗后数据量: "
            f"attractions={len(attractions_context)}, "
            f"hotels={len(hotels_context)}, "
            f"weather={len(weather_context)}, "
            f"routes={len(routes_context)}"
        )

        intent = state.get("trip_intent", {})
        accommodation = state.get("accommodation", "未知")
        preferences = state.get("preferences", [])
        special_requirements = intent.get("special_requirements", [])
        budget_level = intent.get("budget_level", "unknown")
        hotel_intent = intent.get("hotel_intent", "unknown")
        itinerary_style = intent.get("itinerary_style", "relaxed")
        hard_constraints = intent.get("hard_constraints", [])

        preferences_str = ", ".join(preferences) if preferences else "无"
        special_req_str = (
            ", ".join(special_requirements) if special_requirements else "无"
        )

        if itinerary_style == "compact":
            geo_constraint = "【地理约束 - 强制】所有景点必须在步行15分钟或单次地铁可达范围内，不得安排跨越城市对角线的景点组合。优先选择同一商圈/区域内的景点。"
        else:
            geo_constraint = ""

        constraints_block = ""
        if hard_constraints:
            constraints_str = "\n".join(f"- {c}" for c in hard_constraints)
            constraints_block = f"""
⚠️【用户硬性约束 - 必须严格执行，不得忽略或替换】
{constraints_str}
"""

        planner_messages = [
            SystemMessage(content=AgentPrompts.PLANNER),
            HumanMessage(
                content=f"""
请根据以下结构化数据生成旅行计划。

【基本信息】
目标城市: {state.get("city", "未知")}
旅行日期: {state.get("start_date", "未知")} 至 {state.get("end_date", "未知")}
旅行天数: {state.get("travel_days", 0)}天
交通方式: {state.get("transportation", "未知")}
住宿偏好: {accommodation}
旅行偏好: {preferences_str}

【用户意图（请严格遵守）】
住宿需求: {
                    "用户明确不需要订酒店，请勿安排酒店费用"
                    if hotel_intent == "skip"
                    else "用户需要订酒店，请根据住宿偏好推荐"
                }
预算水平: {budget_level}
特殊需求: {special_req_str}
{
                    "（注意：有特殊需求，请在行程安排和建议中体现，例如选择无障碍设施、避免长途步行等）"
                    if special_requirements
                    else ""
                }

{geo_constraint}

{constraints_block}

【结构化数据（已清洗，字段完整）】
景点数据:
{json.dumps(attractions_context, ensure_ascii=False)}

天气数据（共{len(weather_context)}条，必须全部写入 weather_info 字段，不得省略）:
{json.dumps(weather_context, ensure_ascii=False)}

酒店数据:
{json.dumps(hotels_context, ensure_ascii=False)}

路线数据:
{json.dumps(routes_context, ensure_ascii=False)}

请严格遵守输出格式，不要包含任何 markdown 标记。
"""
            ),
        ]

        try:
            plan_obj = await self.structured_llm.ainvoke(planner_messages)
            final_plan = plan_obj.model_dump()
            logger.info("Planner 成功生成强类型校验后的旅行计划。")
        except Exception as e:
            logger.error(f"Planner 生成或校验失败: {str(e)}")
            final_plan = TripPlan(
                city=state.get("city", "未知"),
                start_date=state.get("start_date", "未知"),
                end_date=state.get("end_date", "未知"),
                overall_suggestions="抱歉，行程生成过程中发生错误，请稍后重试。",
            ).model_dump()

        return {"final_plan": final_plan, "next": "end"}

    def get_node(self):
        async def planner_node(state: AgentState) -> Dict[str, Any]:
            return await self.generate(state)

        return planner_node
