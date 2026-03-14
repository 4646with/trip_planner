"""Graph Builder 模块 - StateGraph 构建"""

import logging
from typing import Callable, Dict, Any, List, Union

from langgraph.graph import StateGraph, END
from langgraph.constants import Send

from ..schemas.state import AgentState

logger = logging.getLogger(__name__)


def parallel_router(state: AgentState) -> Union[str, List[Send]]:
    """
    并发路由函数

    如果 next 是列表，返回 Send 列表（fan-out）
    如果 next 是字符串，直接返回节点名
    """
    next_nodes = state.get("next", [])

    if isinstance(next_nodes, list) and len(next_nodes) > 1:
        # 并发模式：返回 Send 列表
        return [Send(node, state) for node in next_nodes]
    elif isinstance(next_nodes, list) and len(next_nodes) == 1:
        # 只有一个节点
        return next_nodes[0]
    elif isinstance(next_nodes, str):
        # 字符串模式
        return next_nodes
    else:
        # 默认路由到 planner
        return "planner_agent"


class GraphBuilder:
    """
    StateGraph 构建器 - 配置驱动版

    职责：
    1. 定义和构建多智能体工作流图
    2. 从 worker_nodes 动态配置节点和边的连接关系
    3. 支持并发路由（fan-out/fan-in）
    """

    def __init__(
        self,
        supervisor_node: Callable,
        planner_node: Callable,
        worker_nodes: Dict[str, Callable],
    ):
        """
        初始化图构建器

        Args:
            supervisor_node: Supervisor 决策节点
            planner_node: 行程汇总节点
            worker_nodes: Worker 节点字典，key为节点名，value为节点函数
        """
        self.supervisor_node = supervisor_node
        self.planner_node = planner_node
        self.worker_nodes = worker_nodes

        self._graph = None

    def build(self) -> StateGraph:
        """
        构建并编译 StateGraph

        Returns:
            编译后的 StateGraph 实例
        """
        print("开始构建 StateGraph...")

        workflow = StateGraph(AgentState)

        # 添加 Supervisor 和 Planner 节点
        workflow.add_node("supervisor", self.supervisor_node)
        workflow.add_node("planner_agent", self.planner_node)

        # ✅ 从 worker_nodes 自动添加所有 Worker 节点
        for name, node_func in self.worker_nodes.items():
            workflow.add_node(name, node_func)

        print(
            f"已添加 {len(self.worker_nodes) + 2} 个节点 (supervisor + planner + {len(self.worker_nodes)} workers)"
        )

        # ✅ 自动生成边：Worker -> Supervisor（所有Worker执行完后都回Supervisor）
        for worker_name in self.worker_nodes.keys():
            workflow.add_edge(worker_name, "supervisor")

        print(f"已添加 {len(self.worker_nodes)} 条 Worker -> Supervisor 边")

        # ✅ 使用并发路由器处理 Supervisor 的条件边
        # parallel_router 支持单个节点或多个节点（并发）
        routing_map = {name: name for name in self.worker_nodes.keys()}
        routing_map["planner_agent"] = "planner_agent"

        workflow.add_conditional_edges("supervisor", parallel_router, routing_map)

        print("已添加 Supervisor 并发条件边")

        # Planner -> END
        workflow.add_edge("planner_agent", END)

        print("已添加 Planner -> END 边")

        # 设置入口点
        workflow.set_entry_point("supervisor")

        print("已设置入口点: supervisor")

        # 编译图
        self._graph = workflow.compile()

        print("StateGraph 构建完成（支持并发路由）")

        return self._graph

    def get_graph(self) -> StateGraph:
        """
        获取已构建的图

        Returns:
            StateGraph 实例
        """
        if self._graph is None:
            raise RuntimeError("图尚未构建，请先调用 build()")
        return self._graph
