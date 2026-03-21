"""Workers 模块 - 配置化驱动 + 动态注册版
核心改进：
1. AGENT_REGISTRY: 配置化驱动，新增 Agent 只需填表
2. 动态注册: 自动实例化所有注册表中的 Worker
3. 高并发: Semaphore(40) 榨干 50 并发额度
4. 结构化输出: 所有 Agent 返回结构化 JSON 数据
"""

import asyncio
import json
import logging
import re
from functools import wraps
from typing import Dict, List, Any

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import create_react_agent

from .schemas.state import AgentState
from .prompts.agents import AgentPrompts
from ..services.mcp_tools import get_mcp_manager, AmapTools
from .tools import web_search as original_web_search
from ..models.schemas import TripPlan

logger = logging.getLogger(__name__)


# ==========================================
# web_search 体感型包装器 - 自动补全后缀
# ==========================================
_WEB_SEARCH_SUFFIXES = {
    "attraction": " 真实体验 避坑 拍照点位 游玩时长",
    "hotel": " 必吃榜 真实评价 排队情况 适合拍照吗",
}


def create_enhanced_web_search(agent_type: str) -> StructuredTool:
    """创建增强版 web_search 工具，自动补全体感类后缀"""
    suffix = _WEB_SEARCH_SUFFIXES.get(agent_type, "")

    async def enhanced_web_search(query: str) -> str:
        enhanced_query = query + suffix
        logger.info(f"[{agent_type}] web_search 增强: '{query}' -> '{enhanced_query}'")
        return await original_web_search.ainvoke({"query": enhanced_query})

    from pydantic import BaseModel

    enhanced_schema = type(
        "EnhancedWebSearchInput",
        (BaseModel,),
        {"query": (str, ...), "__annotations__": {"query": str}},
    )

    return StructuredTool(
        name="web_search",
        description=original_web_search.description,
        func=None,
        coroutine=enhanced_web_search,
        args_schema=enhanced_schema,
    )


# ==========================================
# Agent 注册表 - 新增 Agent 只需在此配置
# ==========================================
AGENT_REGISTRY = {
    "attraction": {
        "name": "attraction_agent",
        "prompt": AgentPrompts.ATTRACTION,
        "tools": [AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL],
        "output_key": "attractions",
        "use_enhanced_web_search": True,
    },
    "weather": {
        "name": "weather_agent",
        "prompt": AgentPrompts.WEATHER,
        "tools": [AmapTools.WEATHER],
        "output_key": "weather_info",
    },
    "hotel": {
        "name": "hotel_agent",
        "prompt": AgentPrompts.HOTEL,
        "tools": [AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL],
        "output_key": "hotels",
        "use_enhanced_web_search": True,
    },
    "route": {
        "name": "route_agent",
        "prompt": AgentPrompts.ROUTE,
        "tools": [
            AmapTools.DIRECTION_TRANSIT,
            AmapTools.DIRECTION_WALKING,
            AmapTools.DIRECTION_DRIVING,
            AmapTools.DIRECTION_BICYCLING,
        ],
        "output_key": "routes",
    },
}


def with_retry_and_log(func):
    """统一的异常处理和日志装饰器"""

    @wraps(func)
    async def wrapper(self, state: AgentState, *args, **kwargs):
        agent_name = self.name
        print(f"[{agent_name}] 开始执行...")
        try:
            result = await func(self, state, *args, **kwargs)
            print(f"[{agent_name}] 执行成功")
            return result
        except Exception as e:
            print(f"[{agent_name}] 执行失败: {str(e)}")
            import traceback

            traceback.print_exc()
            return {
                "messages": state.get("messages", []),
                self.output_key: [],  # 必须是列表类型
                "agent_results": self._update_agent_results(state, success=False),
            }

    return wrapper


class AgentFactory:
    """Worker Agent 管理器 - 极简版"""

    def __init__(self, llm):
        self.llm = llm
        self._agent_cache: Dict[str, Any] = {}

    def get_or_create_agent(self, name: str, tools: List[BaseTool]) -> Any:
        if name in self._agent_cache:
            return self._agent_cache[name]

        print(f"[{name}] 冷启动，正在创建全新的 Agent 实例...")
        agent = create_react_agent(model=self.llm, tools=tools)
        self._agent_cache[name] = agent
        return agent


