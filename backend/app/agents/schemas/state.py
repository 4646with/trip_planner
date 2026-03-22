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


def merge_errors(a: list, b: list) -> list:
    """合并错误列表，用于并发节点同时写入错误时不丢失"""
    return a + b


class AgentState(TypedDict):
    # ── 元信息 ──────────────────────────────────────────────
    schema_version: int
    request_id: str

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

    # 调用计数（用于防重复调用）
    agent_call_count: Annotated[dict, merge_dicts]

    # Agent 执行结果状态
    agent_results: Annotated[dict, merge_dicts]

    # ── 错误收集 ─────────────────────────────────────────────
    # 格式：[{"agent": "search_agent", "error": "未获取到有效数据", "fatal": False}]
    errors: Annotated[List[dict], merge_errors]
    
    # 意图分析结果
    trip_intent: dict


class TripIntent(BaseModel):
    """
    用户旅行意图的结构化表示。
    
    这个模型是整个系统的"合同"——意图节点负责填写它，
    所有后续节点（Supervisor、Workers、Planner）负责读取它。
    一旦这个模型被填写，系统就不再需要反复推断用户意图。
    """
    
    # ── 核心需求标志位（三级状态） ───────────────────────────────
    hotel_intent: Literal["need", "skip", "unknown"] = Field(
        default="unknown",
        description=(
            "酒店需求的三级状态。"
            "need=用户明确需要（或表单选了酒店类型且无冲突）；"
            "skip=用户明确不需要（'住哥哥家'/'朋友接待'/'不用订酒店'）；"
            "unknown=用户完全未提及，保守处理时等同于need"
        )
    )
    
    need_attraction_search: bool = Field(
        default=True,
        description=(
            "是否需要调用景点Agent搜索景点。"
            "若用户已明确列出所有想去的景点（given_attractions非空且用户说'就去这些'），则为False。"
            "大多数情况下为True，因为用户只是举例而非穷举。"
        )
    )
    
    need_weather: bool = Field(
        default=True,
        description="是否需要查询天气。极短途（当天返回）或用户明确说不需要时为False。"
    )
    
    need_route: bool = Field(
        default=True,
        description="是否需要规划景点间路线。行程只有一个景点时为False。"
    )
    
    # ── 用户已明确提供的信息 ────────────────────────────────────
    given_attractions: List[str] = Field(
        default_factory=list,
        description=(
            "用户在输入中明确提到想去的景点名称。"
            "注意：只有用户明确说'要去XX'才算。偏好标签（如'历史文化'）不能推断为具体景点。"
        )
    )
    
    # ── 特殊需求（这是关键词匹配做不到的，必须用LLM） ──────────
    special_requirements: List[str] = Field(
        default_factory=list,
        description=(
            "从自然语言中提取的特殊需求标签。"
            "这是意图节点最体现价值的地方——'我外婆要来'能被识别为'携带老人'，"
            "'带着娃'能被识别为'亲子游'，这是关键词匹配永远做不到的。"
            "可能的标签：['携带老人', '亲子游', '无障碍需求', '素食者', "
            "'轮椅使用者', '语言障碍', '晕车', '行动不便']"
        )
    )
    
    # ── 预算与风格 ──────────────────────────────────────────────
    budget_level: Literal["budget", "mid", "luxury", "unknown"] = Field(
        default="unknown",
        description=(
            "预算水平，优先从自由文本中判断，其次参考住宿偏好表单。"
            "budget=经济实惠；mid=中等消费；luxury=豪华不限预算；unknown=未明确说明"
        )
    )
    
    travel_style: List[str] = Field(
        default_factory=list,
        description=(
            "旅行风格标签，从用户的偏好标签和自由文本中综合提炼。"
            "例如：['文化历史', '美食探索', '休闲放松', '网红打卡', '自然徒步']"
        )
    )
    
    # ── 冲突检测 ────────────────────────────────────────────────
    has_conflict: bool = Field(
        default=False,
        description="用户的自由文本输入与表单选项是否存在矛盾。"
    )
    conflict_note: str = Field(
        default="",
        description=(
            "冲突说明，用于日志和调试。"
            "例如：'用户表单选了豪华型酒店，但自由文本说住哥哥家，以自由文本为准，hotel_intent=skip'"
        )
    )
    
    # ── 调试字段 ────────────────────────────────────────────────
    reasoning: str = Field(
        default="",
        description="意图分析的推理过程，仅用于调试，不影响业务逻辑。"
    )


class RouteDecision(BaseModel):
    next: Union[str, List[str]] = Field(
        ..., description="下一步要调用的 Agent 名称，支持单个或列表"
    )
    reasoning: str = Field(..., description="决策理由和思考过程")
    parallel: bool = Field(default=False, description="是否并发执行多个节点")
