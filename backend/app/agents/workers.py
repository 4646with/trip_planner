"""Workers 模块 - dataclass 注册表 + 验证驱动版

核心改进（相对上一版）：
1. AgentConfig dataclass：注册表配置在启动时即验证完整性，而非运行时崩溃
2. 单一注册中心：output_schema 内联至 AGENT_REGISTRY，删除 agent_output.OUTPUT_KEYS/SCHEMAS
3. parse_and_validate：Pydantic 逐条验证 + 可选业务清洗钩子
4. 自适应日志：失败率 >50% 或全部失败 -> error，有失败但未超阈值 -> warning
5. 启动检查：工具加载失败在 WorkerExecutor.__init__ 阶段即暴露
6. Planner 数据截断修复：用条数限制替代字符截断
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from functools import wraps
from typing import Dict, List, Any, Type, Callable, Optional

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ValidationError

from .schemas.state import AgentState
from .prompts.agents import AgentPrompts
from .utils.trip_store import get_trip_plan_store
from .schemas.agent_output import (
    AttractionData,
    WeatherData,
    HotelData,
    RouteData,
    SearchResults,
)
from ..services.mcp_tools import get_mcp_manager, AmapTools
from ..models.schemas import TripPlan

logger = logging.getLogger(__name__)


# ==========================================
# AgentConfig - 注册表配置类
# ==========================================


@dataclass
class AgentConfig:
    """
    Agent 配置 dataclass。

    用 dataclass 而非 TypedDict 的理由：
    - 实例化时即检查字段完整性（TypedDict 只做静态检查，运行时透明）
    - config.name 比 config["name"] 更安全，IDE 可跳转
    - data_cleaner 有默认值 None，选填
    """

    name: str
    prompt: str
    tools: List[str]
    output_key: str
    output_schema: Type[BaseModel]
    data_cleaner: Optional[Callable[[dict], dict]] = field(default=None)
    tool_schemas: Dict[str, type] = field(default_factory=dict)
    output_mapper: Optional[Callable[[str, "AgentConfig"], dict]] = field(default=None)
    idempotency_keys: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.idempotency_keys:
            self.idempotency_keys = [self.output_key]


def parse_search_results(text: str, config: AgentConfig) -> dict:
    """
    search_agent 专用解析器。
    同时验证 attractions 和 hotels 两个列表，
    分别写入对应的 state key。
    """
    try:
        json_match = re.search(r"\{[\s\S]*\}", text)
        parsed_data = json.loads(json_match.group()) if json_match else {}
    except json.JSONDecodeError as e:
        logger.error(f"[search_agent] JSON 解析失败: {e}")
        return {"attractions": [], "hotels": []}

    def _validate_list(items, schema, key_name):
        valid = []
        for idx, item in enumerate(items if isinstance(items, list) else []):
            try:
                if config.data_cleaner:
                    item = config.data_cleaner(item)
                validated = schema(**item)
                valid.append(validated.model_dump(exclude_none=True))
            except Exception as e:
                logger.warning(f"[search_agent] {key_name}[{idx}] 验证失败: {e}")
        return valid

    attractions = _validate_list(
        parsed_data.get("attractions", []), AttractionData, "attractions"
    )
    hotels = _validate_list(parsed_data.get("hotels", []), HotelData, "hotels")

    for name, items, raw_key in [
        ("attractions", attractions, "attractions"),
        ("hotels", hotels, "hotels"),
    ]:
        raw_count = len(parsed_data.get(raw_key, []))
        valid_count = len(items)
        if valid_count == 0 and raw_count > 0:
            logger.error(f"[search_agent] {name} 全部验证失败 ({raw_count}条)")
        elif valid_count < raw_count:
            logger.warning(f"[search_agent] {name}: {valid_count}/{raw_count} 条有效")
        else:
            logger.info(f"[search_agent] {name}: 全部 {valid_count} 条有效")

    return {"attractions": attractions, "hotels": hotels}


# ==========================================
# Agent 注册表 - 唯一配置源
# 新增 Agent 只需在此添加一个 AgentConfig 实例
# ==========================================

AGENT_REGISTRY: Dict[str, AgentConfig] = {
    "search": AgentConfig(
        name="search_agent",
        prompt=AgentPrompts.SEARCH,
        tools=[AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL],
        output_key="search_results",
        output_schema=SearchResults,
        output_mapper=parse_search_results,
        idempotency_keys=["attractions", "hotels"],
        data_cleaner=lambda x: {
            **x,
            "address": ""
            if x.get("address") in ("暂无", "无", None)
            else x.get("address", ""),
        },
    ),
    "weather": AgentConfig(
        name="weather_agent",
        prompt=AgentPrompts.WEATHER,
        tools=[AmapTools.WEATHER],
        output_key="weather_info",
        output_schema=WeatherData,
    ),
    "route": AgentConfig(
        name="route_agent",
        prompt=AgentPrompts.ROUTE,
        tools=[
            AmapTools.DIRECTION_TRANSIT,
            AmapTools.DIRECTION_WALKING,
            AmapTools.DIRECTION_DRIVING,
            AmapTools.DIRECTION_BICYCLING,
        ],
        output_key="routes",
        output_schema=RouteData,
    ),
}


# ==========================================
# 解析 + 验证
# ==========================================


def parse_and_validate(text: str, config: AgentConfig) -> List[dict]:
    """
    解析 LLM 输出文本并用 Pydantic 逐条验证。

    职责分层：
    - 本函数：JSON 提取、容错循环、日志上报
    - config.data_cleaner：业务层脏数据修复（可选，先于 Pydantic 执行）
    - config.output_schema（含 field_validator）：类型层脏数据修复

    日志策略（自适应）：
    - 全部失败 或 失败率 >50%  -> logger.error（系统性问题，需告警）
    - 有部分失败但未超阈值     -> logger.warning（偶发脏数据，可接受）
    - 全部成功                 -> logger.info
    """
    # Step 1: 提取 JSON
    try:
        json_match = re.search(r"\{[\s\S]*\}", text)
        parsed_data = json.loads(json_match.group()) if json_match else {}
    except json.JSONDecodeError as e:
        logger.error(f"[{config.name}] JSON 解析失败: {e} | 原始文本片段: {text[:300]}")
        return []

    raw_items = parsed_data.get(config.output_key, [])

    if not isinstance(raw_items, list):
        logger.error(
            f"[{config.name}] 期望 list，得到 {type(raw_items).__name__}，"
            f"请检查 output_key '{config.output_key}' 是否与 Prompt 中的字段名一致"
        )
        return []

    if not raw_items:
        logger.warning(f"[{config.name}] LLM 返回了空列表，可能是工具调用未返回数据")
        return []

    # Step 2: 逐条清洗 + 验证
    valid_items = []
    for idx, item in enumerate(raw_items):
        try:
            # 业务层清洗（先于 Pydantic，因为清洗可能修复 Pydantic 无法识别的格式）
            if config.data_cleaner:
                item = config.data_cleaner(item)

            # Pydantic 验证（field_validator 自动处理类型转换和范围夹紧）
            validated = config.output_schema(**item)
            valid_items.append(validated.model_dump(exclude_none=True))

        except ValidationError as e:
            logger.warning(
                f"[{config.name}] 第 {idx} 条数据验证失败 "
                f"({e.error_count()} 个字段错误): {e.errors()[0]}"
            )
        except Exception as e:
            logger.warning(f"[{config.name}] 第 {idx} 条数据处理异常: {e}")

    # Step 3: 自适应日志
    total = len(raw_items)
    valid = len(valid_items)
    invalid = total - valid

    if valid == 0:
        logger.error(
            f"[{config.name}] 全部 {total} 条数据验证失败，"
            f"下游 Planner 将基于空数据生成，请检查 LLM Prompt 或工具返回格式"
        )
    elif invalid > 0 and (invalid / total) > 0.5:
        logger.error(
            f"[{config.name}] 数据质量严重下降: {valid}/{total} 条有效 "
            f"(失败率 {invalid / total:.0%})，可能是 LLM 输出格式异常"
        )
    elif invalid > 0:
        logger.warning(
            f"[{config.name}] 验证完成: {valid}/{total} 条有效，{invalid} 条已跳过"
        )
    else:
        logger.info(f"[{config.name}] 验证完成: 全部 {total} 条有效")

    return valid_items


# ==========================================
# 装饰器
# ==========================================


def with_retry_and_log(func):
    """统一的异常处理和日志装饰器"""

    @wraps(func)
    async def wrapper(self, state: AgentState, *args, **kwargs):
        logger.info(f"[{self.name}] 开始执行...")
        try:
            result = await func(self, state, *args, **kwargs)
            logger.info(f"[{self.name}] 执行成功")
            return result
        except Exception as e:
            logger.error(f"[{self.name}] 执行失败: {e}", exc_info=True)
            return {
                "messages": state.get("messages", []),
                self.output_key: [],
                "agent_results": {
                    **state.get("agent_results", {}),
                    self.name: {"called": True, "success": False},
                },
            }

    return wrapper


# ==========================================
# AgentFactory
# ==========================================


class AgentFactory:
    """Worker Agent 工厂 - 带缓存的懒加载"""

    def __init__(self, llm):
        self.llm = llm
        self._agent_cache: Dict[str, Any] = {}

    def get_or_create_agent(self, name: str, tools: List[BaseTool]) -> Any:
        if name in self._agent_cache:
            return self._agent_cache[name]
        logger.info(f"[{name}] 冷启动，创建新的 ReAct Agent 实例...")
        agent = create_react_agent(model=self.llm, tools=tools)
        self._agent_cache[name] = agent
        return agent


# ==========================================
# BaseWorker
# ==========================================


class BaseWorker:
    """Worker 基类 - 配置驱动，验证驱动"""

    def __init__(
        self,
        manager: AgentFactory,
        config: AgentConfig,
        tools: List[BaseTool],  # 已由 WorkerExecutor 解析好的工具实例
    ):
        self.name = config.name
        self.base_prompt = config.prompt
        self.output_key = config.output_key
        self._config = config
        self.agent = manager.get_or_create_agent(config.name, tools)

    def _parse_response(self, response: dict) -> str:
        messages = response.get("messages", [])
        if messages:
            last_msg = messages[-1]
            return last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        return ""

    def _update_agent_results(self, state: AgentState, success: bool = True) -> dict:
        results = state.get("agent_results", {}).copy()
        results[self.name] = {"called": True, "success": success}
        return results

    def _update_call_count(self, state: AgentState) -> dict:
        counts = state.get("agent_call_count", {}).copy()
        counts[self.name] = counts.get(self.name, 0) + 1
        return counts

    @with_retry_and_log
    async def execute(self, state: AgentState) -> Dict[str, Any]:
        rid = state.get("request_id", "unknown")

        for key in self._config.idempotency_keys:
            if state.get(key):
                logger.info(f"[{rid}][{self.name}] '{key}' 已有数据，跳过")
                return {}

        city_input = state.get("city", "未知")
        logger.info(f"[{rid}][{self.name}] 开始执行，目的地: {city_input}")

        safe_state = {
            "city": city_input,
            "transportation": state.get("transportation", "未知"),
            "accommodation": state.get("accommodation", "未知"),
            "free_text_input": state.get("free_text_input", "无"),
            "start_date": state.get("start_date", "未知"),
            "end_date": state.get("end_date", "未知"),
            "travel_days": state.get("travel_days", 0),
        }

        full_prompt = self.base_prompt.format(**safe_state)
        invoke_state = {
            "messages": [SystemMessage(content=full_prompt)] + state.get("messages", [])
        }

        response = await self.agent.ainvoke(invoke_state)
        final_text = self._parse_response(response)
        logger.debug(f"[{rid}][{self.name}] LLM 返回片段: {final_text[:200]}")

        if self._config.output_mapper:
            state_updates = self._config.output_mapper(final_text, self._config)
            has_data = any(
                len(v) > 0 for v in state_updates.values() if isinstance(v, list)
            )
        else:
            valid_items = parse_and_validate(final_text, self._config)
            state_updates = {self.output_key: valid_items}
            has_data = len(valid_items) > 0

        errors = []
        if not has_data:
            errors.append(
                {
                    "agent": self.name,
                    "error": "未获取到有效数据",
                    "fatal": False,
                }
            )
            logger.warning(f"[{rid}][{self.name}] 未获取到有效数据，已写入 errors")

        return {
            "messages": response["messages"],
            **state_updates,
            "errors": errors,
            "agent_results": self._update_agent_results(state, success=has_data),
            "agent_call_count": self._update_call_count(state),
        }


# ==========================================
# WorkerExecutor - 动态注册 + 启动检查
# ==========================================


class WorkerExecutor:
    """动态 Worker 执行器"""

    def __init__(self, llm, max_concurrency: int = 40):
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._manager = AgentFactory(llm)
        self._workers: Dict[str, BaseWorker] = {}

        for agent_id, config in AGENT_REGISTRY.items():
            # 把字符串工具名解析为工具实例（全局唯一做这件事的地方）
            resolved_tools = get_mcp_manager().get_tools_by_names(config.tools)

            # 启动阶段检查：工具加载失败立即暴露，不等到运行时
            if not resolved_tools:
                raise RuntimeError(
                    f"Agent '{config.name}' 的工具加载失败，"
                    f"声明的工具: {config.tools}。"
                    f"请确认 MCP 服务器已初始化，且工具名称拼写正确。"
                )

            self._workers[agent_id] = BaseWorker(
                manager=self._manager,
                config=config,
                tools=resolved_tools,
            )

        logger.info(
            f"WorkerExecutor 初始化完成，加载了 {len(self._workers)} 个 Worker: "
            f"{list(AGENT_REGISTRY.keys())}"
        )

    def get_node_func(self, agent_id: str) -> Callable:
        """生成 LangGraph 节点闭包函数"""
        if agent_id not in self._workers:
            raise ValueError(
                f"未知的 Agent ID: '{agent_id}'，"
                f"可用的 ID: {list(self._workers.keys())}"
            )

        worker_instance = self._workers[agent_id]

        async def dynamic_node(state: AgentState) -> Dict[str, Any]:
            async with self._semaphore:
                return await worker_instance.execute(state)

        return dynamic_node

    def get_all_node_funcs(self) -> Dict[str, Callable]:
        return {agent_id: self.get_node_func(agent_id) for agent_id in AGENT_REGISTRY}


# ==========================================
# Planner
# ==========================================


class Planner:
    """Planner Agent - 强类型约束的行程汇总生成器"""

    def __init__(self, llm):
        self.structured_llm = llm.with_structured_output(TripPlan)

    async def generate(self, state: AgentState) -> Dict[str, Any]:
        rid = state.get("request_id", "unknown")
        logger.info(f"[{rid}] Planner 开始生成方案")

        attractions = state.get("attractions", [])
        weather_info = state.get("weather_info", [])
        hotels = state.get("hotels", [])
        routes = state.get("routes", [])
        city = state.get("city", "未知")
        travel_days = state.get("travel_days", 0)

        missing = []
        if not attractions:
            missing.append("景点")
        if not hotels:
            missing.append("酒店")
        if missing:
            logger.error(f"[{rid}] Planner 核心数据缺失: {missing}，执行降级")
            return {
                "final_plan": TripPlan(
                    city=city,
                    start_date=state.get("start_date", "未知"),
                    end_date=state.get("end_date", "未知"),
                    overall_suggestions=(
                        f"抱歉，未能获取到{'/'.join(missing)}数据，"
                        f"无法生成完整行程，请稍后重试。"
                    ),
                ).model_dump(),
                "next": "end",
            }

        historical_context = ""
        try:
            from ..utils.trip_store import get_trip_plan_store

            store = get_trip_plan_store()
            similar_plans = store.find_similar(city, travel_days, limit=2)
            if similar_plans:
                summaries = []
                for i, plan in enumerate(similar_plans, 1):
                    days_desc = " | ".join(
                        d.get("description", "") for d in plan.get("days", [])[:3]
                    )
                    suggestion = plan.get("overall_suggestions", "")[:100]
                    summaries.append(f"历史方案{i}：{days_desc}；建议：{suggestion}")
                historical_context = (
                    "\n\n【历史相似行程参考（仅供风格参考，请结合当前数据生成新方案）】\n"
                    + "\n".join(summaries)
                )
                logger.info(f"[{rid}] Planner 注入 {len(similar_plans)} 条历史参考")
        except Exception as e:
            logger.warning(f"[{rid}] 历史行程查询失败（不影响生成）: {e}")

        planner_messages = [
            SystemMessage(content=AgentPrompts.PLANNER),
            HumanMessage(
                content=f"""
