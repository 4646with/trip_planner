"""基于LangGraph的地图智能体系统 - 面向对象重构版

使用 MCP (Model Context Protocol) 集成高德地图服务
所有智能体共享同一个 MCP 服务器进程
"""

import operator
import asyncio
from typing import Annotated, List, Literal, TypedDict, Optional, Union
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field, ValidationError

from ..services.mcp_tools import get_mcp_manager, AmapTools, initialize_mcp_tools, cleanup_mcp_tools
from .tools import unsplash_image_search
from ..services.llm_service import get_llm
from ..models.schemas import TripRequest, TripPlan, Budget


# ==========================================
# 1. 状态与结构定义
# ==========================================

class AgentState(TypedDict):
    """全图共享状态"""
    messages: Annotated[List[BaseMessage], operator.add]
    next: str
    city: str
    start_date: str
    end_date: str
    travel_days: int
    transportation: str
    accommodation: str
    preferences: List[str]
    free_text_input: str
    final_plan: dict
    agent_call_count: dict
    attraction_data: str
    weather_data: str
    hotel_data: str
    route_data: str


class RouteDecision(BaseModel):
    """Supervisor 决策结果"""
    next: Literal["attraction_agent", "weather_agent", "hotel_agent", "route_agent", "planner_agent"]
    reasoning: str


# ==========================================
# 2. 提示词常量定义
# ==========================================

SUPERVISOR_PROMPT = """你是一个专业的旅行规划团队的监督者（Supervisor）。
你的团队有以下专家：
- attraction_agent: 负责搜索目的地景点信息
- weather_agent: 负责查询目的地天气
- hotel_agent: 负责搜索和推荐住宿酒店
- route_agent: 负责规划具体的交通路线
- planner_agent: 负责根据以上信息，最终汇总生成结构化的旅行行程单

当前任务信息：
目的地: {city}
交通方式: {transportation}
住宿要求: {accommodation}
额外要求: {free_text_input}

已调用次数: {agent_call_count}

你的任务：阅读对话历史，检查团队已经收集到了哪些信息。
1. 如果缺少景点、天气、酒店、交通中的任何一项（且用户没有明确说不需要），请将任务分配给对应的专家。
2. **重要**：仔细阅读"额外要求"，如果用户已说明住宿安排（如住亲戚家、朋友家等），则无需调用 hotel_agent。
3. **关键**：如果某个专家已被调用过（agent_call_count > 0），说明该信息已收集过，不要再重复调用同一专家。
4. 当所有必要信息都收集完毕后，必须输出 'planner_agent' 进行最终汇总。

请思考并决定下一步调用谁。
"""

ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。
任务：搜索 {city} 的景点，并返回详细的景点信息。

**重要要求**：
1. 必须调用 maps_text_search 工具搜索景点
2. 必须返回详细的景点信息，包括：
   - 景点名称
   - 景点地址
   - 门票价格（如果有）
   - 建议游览时间
   - 景点描述
3. 如果工具返回的结果中没有价格信息，请根据景点类型预估一个合理的价格
4. 不要只返回总结性文字，要返回具体的景点列表和详细信息

**输出格式示例**：
1. 景点名称：XXX
   地址：XXX
   门票价格：XX元
   建议游览时间：X小时
   描述：XXX

2. 景点名称：XXX
   ...
"""

WEATHER_AGENT_PROMPT = """你是天气查询专家。
任务：查询 {city} 的天气。
行为：直接调用 maps_weather 工具。无需额外解释，直接输出工具调用结果。
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。
任务：搜索 {city} 的酒店。
行为：直接调用 maps_text_search 工具。无需额外解释，直接输出工具调用结果。
"""

ROUTE_AGENT_PROMPT = """你是路线规划专家。
任务：规划 {city} 的交通路线。

**重要要求**：
1. 根据用户的交通方式需求，选择合适的路线规划工具：
   - 公交/地铁：调用 maps_direction_transit
   - 步行：调用 maps_direction_walking
   - 驾车：调用 maps_direction_driving
   - 骑行：调用 maps_direction_bicycling
