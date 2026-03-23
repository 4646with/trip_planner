"""规则路由引擎 - 纯逻辑，零LLM调用"""

import logging
from typing import Dict, Any, List, Union
from .schemas.state import AgentState

logger = logging.getLogger(__name__)

MAX_AGENT_CALLS = 6


def intent_based_router(state: AgentState) -> Dict[str, Any]:
    """
    基于意图标志位的规则路由（首次决策）

    返回：{"next": ["attraction_agent", "weather_agent", ...]}
    """
    rid = state.get("request_id", "unknown")
    intent = state.get("trip_intent", {})

    agents = []

    if intent.get("need_attraction_search", True):
        agents.append("attraction_agent")

    if intent.get("need_weather", True):
        agents.append("weather_agent")

    if intent.get("hotel_intent") != "skip":
        agents.append("hotel_agent")

    if intent.get("need_route", True):
        agents.append("route_agent")

    logger.info(f"[{rid}] 规则路由: {agents}")

    if not agents:
        return {"next": "planner_agent"}

    return {"next": agents if len(agents) > 1 else agents[0]}


def fault_check_router(state: AgentState) -> Dict[str, Any]:
    """
    规则容错检查（后续决策）- 梯队感知 + 幂等保护版本

    第一梯队：attraction_agent, weather_agent（无依赖）
    第二梯队：hotel_agent, route_agent（依赖 attractions 数据）

    幂等保护机制：每个 agent 只派发一次，靠 call_count 做唯一可信来源
    """
    rid = state.get("request_id", "unknown")
    intent = state.get("trip_intent", {})
    call_count = state.get("agent_call_count", {})

    logger.info(f"[{rid}] fault_check_router | call_count={call_count}")

    total_calls = sum(call_count.values())
    if total_calls >= MAX_AGENT_CALLS:
        logger.error(f"[{rid}] 达到最大调用次数，强制进入 planner_agent")
        return {"next": "planner_agent"}

    errors = state.get("errors", [])
    fatal_errors = [e for e in errors if e.get("fatal")]

    if fatal_errors:
        logger.error(f"[{rid}] 检测到致命错误，强制进入 planner_agent")
        return {"next": "planner_agent"}

    if len(errors) > 2:
        logger.warning(f"[{rid}] 错误过多，降级到 planner_agent")
        return {"next": "planner_agent"}

    has_attractions = len(state.get("attractions", [])) > 0
    has_weather = bool(state.get("weather_info"))
    has_hotels = len(state.get("hotels", [])) > 0
    has_routes = len(state.get("routes", [])) > 0

    need_attractions = intent.get("need_attraction_search", True)
    need_weather = intent.get("need_weather", True)
    need_hotel = intent.get("hotel_intent") != "skip"
    need_route = intent.get("need_route", True)

    first_wave_done = (not need_attractions or has_attractions) and (
        not need_weather or has_weather
    )

    if not first_wave_done:
        retry = []
        if need_attractions and not has_attractions:
            if call_count.get("attraction_agent", 0) == 0:
                retry.append("attraction_agent")
        if need_weather and not has_weather:
            if call_count.get("weather_agent", 0) == 0:
                retry.append("weather_agent")

        if retry:
            logger.info(f"[{rid}] 第一梯队未完成，首次派发: {retry}")
            return {"next": retry if len(retry) > 1 else retry[0]}
        else:
            logger.info(f"[{rid}] 第一梯队已派发过，等待结果中...")
            return {"next": "planner_agent"}

    second_wave = []

    if need_hotel and not has_hotels and has_attractions:
        if call_count.get("hotel_agent", 0) == 0:
            second_wave.append("hotel_agent")
        else:
            logger.info(f"[{rid}] hotel_agent 已派发过，不再重试")

    if need_route and not has_routes and has_attractions:
        if call_count.get("route_agent", 0) == 0:
            second_wave.append("route_agent")
        else:
            logger.info(f"[{rid}] route_agent 已派发过，不再重试")
    elif need_route and not has_attractions:
        logger.info(f"[{rid}] 需要路线规划但没有景点数据，跳过 route_agent")

    if second_wave:
        logger.info(f"[{rid}] 启动第二梯队: {second_wave}")
        return {"next": second_wave if len(second_wave) > 1 else second_wave[0]}

    logger.info(f"[{rid}] 所有数据收集完毕，进入 planner_agent")
    return {"next": "planner_agent"}


def smart_router(state: AgentState) -> Dict[str, Any]:
    """
    智能路由器：自动选择规则路由或容错检查

    首次调用：使用 intent_based_router
    后续调用：使用 fault_check_router
    """
    total_calls = sum(state.get("agent_call_count", {}).values())

    if total_calls == 0:
        return intent_based_router(state)
    else:
        return fault_check_router(state)
