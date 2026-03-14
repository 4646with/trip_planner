"""智能体状态定义 - 精简版 State 和 RouteDecision"""

import operator
from typing import Annotated, List, Literal, TypedDict, Optional, Union
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field


def merge_dicts(a: dict, b: dict) -> dict:
    """合并两个字典，用于处理并发更新"""
    result = a.copy()
    result.update(b)
    return result


class AgentState(TypedDict):
    """
    全图共享状态 - 精简版

    改进点：
    - 移除临时数据字段（attraction_data, weather_data 等）
    - 利用 messages 传递上下文和历史信息
    - 保留核心配置和决策字段
    - 支持并发路由
    """

    # 消息历史（核心通信机制）
    messages: Annotated[List[BaseMessage], operator.add]

    # 路由决策（支持单个或多个节点）
    next: Union[str, List[str]]

    # 旅行请求核心参数
    city: str
    start_date: str
    end_date: str
    travel_days: int
    transportation: str
    accommodation: str
    preferences: List[str]
    free_text_input: str

    # Agent 返回的结构化数据
    attractions: Annotated[list, operator.add]
    weather_info: Annotated[list, operator.add]
    hotels: Annotated[list, operator.add]
    routes: Annotated[list, operator.add]

    # 最终输出
    final_plan: dict

    # 调用计数（用于防重复调用）- 支持并发合并
    agent_call_count: Annotated[dict, merge_dicts]

    # Agent 执行结果状态（用于判断是否有有效结果）- 支持并发合并
    # 格式: {"agent_name": {"called": bool, "success": bool}}
    agent_results: Annotated[dict, merge_dicts]


class RouteDecision(BaseModel):
    """
    Supervisor 决策结果 - 强类型输出

    使用 Pydantic 模型确保 LLM 输出符合预期结构
    支持单个节点或多个节点（并发）
    """

    next: Union[str, List[str]] = Field(
        ..., description="下一步要调用的 Agent 名称，支持单个或列表"
    )
    reasoning: str = Field(..., description="决策理由和思考过程")
    parallel: bool = Field(default=False, description="是否并发执行多个节点")