def parse_agent_output(text: str) -> dict:
    """解析 Agent 返回的 JSON 输出"""
    try:
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            data = json.loads(json_match.group())
            # 确保所有值都是列表类型（LangGraph 的 Annotated[list, operator.add] 要求）
            for key in ["attractions", "weather_info", "hotels", "routes"]:
                if key in data and not isinstance(data[key], list):
                    # 如果是单个对象，包装成列表
                    data[key] = [data[key]] if data[key] else []
            return data
    except json.JSONDecodeError:
        pass

    return {"success": False, "data": {}, "raw_text": text}


def parse_and_validate(text: str, config: dict) -> list:
    """
    解析并验证 Agent 输出

    Args:
        text: Agent 返回的文本（包含 JSON）
        config: Agent 配置，必须包含 output_key

    Returns:
        验证后的数据列表，失败或无效时返回空列表
    """
    # 提取 JSON
    try:
        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            return []
        data = json.loads(json_match.group())
    except (json.JSONDecodeError, AttributeError):
        return []

    # 获取输出键
    output_key = config.get("output_key")
    if not output_key:
        return []

    # 获取数据列表
    items = data.get(output_key, [])
    if not isinstance(items, list):
        return []

    # 过滤空项
    valid_items = [item for item in items if item and isinstance(item, dict)]

    # 应用 validator（如果存在）
    validator = config.get("validator")
    if validator:
        validated_items = []
        for item in valid_items:
            try:
                validated = validator(item)
                if validated:
                    validated_items.append(validated)
            except Exception:
                continue
        valid_items = validated_items

    return valid_items


class BaseWorker:
    """Worker 基类 - 纯粹流水线模式"""

    def __init__(
        self,
        manager: AgentFactory,
        name: str,
        base_prompt: str,
        tools: List[BaseTool],
        output_key: str,
    ):
        self.name = name
        self.base_prompt = base_prompt
        self.output_key = output_key
        self.agent = manager.get_or_create_agent(name, tools)

    def _parse_response(self, response: dict) -> str:
        messages = response.get("messages", [])
        if messages:
            last_msg = messages[-1]
            return last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        return ""

    def _update_agent_results(self, state: AgentState, success: bool = True) -> dict:
        agent_results = state.get("agent_results", {}).copy()
        agent_results[self.name] = {
            "called": True,
            "success": success,
        }
        return agent_results

    def _update_call_count(self, state: AgentState) -> dict:
        counts = state.get("agent_call_count", {}).copy()
        counts[self.name] = counts.get(self.name, 0) + 1
        return counts

    @with_retry_and_log
    async def execute(self, state: AgentState) -> Dict[str, Any]:
        import sys

        print(f"[{self.name}] 正在调用 LLM 和工具...", flush=True)

        city_input = state.get("city", "未知")
        print(f"[{self.name}] >>>>>>> 原始目的地: {city_input} <<<<<<", flush=True)

        safe_state = {
            "city": city_input,
            "transportation": state.get("transportation", "未知"),
            "accommodation": state.get("accommodation", "未知"),
            "preferences": ", ".join(state.get("preferences", [])) or "无",
            "free_text_input": state.get("free_text_input", "无"),
            "start_date": state.get("start_date", "未知"),
            "end_date": state.get("end_date", "未知"),
            "travel_days": state.get("travel_days", 0),
        }

        full_prompt = self.base_prompt.format(**safe_state)

        system_msg = SystemMessage(content=full_prompt)

        messages = state.get("messages", [])
        invoke_state = {"messages": [system_msg] + messages}

        response = await self.agent.ainvoke(invoke_state)
        final_text = self._parse_response(response)

        print(f"[{self.name}] 原始返回: {final_text[:500]}...", flush=True)

        parsed = parse_agent_output(final_text)

        has_data = bool(parsed.get(self.output_key, []))

        print(
            f"[{self.name}] 解析结果: has_data={has_data}",
            flush=True,
        )
        print(
            f"[{self.name}] 数据预览: {str(parsed.get(self.output_key, []))[:200]}...",
            flush=True,
        )

        agent_results = self._update_agent_results(state, success=has_data)
        call_count = self._update_call_count(state)

        return {
            "messages": response["messages"],
            self.output_key: parsed.get(self.output_key, []),
            "agent_results": agent_results,
            "agent_call_count": call_count,
        }


