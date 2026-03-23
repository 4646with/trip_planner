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
from functools import wraps
from typing import Dict, List, Any, Optional

from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import create_react_agent

from .schemas.state import AgentState
from .schemas.agent_output import AttractionData, WeatherData, HotelData, RouteData
from .prompts.agents import AgentPrompts
from ..services.mcp_tools import get_mcp_manager, AmapTools
from .tools import web_search as original_web_search
from ..models.schemas import TripPlan

logger = logging.getLogger(__name__)


# ==========================================
# web_search 体感型包装器 - 自动补全后缀
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
        try:
            enhanced_query = query + suffix
            logger.info(
                f"[{agent_type}] web_search 增强: '{query}' -> '{enhanced_query}'"
            )
            return await original_web_search.ainvoke({"query": enhanced_query})
        except Exception as e:
            logger.error(f"[web_search] 失败: {e}")
            return ""

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
def build_route_context(worker: "BaseWorker", state: AgentState) -> str:
    """route_agent 专用上下文构建器"""
    attractions = state.get("attractions", [])
    if not attractions:
        return "\n【警告】当前还未获取到任何景点数据，请直接返回空路线结果。"

    places = []
    for a in attractions:
        name = a.get("name", "未知景点")
        lon = a.get("longitude") or (a.get("location") or {}).get("longitude")
        lat = a.get("latitude") or (a.get("location") or {}).get("latitude")

        if lon and lat:
            places.append(
                f"- {name}：坐标 {lon},{lat}（调用工具时origin/destination必须用此坐标）"
            )
        else:
            logger.warning(f"[route_agent] 景点 {name} 缺少坐标，跳过路线规划")

    if places:
        return (
            f"\n【重要路线规划数据】\n"
            f"以下是景点的精确坐标，调用 maps_direction_walking 等工具时，\n"
            f"origin 和 destination 参数必须使用'经度,纬度'格式的数字坐标，\n"
            f"绝对不能使用景点名称或地址文字：\n" + "\n".join(places)
        )
    else:
        return "\n【警告】景点数据缺少坐标信息，无法进行路线规划，请返回空路线。"


AGENT_REGISTRY = {
    "attraction": {
        "name": "attraction_agent",
        "prompt": AgentPrompts.ATTRACTION,
        "tools": [AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL],
        "output_key": "attractions",
        "use_enhanced_web_search": True,
        "validator": lambda item: AttractionData(**item).model_dump(),
    },
    "weather": {
        "name": "weather_agent",
        "prompt": AgentPrompts.WEATHER,
        "tools": [AmapTools.WEATHER],
        "output_key": "weather_info",
        "validator": lambda item: WeatherData(**item).model_dump(),
    },
    "hotel": {
        "name": "hotel_agent",
        "prompt": AgentPrompts.HOTEL,
        "tools": [AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL],
        "output_key": "hotels",
        "use_enhanced_web_search": True,
        "validator": lambda item: HotelData(**item).model_dump(),
    },
    "route": {
        "name": "route_agent",
        "prompt": AgentPrompts.ROUTE,
        "tools": [
            AmapTools.DIRECTION_WALKING,
            AmapTools.DIRECTION_DRIVING,
        ],
        "output_key": "routes",
        "validator": lambda item: RouteData(**item).model_dump(),
        "context_builder": build_route_context,
    },
}


