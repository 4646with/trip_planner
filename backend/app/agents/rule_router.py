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
    
    if intent.get("need_attractions", True):
        agents.append("attraction_agent")
    
    if intent.get("need_weather", True):
        agents.append("weather_agent")
    
    if intent.get("need_hotel", True):
        agents.append("hotel_agent")
    
    if intent.get("need_route", True):
        agents.append("route_agent")
    
    logger.info(f"[{rid}] 规则路由: {agents}")
    
    if not agents:
        return {"next": "planner_agent"}
    
    return {"next": agents if len(agents) > 1 else agents[0]}


def fault_check_router(state: AgentState) -> Dict[str, Any]:
    """
    规则容错检查（后续决策）
    
    检查运行时状态，决定重试或降级
    """
    rid = state.get("request_id", "unknown")
    
    total_calls = sum(state.get("agent_call_count", {}).values())
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
    
    intent = state.get("trip_intent", {})
    has_attractions = len(state.get("attractions", [])) > 0
    has_weather = bool(state.get("weather_info"))
    has_hotels = len(state.get("hotels", [])) > 0
    has_routes = len(state.get("routes", [])) > 0
    
    missing_agents = []
    
    if intent.get("need_attractions", True) and not has_attractions:
        missing_agents.append("attraction_agent")
    
    if intent.get("need_weather", True) and not has_weather:
        missing_agents.append("weather_agent")
    
    if intent.get("need_hotel", True) and not has_hotels:
        missing_agents.append("hotel_agent")
    
    if intent.get("need_route", True) and not has_routes:
        missing_agents.append("route_agent")
    
    if not missing_agents:
        logger.info(f"[{rid}] 所有必要数据已获取，进入 planner_agent")
        return {"next": "planner_agent"}
    
    if len(missing_agents) == 1:
        agent_name = missing_agents[0]
        call_count = state.get("agent_call_count", {}).get(agent_name, 0)
        
        if call_count < 2:
            logger.info(f"[{rid}] 重试失败的Agent: {agent_name}")
            return {"next": agent_name}
        else:
            logger.warning(f"[{rid}] Agent {agent_name} 已重试{call_count}次，放弃重试")
            return {"next": "planner_agent"}
    
    if len(missing_agents) > 1:
        logger.info(f"[{rid}] 多个Agent失败，尝试并发重试: {missing_agents}")
        return {"next": missing_agents}
    
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
