"""Map Agents 系统主入口 - 重构版

这是多智能体系统的唯一入口，负责：
1. 系统初始化和资源管理
2. 对外暴露统一的接口
3. 协调各个模块的工作
"""

import logging
from typing import Optional

from langchain_core.messages import HumanMessage

from ..services.llm_service import get_llm
from ..models.schemas import TripRequest, TripPlan
from ..services.mcp_tools import initialize_mcp_tools, cleanup_mcp_tools
from .supervisor import Supervisor
from .workers import WorkerExecutor as WorkerManager, Planner
from .graph.builder import GraphBuilder
from .utils.parsers import parse_and_build_plan

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
        self._supervisor = None
        self._worker_manager = None
        self._planner = None

    async def initialize(self) -> None:
        """
        初始化 MCP 工具与大模型

        必须在首次使用系统前调用
        """
        if self._initialized:
            logger.info("系统已初始化，跳过")
            return

        logger.info("正在初始化 MCP 工具...")
        await initialize_mcp_tools()

        logger.info("正在初始化 LLM...")
        self._llm = get_llm()

        logger.info("正在初始化 Supervisor...")
        self._supervisor = Supervisor(self._llm)

        logger.info("正在初始化 Workers...")
        self._worker_manager = WorkerManager(self._llm)

        logger.info("正在初始化 Planner...")
        self._planner = Planner(self._llm)

        logger.info("正在构建 StateGraph...")
        builder = GraphBuilder(
            supervisor_node=self._supervisor.get_node(),
            attraction_node=self._worker_manager.get_node_func("attraction"),
            weather_node=self._worker_manager.get_node_func("weather"),
            hotel_node=self._worker_manager.get_node_func("hotel"),
            route_node=self._worker_manager.get_node_func("route"),
            planner_node=self._planner.get_node(),
        )
        self._graph = builder.build()

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
        self._supervisor = None
        self._worker_manager = None
        self._planner = None
        logger.info("✅ 系统资源已清理")

    def _build_initial_state(self, request: TripRequest) -> dict:
        """
        构建初始状态

        Args:
            request: 旅行请求

        Returns:
            初始状态字典
        """
        # 构建初始消息
        initial_message = f"我要去{request.city}玩{request.travel_days}天。"
        if request.free_text_input:
            initial_message += f" 额外要求：{request.free_text_input}"

        return {
            "messages": [HumanMessage(content=initial_message)],
            "city": request.city,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "travel_days": request.travel_days,
            "transportation": request.transportation,
            "accommodation": request.accommodation,
            "preferences": request.preferences,
            "free_text_input": request.free_text_input,
            "next": "supervisor",
            "agent_call_count": {},
            "agent_results": {},
            "attractions": [],
            "weather_info": [],
            "hotels": [],
            "routes": [],
            "final_plan": {},
        }

    def plan_trip(self, request: TripRequest) -> TripPlan:
        """
        规划旅行（同步接口）

        Args:
            request: 旅行请求

        Returns:
            旅行计划
        """
        if not self._initialized or self._graph is None:
            raise RuntimeError("系统未初始化，请先调用 initialize()")

        # 构建初始状态
        initial_state = self._build_initial_state(request)

        logger.info("=" * 60)
        logger.info("🚀 启动多智能体协作（同步模式）...")
        logger.info("=" * 60)

        # 执行图
        final_state = self._graph.invoke(initial_state)

        logger.info("=" * 60)
        logger.info("✅ 规划完成")
        logger.info("=" * 60)

        # 解析结果
        return parse_and_build_plan(final_state.get("final_plan"), request)

    async def plan_trip_async(self, request: TripRequest) -> TripPlan:
        """
        规划旅行（异步接口）

        Args:
            request: 旅行请求

        Returns:
            旅行计划
        """
        if not self._initialized or self._graph is None:
            raise RuntimeError("系统未初始化，请先调用 initialize()")

        # 构建初始状态
        initial_state = self._build_initial_state(request)

        logger.info("=" * 60)
        logger.info("🚀 启动多智能体协作（异步模式）...")
        logger.info("=" * 60)

        # 异步执行图
        final_state = await self._graph.ainvoke(initial_state)

        logger.info("=" * 60)
        logger.info("✅ 规划完成")
        logger.info("=" * 60)

        # 解析结果
        return parse_and_build_plan(final_state.get("final_plan"), request)

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
