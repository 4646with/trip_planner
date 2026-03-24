"""Map Agents 系统主入口 - 重构版

这是多智能体系统的唯一入口，负责：
1. 系统初始化和资源管理
2. 对外暴露统一的接口
3. 协调各个模块的工作
"""

import logging
import uuid
from typing import Optional

from langchain_core.messages import HumanMessage

from ..services.llm_service import get_llm
from ..models.schemas import TripRequest, TripPlan
from ..services.mcp_tools import initialize_mcp_tools, cleanup_mcp_tools
from .graph.builder import build_trip_graph
from .utils.parsers import parse_and_build_plan_async

logger = logging.getLogger(__name__)


class MapAgentsSystem:
    """
    地图智能体调度中枢 - 重构版

    这是系统的主要接口类，封装了所有内部实现细节。
    """

    def __init__(self):
        """初始化系统"""
        self._graph = None
        self._initialized = False
        self._llm = None

    async def initialize(self) -> None:
        """初始化 MCP 工具与大模型"""
        if self._initialized:
            logger.info("系统已初始化，跳过")
            return

        logger.info("正在初始化 MCP 工具...")
        await initialize_mcp_tools()

        logger.info("正在初始化 LLM...")
        self._llm = get_llm()

        logger.info("正在构建 StateGraph...")
        self._graph = build_trip_graph(self._llm)

        self._initialized = True
        logger.info("✅ 系统初始化完成")

    async def cleanup(self) -> None:
        """
        清理系统资源

        在应用关闭时调用
        """
        logger.info("正在清理系统资源...")
        await cleanup_mcp_tools()
        self._initialized = False
        self._graph = None
        self._intent_analyzer = None
        self._supervisor = None
        self._worker_manager = None
        self._planner = None
        logger.info("✅ 系统资源已清理")

    def _build_initial_state(self, request: TripRequest) -> dict:
        initial_message = f"我要去{request.city}玩{request.travel_days}天。"
        if request.free_text_input:
            initial_message += f" 额外要求：{request.free_text_input}"

        return {
            "schema_version": 1,
            "request_id": str(uuid.uuid4()),
            "messages": [HumanMessage(content=initial_message)],
            "next": "supervisor",
            "city": request.city,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "travel_days": request.travel_days,
            "transportation": request.transportation,
            "accommodation": request.accommodation,
            "preferences": request.preferences,
            "free_text_input": request.free_text_input,
            "agent_call_count": {},
            "agent_results": {},
            "errors": [],
            "attractions": [],
            "weather_info": [],
            "hotels": [],
            "routes": [],
            "final_plan": {},
            "trip_intent": {},
        }

    async def plan_trip(self, request: TripRequest) -> TripPlan:
        if not self._initialized or self._graph is None:
            raise RuntimeError("系统未初始化，请先调用 initialize()")

        initial_state = self._build_initial_state(request)
        rid = initial_state["request_id"]
        logger.info(f"[{rid}] 启动多智能体规划: {request.city} {request.travel_days}天")

        final_state = await self._graph.ainvoke(initial_state)

        errors = final_state.get("errors", [])
        if errors:
            logger.warning(f"[{rid}] 规划完成，但有 {len(errors)} 个错误: {errors}")
        else:
            logger.info(f"[{rid}] 规划完成，无错误")

        result = await parse_and_build_plan_async(
            final_state.get("final_plan"), request
        )

        return result

    async def plan_trip_async(self, request: TripRequest) -> TripPlan:
        """plan_trip 的别名，保持向后兼容"""
        return await self.plan_trip(request)

    @property
    def is_initialized(self) -> bool:
        """检查系统是否已初始化"""
        return self._initialized


# ==========================================
# 模块级单例暴露
# ==========================================

_system: Optional[MapAgentsSystem] = None


def get_map_agents_system() -> MapAgentsSystem:
    """
    获取地图智能体系统实例（懒加载）

    注意：返回的实例可能尚未初始化，如需确保初始化请使用 initialize_map_agents_system()

    Returns:
        MapAgentsSystem 实例
    """
    global _system
    if _system is None:
        _system = MapAgentsSystem()
    return _system


async def initialize_map_agents_system() -> MapAgentsSystem:
    """
    初始化地图智能体系统（应用启动时调用）

    Returns:
        已初始化的 MapAgentsSystem 实例
    """
    global _system
    if _system is None:
        _system = MapAgentsSystem()

    if not _system.is_initialized:
        await _system.initialize()

    return _system


async def cleanup_map_agents_system() -> None:
    """
    清理地图智能体系统（应用关闭时调用）
    """
    global _system
    if _system:
        await _system.cleanup()
        _system = None
