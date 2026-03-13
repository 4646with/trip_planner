"""Graph Builder 模块 - StateGraph 构建"""

import logging
from typing import Callable, Dict, Any

from langgraph.graph import StateGraph, END

from ..schemas.state import AgentState

logger = logging.getLogger(__name__)


class GraphBuilder:
    """
    StateGraph 构建器
    
    职责：
    1. 定义和构建多智能体工作流图
    2. 配置节点和边的连接关系
    3. 提供编译后的可执行图
    """
    
    def __init__(
        self,
        supervisor_node: Callable,
        attraction_node: Callable,
        weather_node: Callable,
        hotel_node: Callable,
        route_node: Callable,
        planner_node: Callable,
    ):
        """
        初始化图构建器
        
        Args:
            supervisor_node: Supervisor 决策节点
            attraction_node: 景点搜索节点
            weather_node: 天气查询节点
            hotel_node: 酒店推荐节点
            route_node: 路线规划节点
            planner_node: 行程汇总节点
        """
        self.supervisor_node = supervisor_node
        self.attraction_node = attraction_node
        self.weather_node = weather_node
        self.hotel_node = hotel_node
        self.route_node = route_node
        self.planner_node = planner_node
        
        self._graph = None
    
    def build(self) -> StateGraph:
        """
        构建并编译 StateGraph
        
        Returns:
            编译后的 StateGraph 实例
        """
        print("开始构建 StateGraph...")
        
        # 创建工作流
        workflow = StateGraph(AgentState)
        
        # 添加节点
        workflow.add_node("supervisor", self.supervisor_node)
        workflow.add_node("attraction_agent", self.attraction_node)
        workflow.add_node("weather_agent", self.weather_node)
        workflow.add_node("hotel_agent", self.hotel_node)
        workflow.add_node("route_agent", self.route_node)
        workflow.add_node("planner_agent", self.planner_node)
        
        print("已添加所有节点")
        
        # 添加 Worker 到 Supervisor 的边（Worker 执行完后返回 Supervisor）
        for worker in ["attraction_agent", "weather_agent", "hotel_agent", "route_agent"]:
            workflow.add_edge(worker, "supervisor")
        
        print("已添加 Worker -> Supervisor 边")
        
        # 添加 Supervisor 的条件边（根据决策路由到不同节点）
        workflow.add_conditional_edges(
            "supervisor",
            lambda x: x["next"],  # 根据 state["next"] 决定路由
            {
                "attraction_agent": "attraction_agent",
                "weather_agent": "weather_agent",
                "hotel_agent": "hotel_agent",
                "route_agent": "route_agent",
                "planner_agent": "planner_agent"
            }
        )
        
        print("已添加 Supervisor 条件边")
        
        # Planner 执行完后结束
        workflow.add_edge("planner_agent", END)
        
        print("已添加 Planner -> END 边")
        
        # 设置入口点
        workflow.set_entry_point("supervisor")
        
        print("已设置入口点: supervisor")
        
        # 编译图
        self._graph = workflow.compile()
        
        print("StateGraph 构建完成")
        
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
