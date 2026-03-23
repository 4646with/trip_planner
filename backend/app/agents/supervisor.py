"""Supervisor 模块 - 极简纯逻辑路由"""

import logging
from .schemas.state import AgentState
from .constants import MAX_AGENT_CALLS

logger = logging.getLogger(__name__)


async def _supervisor_logic(state: AgentState) -> dict:
    """核心路由逻辑（纯函数风格）"""
    rid = state.get("request_id", "unknown")
    intent = state.get("trip_intent", {})
    counts = state.get("agent_call_count", {})

    def has_data(key: str) -> bool:
        """检查数据是否存在"""
        return bool(state.get(key))

    def need(key: str, default=True) -> bool:
        """检查意图开关"""
        if key == "hotel":
            return intent.get("hotel_intent") != "skip"
        return intent.get(key, default)

    def can_run(name: str, required: bool = True) -> bool:
        """判断是否该派发"""
        if not required:
            return False
        key_map = {
            "attraction": "attractions",
            "hotel": "hotels",
            "route": "routes",
            "weather": "weather_info",
        }
        data_key = key_map.get(name, name)
        if has_data(data_key):
            return False
        return counts.get(f"{name}_agent", 0) == 0

    if sum(counts.values()) >= MAX_AGENT_CALLS:
        logger.warning(f"[{rid}] 达到最大调用次数")
        return {"next": "planner_agent"}

    if any(e.get("fatal") for e in state.get("errors", [])):
        logger.warning(f"[{rid}] 存在致命错误")
        return {"next": "planner_agent"}

    first_wave = []
    if can_run("attraction", need("need_attraction_search", True)):
        first_wave.append("attraction_agent")
    if can_run("weather", need("need_weather", True)):
        first_wave.append("weather_agent")

    if intent.get("pre_selected_hotel") and can_run("hotel", need("hotel", True)):
        first_wave.append("hotel_agent")

    if first_wave:
        logger.info(f"[{rid}] 第一梯队派发: {first_wave}")
        return {"next": first_wave[0] if len(first_wave) == 1 else first_wave}

    second_wave = []
    if can_run("hotel", need("hotel", True)):
        second_wave.append("hotel_agent")
    if can_run("route", need("need_route", True)) and has_data("attractions"):
        second_wave.append("route_agent")

    if second_wave:
        logger.info(f"[{rid}] 第二梯队派发: {second_wave}")
        return {"next": second_wave[0] if len(second_wave) == 1 else second_wave}

    logger.info(f"[{rid}] 数据已齐全，进入 Planner")
    return {"next": "planner_agent"}


class Supervisor:
    """保留类封装以兼容 GraphBuilder"""

    def __init__(self, llm=None):
        pass

    async def decide(self, state: AgentState) -> dict:
        return await _supervisor_logic(state)

    def get_node(self):
        async def node(state: AgentState) -> dict:
            return await self.decide(state)

        return node