2. 如果用户没有指定交通方式，默认使用 maps_direction_transit（公交/地铁）
3. 无需额外解释，直接输出工具调用结果。
"""

PLANNER_AGENT_PROMPT = """你是一个专业的旅行规划专家。
必须严格按照以下 JSON 结构输出，且不要包含任何 markdown 标记（即绝对不要用 ```json 包裹）：
{
  "city": "北京",
  "start_date": "2025-06-01",
  "end_date": "2025-06-03",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "当日行程简述",
      "transportation": "地铁/打车",
      "accommodation": "某某酒店",
      "attractions": [
        {
          "name": "景点名称",
          "address": "景点地址",
          "location": {"longitude": 116.4, "latitude": 39.9},
          "visit_duration": 120,
          "description": "景点看点描述",
          "category": "景点",
          "ticket_price": 60
        }
      ],
      "meals": [
        {
          "type": "lunch",
          "name": "餐厅名称",
          "estimated_cost": 50
        }
      ]
    }
  ],
  "overall_suggestions": "请根据以上行程提供详细的总体旅行建议，包含穿衣指南、交通避坑等...",
  "budget": {
      "total_attractions": 150,
      "total_hotels": 600,
      "total_meals": 300,
      "total_transportation": 100,
      "total": 1150
  }
}

注意：
1. 所有的 ticket_price 和 estimated_cost 必须是纯数字，不要写"免费"或"元"。
2. location 字段包含 longitude (经度) 和 latitude (纬度)，如果不确定具体坐标，可以使用默认值 {"longitude": 116.4, "latitude": 39.9}。

**重要：预算计算步骤（必须严格执行）**
1. 遍历所有 days 数组中的每一天
2. 对于每一天：
   - 累加所有 attractions 中的 ticket_price 到 total_attractions
   - 累加所有 meals 中的 estimated_cost 到 total_meals
3. 根据住宿情况计算 total_hotels：
   - 如果用户明确说住亲戚家/朋友家，total_hotels = 0
   - 否则，根据行程天数估算住宿费用
4. 根据交通方式估算 total_transportation
5. 计算 total = total_attractions + total_hotels + total_meals + total_transportation
6. **必须输出 budget 字段，且所有数值必须准确计算，不能全为0**

示例计算：
- 第1天：景点门票 60 + 80 = 140，餐饮 50 + 60 = 110
- 第2天：景点门票 50，餐饮 80
- total_attractions = 140 + 50 = 190
- total_meals = 110 + 80 = 190
- total_hotels = 0（住亲戚家）
- total_transportation = 50
- total = 190 + 0 + 190 + 50 = 430
"""


# ==========================================
# 3. 核心系统类 (高度封装)
# ==========================================

class MapAgentsSystem:
    """地图智能体调度中枢"""

    def __init__(self):
        self.graph = None
        self._initialized = False
        self._llm = None
        self._api_semaphore = asyncio.Semaphore(1)
        self._agents = {}

    async def initialize(self) -> None:
        """初始化 MCP 工具与大模型"""
        if self._initialized:
            return

        print("正在初始化 MCP 工具...")
        await initialize_mcp_tools()

        self._llm = get_llm()
        print("正在构建智能体图...")
        self.graph = self._build_graph()

        self._initialized = True
        print("系统初始化完成")

    async def cleanup(self) -> None:
        """清理资源"""
        print(" [CLEANUP] 正在清理系统资源...")
        await cleanup_mcp_tools()
        self._initialized = False
        self.graph = None
        self._agents = {}
        print(" [OK] 系统资源已清理")

    def _get_tools(self, tool_names: List[str]) -> List[BaseTool]:
        """获取指定的 MCP 工具"""
        return get_mcp_manager().get_tools_by_names(tool_names)

    async def _supervisor_node(self, state: AgentState) -> dict:
        """智能路由决策中心"""
        print("🧠 Supervisor (LLM) 正在深度阅读并思考决策...")

        supervisor_chain = self._llm.with_structured_output(RouteDecision)
        prompt = ChatPromptTemplate.from_messages([
            ("system", SUPERVISOR_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
        ])

        chain_input = {
            "messages": state["messages"],
            "city": state.get("city", "未知"),
            "transportation": state.get("transportation", "未知"),
            "accommodation": state.get("accommodation", "未知"),
            "free_text_input": state.get("free_text_input", "无"),
            "agent_call_count": state.get("agent_call_count", {})
        }

        try:
            decision = await supervisor_chain.ainvoke(prompt.format(**chain_input))
            print(f"  [思考过程]: {decision.reasoning}")
            print(f"  [决定流向]: {decision.next}")
            return {"next": decision.next}
        except Exception as e:
            print(f"❌ Supervisor 决策失败，执行安全兜底路由: {e}")
            return {"next": "planner_agent"}

    async def _worker_node(self, state: AgentState, agent_name: str, prompt_tpl: str, tools: List[BaseTool]) -> dict:
        """通用的 Worker 执行节点"""
        async with self._api_semaphore:
            await asyncio.sleep(2)
            print(f"  [{agent_name}] 开始执行...")

            try:
                # 确保 state 中包含必要的字段
                safe_state = {
                    "city": state.get("city", "未知"),
                    "transportation": state.get("transportation", "未知"),
                    "accommodation": state.get("accommodation", "未知"),
                    "free_text_input": state.get("free_text_input", "无"),
                    **state
                }
                
                if agent_name not in self._agents:
                    prompt = ChatPromptTemplate.from_messages([
                        ("system", prompt_tpl.format(**safe_state)),
                        MessagesPlaceholder(variable_name="messages")
                    ])
                    # 使用 bind_tools 强制模型行为，确保 LLM 严格按照工具的 schema 生成调用参数
                    llm_with_tools = self._llm.bind_tools(tools)
                    self._agents[agent_name] = create_react_agent(llm_with_tools, tools=tools, prompt=prompt)

                print(f"  [{agent_name}] 可用工具: {[t.name for t in tools]}")
                result = await self._agents[agent_name].ainvoke(state)

                last_msg = result["messages"][-1] if result["messages"] else None
                if last_msg:
                    content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
                    print(f"  [{agent_name}] 执行完成，返回内容长度: {len(content)} 字符")
                    if len(content) < 200:
                        print(f"  [{agent_name}] 返回内容: {content}")
                    else:
                        print(f"  [{agent_name}] 返回内容预览: {content[:200]}...")

                counts = state.get("agent_call_count", {})
                counts[agent_name] = counts.get(agent_name, 0) + 1

                # 将工具执行结果存入对应的数据字段
                if agent_name == "attraction_agent":
                    state["attraction_data"] = content
                    print(f"  [DEBUG] attraction_data 已存储，长度: {len(content)} 字符")
                    print(f"  [DEBUG] attraction_data 内容预览: {content[:500]}...")
                elif agent_name == "weather_agent":
                    state["weather_data"] = content
                    print(f"  [DEBUG] weather_data 已存储，长度: {len(content)} 字符")
                elif agent_name == "hotel_agent":
                    state["hotel_data"] = content
                    print(f"  [DEBUG] hotel_data 已存储，长度: {len(content)} 字符")
                elif agent_name == "route_agent":
                    state["route_data"] = content
                    print(f"  [DEBUG] route_data 已存储，长度: {len(content)} 字符")

                # 确保返回的字典包含所有必要的字段
                return {
                    "messages": result["messages"], 
                    "agent_call_count": counts,
                    **state  # 保留原始 state 中的所有字段
                }
            except Exception as e:
                print(f"  [{agent_name}] 执行失败: {e}")
                import traceback
                traceback.print_exc()
                counts = state.get("agent_call_count", {})
                counts[agent_name] = counts.get(agent_name, 0) + 1
                # 确保即使在异常情况下也返回所有必要的字段
                return {
                    "agent_call_count": counts,
                    **state  # 保留原始 state 中的所有字段
                }

    async def attraction_node(self, state: AgentState) -> dict:
        """景点搜索节点"""
        return await self._worker_node(
            state,
            "attraction_agent",
            ATTRACTION_AGENT_PROMPT,
            self._get_tools([AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL])
        )

    async def weather_node(self, state: AgentState) -> dict:
        """天气查询节点"""
        return await self._worker_node(
            state,
            "weather_agent",
            WEATHER_AGENT_PROMPT,
            self._get_tools([AmapTools.WEATHER])
        )

    async def hotel_node(self, state: AgentState) -> dict:
        """酒店推荐节点"""
        return await self._worker_node(
            state,
            "hotel_agent",
            HOTEL_AGENT_PROMPT,
            self._get_tools([AmapTools.TEXT_SEARCH, AmapTools.SEARCH_DETAIL])
        )

    async def route_node(self, state: AgentState) -> dict:
        """路线规划节点"""
        return await self._worker_node(
            state,
            "route_agent",
            ROUTE_AGENT_PROMPT,
            self._get_tools([
                AmapTools.DIRECTION_TRANSIT,
                AmapTools.DIRECTION_WALKING,
                AmapTools.DIRECTION_DRIVING,
                AmapTools.DIRECTION_BICYCLING
            ])
        )

    async def _planner_node(self, state: AgentState) -> dict:
        """行程汇总生成"""
        print("Planner 正在生成最终方案...")
    
        # 直接从 state 中读取已收集的数据
        attraction_info = state.get("attraction_data", "")
        weather_info = state.get("weather_data", "")
        hotel_info = state.get("hotel_data", "")
        route_info = state.get("route_data", "")
    
        # 添加详细的调试日志
        print(f"  [DEBUG] attraction_data 长度: {len(attraction_info)}")
        print(f"  [DEBUG] attraction_data 完整内容: {attraction_info}")
        print(f"  [DEBUG] weather_data 长度: {len(weather_info)}")
        print(f"  [DEBUG] hotel_data 长度: {len(hotel_info)}")
        print(f"  [DEBUG] route_data 长度: {len(route_info)}")
    
        data_summary = ""
        if attraction_info:
            data_summary += "【景点信息】\n" + attraction_info + "\n\n"
        if weather_info:
            data_summary += "【天气信息】\n" + weather_info + "\n\n"
        if route_info:
            data_summary += "【步行路线信息】\n" + route_info + "\n\n"
    
        print(f"Planner 处理后的关键信息长度: {len(data_summary)} 字符")
        print(f"Planner 收到的关键信息: {data_summary[:1000]}...")
    
        messages = [
            SystemMessage(content=PLANNER_AGENT_PROMPT),
            HumanMessage(
                content=f"""请根据以下关键数据生成完整的旅行计划：

目标城市: {state.get('city', '未知')}
旅行日期: {state.get('start_date', '未知')} 至 {state.get('end_date', '未知')}
旅行天数: {state.get('travel_days', 0)}天
交通方式: {state.get('transportation', '未知')}
住宿要求: {state.get('accommodation', '未知')}

关键信息:
{data_summary}

请根据以上信息，生成包含每日详细行程安排的完整旅行计划。
重要提示：
1. 必须使用目标城市: {state.get('city', '未知')}
2. 必须使用旅行日期: {state.get('start_date', '未知')} 至 {state.get('end_date', '未知')}
3. 必须根据实际收集的信息生成行程，不要使用示例数据
4. 所有 ticket_price 和 estimated_cost 必须是纯数字（如 60），严禁输出 '免费'、'未知' 或 '需查询'。如果无法获取，请基于同类景点预估一个合理的整数价格。
"""
            ),
        ]

        response = self._llm.invoke(messages)
        final_plan = response.content
        print(f"Planner 返回类型: {type(final_plan)}")
        if isinstance(final_plan, str):
            print(f"Planner 返回内容预览: {final_plan[:500]}")

        if isinstance(final_plan, str):
            try:
                import json
                data = json.loads(final_plan)
                print(f"解析后的JSON keys: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")

                if "city" not in data:
                    data["city"] = state["city"]
                if "start_date" not in data:
                    data["start_date"] = state["start_date"]
                if "end_date" not in data:
                    data["end_date"] = state["end_date"]
                if "overall_suggestions" not in data:
                    data["overall_suggestions"] = data.get("overall_suggestions", "祝您旅途愉快！")
                if "days" not in data:
                    data["days"] = []
                if "weather_info" not in data:
                    data["weather_info"] = []
                final_plan = data
            except Exception as e:
                print(f"JSON解析失败: {e}")
                final_plan = {
                    "city": state["city"],
                    "start_date": state["start_date"],
                    "end_date": state["end_date"],
                    "days": [],
                    "weather_info": [],
                    "overall_suggestions": str(final_plan) if final_plan else "祝您旅途愉快！",
                    "budget": {},
                }

        return {"final_plan": final_plan, "next": "end"}

    def _build_graph(self):
        """编排多智能体工作流"""
        workflow = StateGraph(AgentState)

        workflow.add_node("supervisor", self._supervisor_node)
        workflow.add_node("attraction_agent", self.attraction_node)
        workflow.add_node("weather_agent", self.weather_node)
        workflow.add_node("hotel_agent", self.hotel_node)
        workflow.add_node("route_agent", self.route_node)
        workflow.add_node("planner_agent", self._planner_node)

        for worker in ["attraction_agent", "weather_agent", "hotel_agent", "route_agent"]:
            workflow.add_edge(worker, "supervisor")

        workflow.add_conditional_edges(
            "supervisor",
            lambda x: x["next"],
            {
                "attraction_agent": "attraction_agent",
                "weather_agent": "weather_agent",
                "hotel_agent": "hotel_agent",
                "route_agent": "route_agent",
                "planner_agent": "planner_agent"
            }
        )
        workflow.add_edge("planner_agent", END)
        workflow.set_entry_point("supervisor")

        return workflow.compile()

    def plan_trip(self, request: TripRequest) -> TripPlan:
        """规划旅行（同步接口）"""
        if not self._initialized or self.graph is None:
            raise RuntimeError("系统未初始化，请先调用 initialize()")

        initial_message = f"我要去{request.city}玩{request.travel_days}天。"
        if request.free_text_input:
            initial_message += f" 额外要求：{request.free_text_input}"

        initial_state = {
            "messages": [HumanMessage(content=initial_message)],
            "city": request.city,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "travel_days": request.travel_days,
            "transportation": request.transportation,
            "accommodation": request.accommodation,
            "preferences": request.preferences,
            "free_text_input": request.free_text_input,
            "next": "supervisor",
            "agent_call_count": {},
        }

        print(" [START] 启动多智能体协作...")
        final_state = self.graph.invoke(initial_state)

        print(" [DONE] 规划完成")
        return self._parse_json_to_plan(final_state.get("final_plan"), request)

    async def plan_trip_async(self, request: TripRequest) -> TripPlan:
        """规划旅行（异步接口）"""
        if not self._initialized or self.graph is None:
            raise RuntimeError("系统未初始化，请先调用 initialize()")

        initial_message = f"我要去{request.city}玩{request.travel_days}天。"
        if request.free_text_input:
            initial_message += f" 额外要求：{request.free_text_input}"

        initial_state = {
            "messages": [HumanMessage(content=initial_message)],
            "city": request.city,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "travel_days": request.travel_days,
            "transportation": request.transportation,
            "accommodation": request.accommodation,
            "preferences": request.preferences,
            "free_text_input": request.free_text_input,
            "next": "supervisor",
            "agent_call_count": {},
        }

        print(" [START] 启动多智能体协作...")
        final_state = await self.graph.ainvoke(initial_state)

        print(" [DONE] 规划完成")
        return self._parse_json_to_plan(final_state.get("final_plan"), request)

    def _parse_json_to_plan(self, json_data: Union[dict, str], request: TripRequest) -> TripPlan:
        """解析数据为 TripPlan 对象"""
        import json
        
        def extract_json_from_text(text: str) -> Optional[dict]:
            """从文本中提取并解析 JSON"""
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    extracted = text[start:end + 1]
                    return json.loads(extracted)
            except Exception as e:
                print(f" [ERROR] 提取 JSON 失败: {e}")
            return None
        
        try:
            # 情况1: 已经是字典
            if isinstance(json_data, dict):
                print(f" [PARSE] 接收到有效字典数据")
                return self._build_trip_plan(json_data, request)
            
            # 情况2: 是字符串，尝试解析
            elif isinstance(json_data, str):
                try:
                    data = json.loads(json_data)
                    print(f" [PARSE] JSON 解析成功")
                    return self._build_trip_plan(data, request)
                except json.JSONDecodeError:
                    print(f" [INFO] 直接解析失败，尝试从文本中提取 JSON")
                    if extracted_data := extract_json_from_text(json_data):
                        print(f" [PARSE] 从文本中提取 JSON 成功")
                        return self._build_trip_plan(extracted_data, request)
            
            # 所有解析尝试失败
            print(f" [WARN] 无法解析数据: {type(json_data)}")
            
        except Exception as e:
            print(f" [ERROR] 解析过程发生错误: {e}")
        
        # 兜底方案
        print(" [WARN] 使用默认空计划")
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=[],
            overall_suggestions="无法生成详细计划",
            budget=Budget(),
        )



    def _build_trip_plan(self, data: dict, request: TripRequest) -> TripPlan:
        """构建 TripPlan 对象并自动计算预算"""
        import json
        from pydantic import ValidationError

        # 自动计算预算
        total_attr = 0
        total_meal = 0
        
        print(f"  [DEBUG] 开始计算预算...")
        print(f"  [DEBUG] days 数量: {len(data.get('days', []))}")
        
        for day in data.get("days", []):
            # 累加景点门票
            for attr in day.get("attractions", []):
                price = attr.get("ticket_price", 0)
                print(f"  [DEBUG] 景点: {attr.get('name', '未知')}, 门票价格: {price}, 类型: {type(price)}")
                if isinstance(price, (int, float)) and price >= 0:
                    total_attr += price
            # 累加餐饮费用
            for meal in day.get("meals", []):
                cost = meal.get("estimated_cost", 0)
                print(f"  [DEBUG] 餐饮: {meal.get('name', '未知')}, 费用: {cost}, 类型: {type(cost)}")
                if isinstance(cost, (int, float)) and cost >= 0:
                    total_meal += cost

        print(f"  [DEBUG] 景点总费用: {total_attr}")
        print(f"  [DEBUG] 餐饮总费用: {total_meal}")

        # 构建预算字典，确保在赋值前字典内容是正确的
        clean_budget_data = {
            "total_attractions": int(total_attr),
            "total_hotels": 0,  # 用户住亲戚家，强制为 0
            "total_meals": int(total_meal),
            "total_transportation": 0,
            "total": int(total_attr + total_meal + 100)
        }
        
        print(f"  [DEBUG] 最终预算字典: {clean_budget_data}")

        overall_suggestions = data.get("overall_suggestions", [])
        if isinstance(overall_suggestions, list):
            overall_suggestions = " ".join(overall_suggestions)

        try:
            plan = TripPlan(
                city=data.get("city", request.city),
                start_date=data.get("start_date", request.start_date),
                end_date=data.get("end_date", request.end_date),
                days=data.get("days", []),
                weather_info=data.get("weather_info", []),
                overall_suggestions=overall_suggestions or "祝您旅途愉快！",
                budget=clean_budget_data,  # Pydantic 会自动把这个字典转换成 Budget 模型
            )
            print(f" [SUCCESS] TripPlan 创建成功")
            return plan
        except ValidationError as e:
            print(f" [ERROR] 结构验证失败，请检查 LLM 输出的字段类型:")
            for error in e.errors():
                print(f"   - 路径: {error['loc']} | 原因: {error['msg']}")
            safe_data = {
                "city": data.get("city", request.city),
                "start_date": data.get("start_date", request.start_date),
                "end_date": data.get("end_date", request.end_date),
                "days": [],
                "weather_info": [],
                "overall_suggestions": "数据验证失败，但规划完成",
                "budget": clean_budget_data,
            }
            print(f" [SAFE] 使用安全版本: {safe_data}")
            return TripPlan(**safe_data)


# ==========================================
# 4. 模块级单例暴露
# ==========================================

_system: Optional[MapAgentsSystem] = None


def get_map_agents_system() -> MapAgentsSystem:
    """获取地图智能体系统实例（懒加载）"""
    global _system
    if _system is None:
        _system = MapAgentsSystem()
    return _system


async def initialize_map_agents_system() -> MapAgentsSystem:
    """初始化地图智能体系统（应用启动时调用）"""
    global _system
    if _system is None:
        _system = MapAgentsSystem()
        await _system.initialize()
    return _system


async def cleanup_map_agents_system() -> None:
    """清理地图智能体系统（应用关闭时调用）"""
    global _system
    if _system:
        await _system.cleanup()
        _system = None
