"""Graph Builder 模块 - 极简纯函数版"""

import logging
from typing import Union, List

from langgraph.graph import StateGraph, END
from langgraph.constants import Send
from langgraph.constants import START

from ..schemas.state import AgentState
from ..intent_analyzer import IntentAnalyzer
from ..supervisor import _supervisor_logic
from ..workers import WORKER_NODES
from ..planner import Planner

logger = logging.getLogger(__name__)


def route_from_supervisor(state: AgentState) -> Union[str, List[Send]]:
    """
    并发路由函数：将 Supervisor 的指令翻译成 LangGraph 的并发节点
    """
    next_destinations = state.get("next", "planner_agent")

    if isinstance(next_destinations, str):
        return next_destinations

    if isinstance(next_destinations, list):
        if len(next_destinations) == 1:
            return next_destinations[0]
        return [Send(node_name, state) for node_name in next_destinations]

    return "planner_agent"


def build_trip_graph(llm) -> StateGraph:
    """构建多智能体旅行规划图"""
    logger.info("开始构建极简版 StateGraph...")

    workflow = StateGraph(AgentState)

    # 1. 添加核心大脑节点
    intent_analyzer = IntentAnalyzer(llm)
    workflow.add_node("intent_analyzer", intent_analyzer.get_node())

    workflow.add_node("supervisor", _supervisor_logic)

    planner = Planner(llm)
    workflow.add_node("planner_agent", planner.get_node())

    # 2. 动态注册所有 Worker 节点
    for worker_name, worker_func in WORKER_NODES.items():
        workflow.add_node(worker_name, worker_func)

    # 3. 编排边（连线）
    workflow.add_edge(START, "intent_analyzer")
    workflow.add_edge("intent_analyzer", "supervisor")

    # 所有 Worker 节点执行完后，必须回到 supervisor 重新检查状态
    for worker_name in WORKER_NODES.keys():
        workflow.add_edge(worker_name, "supervisor")

    # 4. 配置条件并发边
    possible_destinations = list(WORKER_NODES.keys()) + ["planner_agent"]

    workflow.add_conditional_edges(
        "supervisor", route_from_supervisor, possible_destinations
    )

    # 出口
    workflow.add_edge("planner_agent", END)

    logger.info(f"图构建完成! 包含 {len(WORKER_NODES)} 个 Worker 节点。")
    return workflow.compile()
