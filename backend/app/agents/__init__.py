"""Agents 模块 - 多智能体旅行规划系统

重构后的模块化架构：
- schemas/: 状态和数据模型定义
- prompts/: 提示词配置
- utils/: 工具函数
- supervisor.py: Supervisor 决策逻辑
- workers.py: Worker Agent 实现
- graph/: StateGraph 构建
- main.py: 系统主入口
"""

# 主入口（对外暴露的主要接口）
from .main import (
    MapAgentsSystem,
    get_map_agents_system,
    initialize_map_agents_system,
    cleanup_map_agents_system,
)

# 类型定义
from .schemas.state import AgentState, RouteDecision

# 提示词（如需自定义）
from .prompts.agents import AgentPrompts

# 工具（如需扩展）
from .workers import WORKER_NODES
from .planner import Planner
from .supervisor import Supervisor

__all__ = [
    # 主接口
    "MapAgentsSystem",
    "get_map_agents_system",
    "initialize_map_agents_system",
    "cleanup_map_agents_system",
    # 类型
    "AgentState",
    "RouteDecision",
    # 提示词
    "AgentPrompts",
    # 组件（高级使用）
    "WORKER_NODES",
    "Planner",
    "Supervisor",
]
