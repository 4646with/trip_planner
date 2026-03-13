"""智能体状态定义 - 精简版 State 和 RouteDecision"""

import operator
from typing import Annotated, List, Literal, TypedDict, Optional
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field


class AgentState(TypedDict):
    """
    全图共享状态 - 精简版
    
    改进点：
    - 移除临时数据字段（attraction_data, weather_data 等）
    - 利用 messages 传递上下文和历史信息
    - 保留核心配置和决策字段
    """
    # 消息历史（核心通信机制）
    messages: Annotated[List[BaseMessage], operator.add]
    
    # 路由决策
    next: str
    
    # 旅行请求核心参数
    city: str
    start_date: str
    end_date: str
    travel_days: int
    transportation: str
    accommodation: str
    preferences: List[str]
    free_text_input: str
    
    # 最终输出
    final_plan: dict
    
    # 调用计数（用于防重复调用）
    agent_call_count: dict
    
    # Agent 执行结果状态（用于判断是否有有效结果）
    # 格式: {"agent_name": {"called": bool, "success": bool, "has_data": bool}}
    agent_results: dict


class RouteDecision(BaseModel):
    """
    Supervisor 决策结果 - 强类型输出
    
    使用 Pydantic 模型确保 LLM 输出符合预期结构
    """
    next: Literal[
        "attraction_agent", 
        "weather_agent", 
        "hotel_agent", 
        "route_agent", 
        "planner_agent"
    ] = Field(
        ..., 
        description="下一步要调用的 Agent 名称"
    )
    reasoning: str = Field(
        ..., 
        description="决策理由和思考过程"
    )
