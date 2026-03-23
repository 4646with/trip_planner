"""Supervisor 模块 - 智能路由决策中心

职责：基于 AgentState（包含意图和历史调用次数）进行纯 Python 的状态机路由。
彻底弃用 LLM 路由，保证 100% 稳定性和极速并发。
"""

import logging
from typing import Dict, Any, List

from .schemas.state import AgentState
from .constants import MAX_AGENT_CALLS

logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, llm=None):
        self.llm = llm

    async def decide(self, state: AgentState) -> Dict[str, Any]:
        """
        核心路由决策逻辑（融合了原 rule_router 的 fault_check_router）

        第一梯队：attraction_agent, weather_agent（无依赖）
        第二梯队：hotel_agent, route_agent（依赖 attractions 数据）
        快通道模式：如果用户锁定了酒店，hotel_agent 可提前并发
        """
        rid = state.get("request_id", "unknown")
        intent = state.get("trip_intent", {})
        call_count = state.get("agent_call_count", {})

        logger.info(f"[{rid}] Supervisor 路由检查 | call_count={call_count}")

        total_calls = sum(call_count.values())
        if total_calls >= MAX_AGENT_CALLS:
            logger.error(
                f"[{rid}] 达到最大调用次数 ({MAX_AGENT_CALLS})，强制进入 planner_agent"
            )
            return {"next": "planner_agent"}

        errors = state.get("errors", [])
        if any(e.get("fatal") for e in errors):
            logger.error(f"[{rid}] 检测到致命错误，强制进入 planner_agent")
            return {"next": "planner_agent"}

        has_attractions = len(state.get("attractions", [])) > 0
        has_hotels = len(state.get("hotels", [])) > 0
        has_routes = len(state.get("routes", [])) > 0
        has_weather = state.get("weather_info") is not None

        need_attractions = intent.get("need_attraction_search", True)
        need_weather = intent.get("need_weather", True)
        need_hotel = intent.get("hotel_intent") != "skip"
        need_route = intent.get("need_route", True)

        hotel_can_run_early = bool(intent.get("pre_selected_hotel"))

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

            if hotel_can_run_early and need_hotel and not has_hotels:
                if call_count.get("hotel_agent", 0) == 0:
                    retry.append("hotel_agent")
                    logger.info(
                        f"[{rid}] 快通道：hotel_agent 因 pre_selected_hotel 提前并发"
                    )

            if retry:
                logger.info(f"[{rid}] 第一梯队派发（含快通道）: {retry}")
                return {"next": retry if len(retry) > 1 else retry[0]}
            else:
                logger.info(f"[{rid}] 第一梯队已派发过，等待结果中...")
                return {"next": "planner_agent"}

        second_wave = []

        if need_hotel and not has_hotels:
            if call_count.get("hotel_agent", 0) == 0:
                if hotel_can_run_early:
                    logger.info(f"[{rid}] hotel_agent 已在快通道中派发，跳过")
                else:
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

        logger.info(f"[{rid}] 所有数据收集完毕，进入 Planner")
        return {"next": "planner_agent"}

    def get_node(self):
        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            return await self.decide(state)

        return supervisor_node