class WorkerExecutor:
    """动态 Worker 执行器 - 高扩展性与高并发设计"""

    def __init__(self, llm, max_concurrency: int = 40):
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._manager = AgentFactory(llm)
        self._workers: Dict[str, BaseWorker] = {}

        for agent_id, config in AGENT_REGISTRY.items():
            tools = get_mcp_manager().get_tools_by_names(config["tools"])

            if config.get("use_enhanced_web_search", False):
                enhanced_search = create_enhanced_web_search(agent_id)
                tools.append(enhanced_search)
                logger.info(f"[{agent_id}] 已注入增强版 web_search 工具")

            self._workers[agent_id] = BaseWorker(
                manager=self._manager,
                name=config["name"],
                base_prompt=config["prompt"],
                tools=tools,
                output_key=config["output_key"],
            )

        logger.info(
            f"WorkerExecutor 初始化完成，动态加载了 {len(self._workers)} 个 Worker: {list(AGENT_REGISTRY.keys())}"
        )

    def get_node_func(self, agent_id: str):
        """动态生成 LangGraph 节点闭包函数"""
        if agent_id not in self._workers:
            raise ValueError(f"未知的 Agent ID: {agent_id}")

        worker_instance = self._workers[agent_id]

        async def dynamic_node(state: AgentState) -> Dict[str, Any]:
            async with self._semaphore:
                return await worker_instance.execute(state)

        return dynamic_node

    def get_all_node_funcs(self) -> Dict[str, callable]:
        """获取所有节点的动态函数"""
        return {
            agent_id: self.get_node_func(agent_id) for agent_id in AGENT_REGISTRY.keys()
        }


# ==========================================
# Planner 节点
# ==========================================
class Planner:
    """Planner Agent - 基于 schemas.py 中 TripPlan 强类型约束的行程汇总生成器"""

    def __init__(self, llm):
        self.structured_llm = llm.with_structured_output(TripPlan)

    async def generate(self, state: AgentState) -> Dict[str, Any]:
        logger.info("Planner 正在生成最终方案...")

        attractions = state.get("attractions", {})
        weather_info = state.get("weather_info", {})
        hotels = state.get("hotels", {})
        routes = state.get("routes", {})

        logger.info(
            f"Planner 收到的结构化数据: attractions={bool(attractions)}, weather={bool(weather_info)}, hotels={bool(hotels)}, routes={bool(routes)}"
        )

        accommodation = state.get("accommodation", "未知")
        preferences = state.get("preferences", [])
        preferences_str = ", ".join(preferences) if preferences else "无"

        planner_prompt = AgentPrompts.PLANNER.format(
            accommodation=accommodation, preferences=preferences_str
        )

        planner_messages = [
            SystemMessage(content=planner_prompt),
            HumanMessage(
                content=f"""
请根据以下结构化数据生成旅行计划。

目标城市: {state.get("city", "未知")}
旅行日期: {state.get("start_date", "未知")} 至 {state.get("end_date", "未知")}
旅行天数: {state.get("travel_days", 0)}天
住宿偏好: {accommodation}
旅行偏好: {preferences_str}

【结构化数据】:
景点数据: {json.dumps(attractions, ensure_ascii=False)[:500]}
天气数据: {json.dumps(weather_info, ensure_ascii=False)[:500]}
酒店数据: {json.dumps(hotels, ensure_ascii=False)[:300]}
路线数据: {json.dumps(routes, ensure_ascii=False)[:500]}

请严格遵守输出格式。
"""
            ),
        ]

        try:
            plan_obj = self.structured_llm.invoke(planner_messages)
            final_plan = plan_obj.model_dump()
            logger.info("Planner 成功生成强类型校验后的旅行计划。")
        except Exception as e:
            logger.error(f"Planner 生成或校验失败: {str(e)}")
            final_plan = TripPlan(
                city=state.get("city", "未知"),
                start_date=state.get("start_date", "未知"),
                end_date=state.get("end_date", "未知"),
                overall_suggestions="抱歉，行程生成过程中发生错误，请稍后重试。",
            ).model_dump()

        return {"final_plan": final_plan, "next": "end"}

    def get_node(self):
        async def planner_node(state: AgentState) -> Dict[str, Any]:
            return await self.generate(state)

        return planner_node


def get_agent_registry():
    return AGENT_REGISTRY


def get_worker_keys():
    """获取所有 Worker Agent 的 key（用于 get_node_func）"""
    return list(AGENT_REGISTRY.keys())


def get_worker_names():
    """获取所有 Worker Agent 的 name（用于节点命名）"""
    return [config["name"] for config in AGENT_REGISTRY.values()]