def with_retry_and_log(func):
    """统一的异常处理和日志装饰器，支持 Gemini 429 流重试"""
    import re as regex_module

    @wraps(func)
    async def wrapper(self, state: AgentState, *args, **kwargs):
        agent_name = self.name
        print(f"[{agent_name}] 开始执行...")

        max_retries = 1  # 仅允许1次重试，避免阻塞信号量
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                result = await func(self, state, *args, **kwargs)
                print(f"[{agent_name}] 执行成功")
                return result
            except Exception as e:
                error_str = str(e)
                last_error = e

                # 检查是否是 429 限流错误 - 仅限流重试
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait_match = regex_module.search(r"retry in ([\d.]+)s", error_str)
                    if wait_match:
                        wait_time = float(wait_match.group(1)) + 1
                        print(
                            f"[{agent_name}] 触发限流，等待 {wait_time:.1f}s (尝试 {attempt}/{max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                        # 继续重试
                    else:
                        print(f"[{agent_name}] 429 限流，等待 10s")
                        await asyncio.sleep(10)
                        # 继续重试
                else:
                    # 其他错误不重试，直接失败
                    print(f"[{agent_name}] 执行失败: {error_str[:200]}")
                    import traceback

                    traceback.print_exc()
                    return {
                        "messages": state.get("messages", []),
                        self.output_key: [],
                        "agent_results": self._update_agent_results(
                            state, success=False
                        ),
                    }

        # 重试次数用尽，返回失败
        print(f"[{agent_name}] 重试次数用尽，执行失败")
        if last_error:
            import traceback

            traceback.print_exc()
        return {
            "messages": state.get("messages", []),
            self.output_key: [],
            "agent_results": self._update_agent_results(state, success=False),
        }

    return wrapper


class AgentFactory:
    """Worker Agent 管理器 - 极简版"""

    def __init__(self, llm):
        self.llm = llm
        self._agent_cache: Dict[str, Any] = {}

    def _build_cache_key(self, name: str, tools: List[BaseTool]) -> str:
        tool_names = tuple(sorted(t.name for t in tools))
        return f"{name}:{tool_names}"

    def get_or_create_agent(self, name: str, tools: List[BaseTool]) -> Any:
        key = self._build_cache_key(name, tools)

        if key in self._agent_cache:
            return self._agent_cache[key]

        logger.info(f"[{name}] 创建新 Agent 实例")
        agent = create_react_agent(model=self.llm, tools=tools)
        self._agent_cache[key] = agent
        return agent


def _safe_extract_json(text: str) -> dict:
    """稳定 JSON 提取（避免 regex 贪婪问题）"""
    stack = []
    start = None

    for i, c in enumerate(text):
        if c == "{":
            if not stack:
                start = i
            stack.append(c)
        elif c == "}":
            if stack:
                stack.pop()
                if not stack and start is not None:
                    try:
                        return json.loads(text[start : i + 1])
                    except Exception:
                        return {}

    return {}


def parse_agent_output(text: str) -> dict:
    data = _safe_extract_json(text)

    if not isinstance(data, dict):
        return {}

    for key in ["attractions", "weather_info", "hotels", "routes"]:
        if key in data and not isinstance(data[key], list):
            data[key] = [data[key]] if data[key] else []

    return data


class BaseWorker:
    """Worker 基类 - 纯粹流水线模式"""

    def __init__(
        self,
        manager: AgentFactory,
        name: str,
        base_prompt: str,
        tools: List[BaseTool],
        output_key: str,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.base_prompt = base_prompt
        self.output_key = output_key
        self.config = config or {}
        self.validator = self.config.get("validator")
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
        print(f"[{self.name}] 正在调用 LLM 和工具...", flush=True)

        city = state.get("city", "未知")
        print(f"[{self.name}] >>>>>>> 目的地: {city} <<<<<<", flush=True)

        pruned_messages = self._build_messages(state)
        response = await self.agent.ainvoke({"messages": pruned_messages})

        attractions = state.get("attractions", [])
        parsed = self._parse_and_validate(response, attractions)

        has_data = bool(parsed.get(self.output_key, []))

        print(f"[{self.name}] 解析结果: has_data={has_data}", flush=True)
        print(
            f"[{self.name}] 数据预览: {str(parsed.get(self.output_key, []))[:200]}...",
            flush=True,
        )

        return self._build_result(response, parsed, state, has_data)

    def _build_messages(self, state: AgentState) -> List[BaseMessage]:
        """构建输入消息"""
        system_msg = SystemMessage(content=self.base_prompt)
        user_original = state["messages"][0].content if state.get("messages") else "无"
        context = self._build_context(state)
        retry_context = self._build_retry_context(state)
        execution_directive = self._build_execution_directive(state)

        messages = [
            system_msg,
            HumanMessage(content=f"【用户原始需求】{user_original}"),
            HumanMessage(content=f"【当前任务上下文】{context}{retry_context}"),
        ]

        if execution_directive:
            messages.append(HumanMessage(content=execution_directive))

        return messages

    def _build_execution_directive(self, state: AgentState) -> Optional[str]:
        """
        根据 trip_intent 中的 pre_selected 字段，
        生成'执行模式'指令，覆盖默认的'推荐模式'行为。

        返回 None 表示走默认推荐模式。
        """
        intent = state.get("trip_intent", {})

        if self.name == "hotel_agent":
            pre_selected = intent.get("pre_selected_hotel")
            if pre_selected:
                return (
                    f"⚠️【执行模式 - 最高优先级】\n"
                    f"用户已明确指定酒店：「{pre_selected}」\n"
                    f"你的任务不是推荐，而是执行：\n"
                    f"1. 调用 maps_text_search，keywords='{pre_selected}', city=目标城市\n"
                    f"2. 取第一个结果的坐标、地址\n"
                    f"3. 直接输出该酒店信息，不要推荐其他酒店\n"
                    f"禁止：搜索其他酒店、给出替代方案、提及其他品牌"
                )

        elif self.name == "attraction_agent":
            pre_selected = intent.get("pre_selected_attractions", [])
            if pre_selected:
                names = "、".join(pre_selected)
                return (
                    f"⚠️【混合模式 - 最高优先级】\n"
                    f"用户已锁定景点：{names}\n"
                    f"你的任务：\n"
                    f"1. 先调用 maps_text_search 逐一获取这些景点的坐标和详情\n"
                    f"2. 如果行程天数 > 锁定的景点数量，可以补充推荐其他景点\n"
                    f"3. 优先推荐与锁定景点在同一区域的选择\n"
                    f"禁止：替换用户锁定的景点"
                )

        elif self.name == "restaurant_agent":
            pre_selected = intent.get("pre_selected_restaurants", [])
            if pre_selected:
                names = "、".join(pre_selected)
                return (
                    f"⚠️【混合模式 - 最高优先级】\n"
                    f"用户已指定餐厅：{names}\n"
                    f"你的任务：\n"
                    f"1. 先调用 maps_text_search 逐一获取这些餐厅的坐标、地址和评价\n"
                    f"2. 如果需要，可补充推荐其他餐厅\n"
                    f"禁止：替换用户指定的餐厅"
                )

        return None

    def _build_context(self, state: AgentState) -> str:
        """构建任务上下文"""
        city = state.get("city", "未知")
        has_attractions = len(state.get("attractions", [])) > 0
        has_hotels = len(state.get("hotels", [])) > 0
        has_weather = bool(state.get("weather_info"))

        context = (
            f"目的地: {city}, "
            f"交通方式: {state.get('transportation', '未知')}, "
            f"住宿偏好: {state.get('accommodation', '未知')}, "
            f"旅行日期: {state.get('start_date', '未知')} 至 {state.get('end_date', '未知')} ({state.get('travel_days', 0)}天), "
            f"已获取景点: {'是' if has_attractions else '否'}, "
            f"已获取酒店: {'是' if has_hotels else '否'}, "
            f"已获取天气: {'是' if has_weather else '否'}"
        )

        context_builder = self.config.get("context_builder")
        if context_builder:
            context += context_builder(self, state)

        return context

    def _build_retry_context(self, state: AgentState) -> str:
        """构建重试上下文"""
        call_count = state.get("agent_call_count", {}).get(self.name, 0)
        if call_count == 0:
            return ""

        my_errors = [e for e in state.get("errors", []) if e.get("agent") == self.name]
        recent_errors = my_errors[-3:] if len(my_errors) >= 3 else my_errors

        if not recent_errors:
            return ""

        error_lines = []
        for e in recent_errors:
            err_msg = e.get("error", "")[:200]
            error_lines.append(f"- ❌ {err_msg}")

        return (
            f"\n【重试说明 - 第{call_count + 1}次尝试】\n"
            + "之前执行失败记录：\n"
            + "\n".join(error_lines)
        )

    def _parse_and_validate(self, response: dict, attractions: list) -> dict:
        """解析响应并验证"""
        final_text = self._parse_response(response)

        if not final_text:
            logger.warning(f"[{self.name}] 空响应")
            return {}

        parsed = parse_agent_output(final_text)

        if self.name == "route_agent":
            valid_names = {a.get("name", "") for a in attractions if a.get("name")}
            routes = parsed.get(self.output_key, [])
            valid_routes = [
                r
                for r in routes
                if r.get("origin") in valid_names
                and r.get("destination") in valid_names
            ]
            if len(valid_routes) < len(routes):
                print(
                    f"[{self.name}] 过滤幻觉路线: {len(routes) - len(valid_routes)} 条",
                    flush=True,
                )
            parsed[self.output_key] = valid_routes

        if self.validator:
            output_key = self.output_key
            items = parsed.get(output_key, [])
            if isinstance(items, list):
                validated_items = []
                for item in items:
                    if isinstance(item, dict):
                        try:
                            validated = self.validator(item)
                            validated_items.append(validated)
                        except Exception as e:
                            logger.warning(f"[{self.name}] validator 失败: {e}")
                    else:
                        validated_items.append(item)
                parsed[output_key] = validated_items

        return parsed

    def _build_result(
        self, response: dict, parsed: dict, state: AgentState, has_data: bool
    ) -> Dict[str, Any]:
        """构建返回字典"""
        agent_results = self._update_agent_results(state, success=has_data)
        call_count = self._update_call_count(state)

        result_data = parsed.get(self.output_key, [])
        if not isinstance(result_data, list):
            result_data = []

        return {
            "messages": response.get("messages", []),
            self.output_key: result_data,
            "agent_results": agent_results,
            "agent_call_count": call_count,
        }


class WorkerExecutor:
    """动态 Worker 执行器 - 高扩展性与高并发设计"""

    def __init__(self, llm, max_concurrency: int = 15):
        """
        max_concurrency: 最大并发数
        - Gemini 免费版 RPM 5，建议 1-2
        - 智谱AI，建议 10-15
        """
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._manager = AgentFactory(llm)
        self._workers: Dict[str, BaseWorker] = {}

        print(f"[WorkerExecutor] 初始化完成，并发限制: {max_concurrency}")

        for agent_id, config in AGENT_REGISTRY.items():
            tools = list(get_mcp_manager().get_tools_by_names(config["tools"]))

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
                config=config,
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
                try:
                    return await worker_instance.execute(state)
                except Exception as e:
                    logger.error(f"[{worker_instance.name}] 崩溃: {e}")
                    return {
                        "messages": state.get("messages", []),
                        worker_instance.output_key: [],
                        "agent_results": {},
                        "agent_call_count": state.get("agent_call_count", {}),
                    }

        return dynamic_node


# ==========================================
# Planner 数据清洗工具
# ==========================================
def _extract_planner_fields(items: list, max_items: int = 5) -> list:
    """
    数据驱动的字段清洗：只保留 Planner 真正需要的字段。

    核心思路的转变：
    旧版是"截断字符串"——先保留所有字段，然后按字符数切断，
    导致 JSON 可能被截断成无效格式。

    新版是"过滤字段"——按字段名精确控制，确保每个保留的字段都完整，
    同时大幅减少不必要的数据量（如 photos、poi_id 等 Planner 用不到的字段）。

    这样做的好处是：数据量可控且可预期，不会出现 JSON 截断导致的解析错误。
    """
    # Planner 生成行程时真正需要的字段，其他字段是 Worker 内部使用的
    ESSENTIAL_KEYS = {
        "name",  # 景点/酒店名称，必须
        "address",  # 地址，用于地图标注
        "location",  # 经纬度，用于地图标注
        "visit_duration",  # 建议游览时长，用于时间安排
        "description",  # 描述，用于生成行程文案
        "category",  # 类别，用于分类展示
        "ticket_price",  # 门票价格，用于预算计算
        "rating",  # 评分，用于质量判断
        # 酒店专属字段
        "price_range",  # 价格区间，用于预算计算
        "distance",  # 距景点距离，用于推荐理由
    }

    cleaned = []
    for item in items[:max_items]:
        if not isinstance(item, dict):
            continue
        # 只保留 Planner 需要的字段，过滤掉 photos、poi_id 等无用字段
        cleaned_item = {k: v for k, v in item.items() if k in ESSENTIAL_KEYS}
        if cleaned_item.get("name"):  # 没有名称的数据无意义，跳过
            cleaned.append(cleaned_item)

    return cleaned


# ==========================================
# Planner 节点
# ==========================================
class Planner:
    """
    Planner Agent - 行程汇总生成器

    改动：
    1. 移除二次字符串截断，信任 _extract_planner_fields 的清洗结果
    2. 将 trip_intent 中的特殊需求注入 prompt，让 Planner 感知约束
    """

    def __init__(self, llm):
        self.structured_llm = llm.with_structured_output(TripPlan)

    async def generate(self, state: AgentState) -> Dict[str, Any]:
        logger.info("Planner 正在生成最终方案...")

        # ── 第一步：数据清洗（字段过滤，不是字符串截断）────────────
        # 使用数据驱动的清洗，而不是粗暴的字符串截断
        attractions_context = _extract_planner_fields(
            state.get("attractions", []), max_items=5
        )
        hotels_context = _extract_planner_fields(state.get("hotels", []), max_items=3)
        weather_context = state.get("weather_info", [])  # 天气数据本身就很小，无需清洗
        routes_context = _extract_planner_fields(state.get("routes", []), max_items=5)

        logger.info(
            f"Planner 清洗后数据量: "
            f"attractions={len(attractions_context)}, "
            f"hotels={len(hotels_context)}, "
            f"weather={len(weather_context)}, "
            f"routes={len(routes_context)}"
        )

        # ── 第二步：从 trip_intent 读取特殊需求 ────────────────────
        # 这是 Planner 感知"携带老人"、"亲子游"等约束的关键
        # 不需要 Planner 自己去读消息历史，意图节点已经归纳好了
        intent = state.get("trip_intent", {})
        accommodation = state.get("accommodation", "未知")
        preferences = state.get("preferences", [])
        special_requirements = intent.get("special_requirements", [])
        budget_level = intent.get("budget_level", "unknown")
        hotel_intent = intent.get("hotel_intent", "unknown")
        itinerary_style = intent.get("itinerary_style", "relaxed")
        hard_constraints = intent.get("hard_constraints", [])

        preferences_str = ", ".join(preferences) if preferences else "无"
        special_req_str = (
            ", ".join(special_requirements) if special_requirements else "无"
        )

        # 地理临近性约束（compact模式强制执行）
        if itinerary_style == "compact":
            geo_constraint = "【地理约束 - 强制】所有景点必须在步行15分钟或单次地铁可达范围内，不得安排跨越城市对角线的景点组合。优先选择同一商圈/区域内的景点。"
        else:
            geo_constraint = ""

        # 硬性约束（用户明确表达的所有具体要求）
        constraints_block = ""
        if hard_constraints:
            constraints_str = "\n".join(f"- {c}" for c in hard_constraints)
            constraints_block = f"""
⚠️【用户硬性约束 - 必须严格执行，不得忽略或替换】
{constraints_str}
"""

        # ── 第三步：构建 Planner 的输入消息 ────────────────────────
        planner_messages = [
            SystemMessage(content=AgentPrompts.PLANNER),
            HumanMessage(
                content=f"""
请根据以下结构化数据生成旅行计划。

【基本信息】
目标城市: {state.get("city", "未知")}
旅行日期: {state.get("start_date", "未知")} 至 {state.get("end_date", "未知")}
旅行天数: {state.get("travel_days", 0)}天
交通方式: {state.get("transportation", "未知")}
住宿偏好: {accommodation}
旅行偏好: {preferences_str}

【用户意图（请严格遵守）】
住宿需求: {
                    "用户明确不需要订酒店，请勿安排酒店费用"
                    if hotel_intent == "skip"
                    else "用户需要订酒店，请根据住宿偏好推荐"
                }
预算水平: {budget_level}
特殊需求: {special_req_str}
{
                    "（注意：有特殊需求，请在行程安排和建议中体现，例如选择无障碍设施、避免长途步行等）"
                    if special_requirements
                    else ""
                }

{geo_constraint}

{constraints_block}

【结构化数据（已清洗，字段完整）】
景点数据:
{json.dumps(attractions_context, ensure_ascii=False)}

天气数据（共{len(weather_context)}条，必须全部写入 weather_info 字段，不得省略）:
{json.dumps(weather_context, ensure_ascii=False)}

酒店数据:
{json.dumps(hotels_context, ensure_ascii=False)}

路线数据:
{json.dumps(routes_context, ensure_ascii=False)}

请严格遵守输出格式，不要包含任何 markdown 标记。
"""
            ),
        ]

        try:
            plan_obj = await self.structured_llm.ainvoke(planner_messages)
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
