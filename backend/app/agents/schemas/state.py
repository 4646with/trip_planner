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
        ),
    )

    need_attraction_search: bool = Field(
        default=True,
        description=(
            "是否需要调用景点Agent搜索景点。"
            "若用户已明确列出所有想去的景点（given_attractions非空且用户说'就去这些'），则为False。"
            "大多数情况下为True，因为用户只是举例而非穷举。"
        ),
    )

    need_weather: bool = Field(
        default=True,
        description="是否需要查询天气。极短途（当天返回）或用户明确说不需要时为False。",
    )

    need_route: bool = Field(
        default=True, description="是否需要规划景点间路线。行程只有一个景点时为False。"
    )

    # ── 用户已明确提供的信息 ────────────────────────────────────
    given_attractions: List[str] = Field(
        default_factory=list,
        description=(
            "用户在输入中明确提到想去的景点名称。"
            "注意：只有用户明确说'要去XX'才算。偏好标签（如'历史文化'）不能推断为具体景点。"
        ),
    )

    # ── 用户明确锁定的实体（执行模式触发条件）────────────────────
    pre_selected_hotel: Optional[str] = Field(
        default=None,
        description=(
            "用户明确指定的酒店名称（执行模式）。"
            "触发条件：用户用了排他性语气，如'就住XX'/'必须住XX'/'我已经订了XX'。"
            "此字段非空时，hotel_agent 不做推荐搜索，只做精确查询。"
            "例：'我一定要住全季酒店' → '全季酒店'"
        ),
    )

    pre_selected_attractions: List[str] = Field(
        default_factory=list,
        description=(
            "用户明确锁定要去的景点列表（混合模式）。"
            "触发条件：'就去这两个地方'/'行程就安排这几个'。"
            "系统必须先获取这些景点的详细信息，如有空余行程再补充推荐。"
        ),
    )

    pre_selected_restaurants: List[str] = Field(
        default_factory=list,
        description=(
            "用户明确指定的餐厅名称列表（混合模式）。"
            "触发条件：'就去那家老碗会'/'必须吃XX'。"
            "系统先精确查询指定餐厅，如有需要再补充推荐其他。"
        ),
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
        ),
    )

    # ── 预算与风格 ──────────────────────────────────────────────
    budget_level: Literal["budget", "mid", "luxury", "unknown"] = Field(
        default="unknown",
        description=(
            "预算水平，优先从自由文本中判断，其次参考住宿偏好表单。"
            "budget=经济实惠；mid=中等消费；luxury=豪华不限预算；unknown=未明确说明"
        ),
    )

    itinerary_style: Literal["compact", "relaxed"] = Field(
        default="relaxed",
        description=(
            "行程节奏。1-2天短途自动设为compact，"
            "要求所有景点集中在同一区域，步行或短程交通可达。"
            "3天以上可设为relaxed，允许跨区安排。"
        ),
    )

    travel_style: List[str] = Field(
        default_factory=list,
        description=(
            "旅行风格标签，从用户的偏好标签和自由文本中综合提炼。"
            "例如：['文化历史', '美食探索', '休闲放松', '网红打卡', '自然徒步']"
        ),
    )

    hard_constraints: List[str] = Field(
        default_factory=list,
        description=(
            "用户明确表达的所有硬性约束，用自然语言条目存储。"
            "这些约束必须被 Planner 严格遵守，不能被忽略或替换。"
            "捕捉原则：只要用户用了'就去这家'、'必须吃'、'不要'、'只想'、"
            "'一定要'这类强烈语气词，或者点名了具体的店/地点/活动，就应该提取。"
            "例子："
            "'我要吃老碗会，就那家西北菜' → ['午餐或晚餐必须安排老碗会（西北菜餐厅）']"
            "'不要安排博物馆，我不感兴趣' → ['行程中不得安排任何博物馆类景点']"
            "'第一天我想轻松一点' → ['第一天景点数量不超过1个，节奏宽松']"
            "'我朋友说蛇口海上世界的那个泰国菜很好' → ['建议在蛇口海上世界安排泰国餐厅']"
        ),
    )

    # ── 冲突检测 ────────────────────────────────────────────────
    has_conflict: bool = Field(
        default=False, description="用户的自由文本输入与表单选项是否存在矛盾。"
    )
    conflict_note: str = Field(
        default="",
        description=(
            "冲突说明，用于日志和调试。"
            "例如：'用户表单选了豪华型酒店，但自由文本说住哥哥家，以自由文本为准，hotel_intent=skip'"
        ),
    )

    # ── 调试字段 ────────────────────────────────────────────────
    reasoning: str = Field(
        default="", description="意图分析的推理过程，仅用于调试，不影响业务逻辑。"
    )

    @property
    def should_search_hotel(self) -> bool:
        return self.hotel_intent != "skip"

    @property
    def should_search_attractions(self) -> bool:
        return self.need_attraction_search

    @property
    def should_search_weather(self) -> bool:
        return self.need_weather

    @property
    def should_search_route(self) -> bool:
        return self.need_route

    @property
    def is_compact_trip(self) -> bool:
        return self.itinerary_style == "compact"


class RouteDecision(BaseModel):
    next: Union[str, List[str]] = Field(
        ..., description="下一步要调用的 Agent 名称，支持单个或列表"
    )
    reasoning: str = Field(..., description="决策理由和思考过程")
    parallel: bool = Field(default=False, description="是否并发执行多个节点")
