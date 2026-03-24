"""Workers 模块 - 纯函数流水线版

重构特性：
1. 彻底移除面向对象基类和注册表
2. 单次 Tool Calling，0 循环
3. 丰富的 Print 日志，开发维护体验拉满
"""

import asyncio
import json
import logging
import sys
import re as regex_module
from functools import wraps
from typing import Dict, Any

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .schemas.state import AgentState
from ..services.mcp_tools import get_mcp_manager, AmapTools
from .tools import web_search as original_web_search
from ..services.llm_service import get_llm

# ==========================================
# 专业日志配置 (穿透 Uvicorn 屏蔽，带颜色与时间戳)
# ==========================================
logger = logging.getLogger("trip_workers")
logger.setLevel(logging.INFO)

# 避免 FastAPI 热重载时重复添加 Handler
if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    # ANSI 颜色转义码：青色时间 | 蓝色模块名 | 绿色/黄色/红色级别
    formatter = logging.Formatter(
        fmt="\033[36m%(asctime)s\033[0m | \033[1;34m%(name)s\033[0m | \033[1;32m%(levelname)-7s\033[0m | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    # 核心：禁止日志冒泡给 Root Logger，防止被 Uvicorn 吃掉
    logger.propagate = False

llm = get_llm()

# ==========================================
# 1. 业务增强工具 & 装饰器
# ==========================================

_WEB_SEARCH_SUFFIXES = {
    "attraction": " 真实体验 避坑 拍照点位 游玩时长",
    "hotel": " 必吃榜 真实评价 排队情况 适合拍照吗",
}


def create_enhanced_web_search(agent_type: str) -> StructuredTool:
    """增强版 web_search 工具，自动补全体感类后缀"""
    suffix = _WEB_SEARCH_SUFFIXES.get(agent_type, "")

    async def enhanced_web_search(query: str) -> str:
        try:
            enhanced_query = query + suffix
            logger.info(
                f"[{agent_type}] 🔍 web_search 增强: '{query}' -> '{enhanced_query}'"
            )
            return await original_web_search.ainvoke({"query": enhanced_query})
        except Exception as e:
            logger.error(f"[{agent_type}] ❌ web_search 失败: {e}")
            return ""

    class EnhancedWebSearchInput(BaseModel):
        query: str = Field(..., description="搜索查询")

    return StructuredTool(
        name="web_search",
        description=original_web_search.description,
        func=None,
        coroutine=enhanced_web_search,
        args_schema=EnhancedWebSearchInput,
    )


def with_retry_and_log(func):
    """统一异常处理和专业日志打印装饰器"""

    @wraps(func)
    async def wrapper(state: AgentState, *args, **kwargs):
        agent_name = func.__name__.replace("_node", "")
        logger.info(f"[{agent_name}] 🚀 开始执行...")

        max_retries = 1
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                result = await func(state, *args, **kwargs)
                logger.info(f"[{agent_name}] ✅ 执行成功，拿到数据！")
                return result
            except Exception as last_error:
                error_str = str(last_error)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait_match = regex_module.search(r"retry in ([\d.]+)s", error_str)
                    wait_time = float(wait_match.group(1)) + 1 if wait_match else 10.0
                    logger.warning(
                        f"[{agent_name}] ⚠️ 触发限流，等待 {wait_time:.1f}s (尝试 {attempt}/{max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"[{agent_name}] ❌ 执行失败: {error_str[:200]}")
                    break

        output_key = "weather_info" if "weather" in agent_name else f"{agent_name}s"
        return {
            output_key: [],
            "agent_call_count": {
                **state.get("agent_call_count", {}),
                f"{agent_name}_agent": 1,
            },
            "errors": [
                {
                    "agent": f"{agent_name}_agent",
                    "error": f"执行失败: {str(last_error)}"
                    if last_error
                    else "未知错误",
                    "fatal": False,
                }
            ],
        }

    return wrapper


def build_worker_context(
    state: AgentState, worker_type: str, include_intent: bool = True
) -> str:
    """构建极简上下文"""
    city = state.get("city", "未知")
    days = state.get("travel_days", 1)
    context = f"【任务】目的地: {city}，时长: {days}天。\n"

    if include_intent:
        intent = state.get("trip_intent", {})
        context += f"【偏好】预算级别: {intent.get('budget_level', '未指定')}。\n"
        if tactical := intent.get("tactical_instructions", {}).get(worker_type):
            context += f"【🚨 战术约束】: {tactical}\n"
    return context


def _safe_parse_json(raw_str: str) -> list:
    """处理大模型返回的 JSON"""
    if isinstance(raw_str, str):
        try:
            parsed = json.loads(raw_str)
            return parsed if isinstance(parsed, list) else [parsed]
        except:
            return []
    return raw_str if isinstance(raw_str, list) else [raw_str]


# ==========================================
# 2. 纯函数节点区
# ==========================================


@with_retry_and_log
async def hotel_agent_node(state: AgentState) -> Dict[str, Any]:
    logger.info("🏨 [hotel_agent] 正在向大模型请求搜索参数...")
    tools = list(
        get_mcp_manager().get_tools_by_names(
            [AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL]
        )
    )
    tools.append(create_enhanced_web_search("hotel"))
    llm_with_tools = llm.bind_tools(tools)

    sys_msg = SystemMessage(
        "你是一个专业的酒店检索助手。请严格根据上下文约束调用工具，获取酒店信息。"
    )
    human_msg = HumanMessage(build_worker_context(state, "hotel"))

    ai_msg = await llm_with_tools.ainvoke([sys_msg, human_msg])

    results = []
    if ai_msg.tool_calls:
        tool_call = ai_msg.tool_calls[0]
        logger.info(
            f"🏨 [hotel_agent] 决定调用工具: {tool_call['name']}, 参数: {tool_call['args']}"
        )
        tool_func = next((t for t in tools if t.name == tool_call["name"]), tools[0])
        raw_res = await tool_func.ainvoke(tool_call["args"])
        results = _safe_parse_json(raw_res)
    else:
        logger.warning("🏨 [hotel_agent] 大模型未触发任何工具调用。")

    return {
        "hotels": results,
        "agent_call_count": {**state.get("agent_call_count", {}), "hotel_agent": 1},
    }


@with_retry_and_log
async def attraction_agent_node(state: AgentState) -> Dict[str, Any]:
    logger.info("🎡 [attraction_agent] 正在向大模型请求搜索参数...")
    tools = list(
        get_mcp_manager().get_tools_by_names(
            [AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL]
        )
    )
    tools.append(create_enhanced_web_search("attraction"))
    llm_with_tools = llm.bind_tools(tools)

    sys_msg = SystemMessage(
        "你是一个专业的景点检索助手。请根据上下文约束调用工具搜索景点。"
    )
    human_msg = HumanMessage(build_worker_context(state, "attraction"))

    ai_msg = await llm_with_tools.ainvoke([sys_msg, human_msg])

    results = []
    if ai_msg.tool_calls:
        tool_call = ai_msg.tool_calls[0]
        logger.info(
            f"🎡 [attraction_agent] 决定调用工具: {tool_call['name']}, 参数: {tool_call['args']}"
        )
        tool_func = next((t for t in tools if t.name == tool_call["name"]), tools[0])
        raw_res = await tool_func.ainvoke(tool_call["args"])
        results = _safe_parse_json(raw_res)
    else:
        logger.warning("🎡 [attraction_agent] 大模型未触发任何工具调用。")

    return {
        "attractions": results,
        "agent_call_count": {
            **state.get("agent_call_count", {}),
            "attraction_agent": 1,
        },
    }


@with_retry_and_log
async def weather_agent_node(state: AgentState) -> Dict[str, Any]:
    logger.info("⛅ [weather_agent] 正在向大模型请求搜索参数...")
    tools = list(get_mcp_manager().get_tools_by_names([AmapTools.WEATHER]))
    llm_with_tools = llm.bind_tools(tools)

    sys_msg = SystemMessage("你是一个天气查询助手。请调用工具查询目的地天气。")
    human_msg = HumanMessage(
        build_worker_context(state, "weather", include_intent=False)
    )

    ai_msg = await llm_with_tools.ainvoke([sys_msg, human_msg])

    results = []
    if ai_msg.tool_calls:
        tool_call = ai_msg.tool_calls[0]
        logger.info(
            f"⛅ [weather_agent] 决定调用工具: {tool_call['name']}, 参数: {tool_call['args']}"
        )
        raw_res = await tools[0].ainvoke(tool_call["args"])
        results = _safe_parse_json(raw_res)

    return {
        "weather_info": results,
        "agent_call_count": {**state.get("agent_call_count", {}), "weather_agent": 1},
    }


@with_retry_and_log
async def route_agent_node(state: AgentState) -> Dict[str, Any]:
    attractions = state.get("attractions", [])
    if not attractions:
        logger.warning("🚗 [route_agent] 没有景点数据，跳过路线规划。")
        return {
            "routes": [],
            "agent_call_count": {**state.get("agent_call_count", {}), "route_agent": 1},
        }

    logger.info("🚗 [route_agent] 正在向大模型请求搜索参数...")
    locations = [
        f"{a.get('name', '未知')} ({a.get('location', '位置未知')})"
        for a in attractions[:4]
    ]
    points_str = " -> ".join(locations)

    tools = list(
        get_mcp_manager().get_tools_by_names(
            [AmapTools.DIRECTION_WALKING, AmapTools.DIRECTION_DRIVING]
        )
    )
    llm_with_tools = llm.bind_tools(tools)

    sys_msg = SystemMessage(
        "你是一个交通路线规划助手。请调用工具查询以下途经点序列的路线。"
    )
    context_str = build_worker_context(state, "route", include_intent=False)
    human_msg = HumanMessage(f"{context_str}\n【需规划的途经点序列】: {points_str}")

    ai_msg = await llm_with_tools.ainvoke([sys_msg, human_msg])

    results = []
    if ai_msg.tool_calls:
        tool_call = ai_msg.tool_calls[0]
        logger.info(
            f"🚗 [route_agent] 决定调用工具: {tool_call['name']}, 参数: {tool_call['args']}"
        )
        tool_func = next((t for t in tools if t.name == tool_call["name"]), tools[0])
        raw_res = await tool_func.ainvoke(tool_call["args"])
        results = _safe_parse_json(raw_res)

    return {
        "routes": results,
        "agent_call_count": {**state.get("agent_call_count", {}), "route_agent": 1},
    }


# ==========================================
# 3. 统一导出注册表
# ==========================================
WORKER_NODES = {
    "hotel_agent": hotel_agent_node,
    "attraction_agent": attraction_agent_node,
    "weather_agent": weather_agent_node,
    "route_agent": route_agent_node,
}