请根据以下结构化数据生成旅行计划。

目标城市: {city}
旅行日期: {state.get("start_date", "未知")} 至 {state.get("end_date", "未知")}
旅行天数: {travel_days}天

【结构化数据】:
景点数据(前8条): {json.dumps(attractions[:8], ensure_ascii=False)}
天气数据: {json.dumps(weather_info, ensure_ascii=False)}
酒店数据(前5条): {json.dumps(hotels[:5], ensure_ascii=False)}
路线数据(前6条): {json.dumps(routes[:6], ensure_ascii=False)}
{historical_context}
请严格遵守输出格式。
"""
            ),
        ]

        try:
            plan_obj = await self.structured_llm.ainvoke(planner_messages)
            final_plan = plan_obj.model_dump()
            logger.info(f"[{rid}] Planner 生成成功")
        except Exception as e:
            logger.error(f"[{rid}] Planner 生成失败: {e}", exc_info=True)
            final_plan = TripPlan(
                city=city,
                start_date=state.get("start_date", "未知"),
                end_date=state.get("end_date", "未知"),
                overall_suggestions="抱歉，行程生成过程中发生错误，请稍后重试。",
            ).model_dump()

        return {"final_plan": final_plan, "next": "end"}

    def get_node(self):
        async def planner_node(state: AgentState) -> Dict[str, Any]:
            return await self.generate(state)

        return planner_node


# ==========================================
# 工具函数（供 main.py 调用）
# ==========================================


def get_agent_registry() -> Dict[str, AgentConfig]:
    return AGENT_REGISTRY


def get_worker_keys() -> List[str]:
    return list(AGENT_REGISTRY.keys())


# ==========================================
# 工具 Schema 注册
# ==========================================

from ..services.mcp_tools import register_tool_schemas

_extra_schemas: Dict[str, type] = {}
for config in AGENT_REGISTRY.values():
    _extra_schemas.update(config.tool_schemas)

if _extra_schemas:
    register_tool_schemas(_extra_schemas)


def get_worker_names() -> List[str]:
    return [config.name for config in AGENT_REGISTRY.values()]
