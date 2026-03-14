"""Supervisor 模块 - 智能路由决策中心"""

import logging
from typing import Dict, Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from .schemas.state import AgentState, RouteDecision
from .prompts.agents import AgentPrompts

logger = logging.getLogger(__name__)


def check_structured_data(state: AgentState, key: str) -> str:
    """检查结构化数据是否有效"""
    data = state.get(key, [])
    if isinstance(data, list) and len(data) > 0:
        return f"✅ 已获取 {len(data)} 条"
    return "❌ 未获取或为空"


class Supervisor:
    """
    Supervisor 智能路由决策器

    职责：
    1. 分析当前状态和对话历史
    2. 决定下一步调用哪个 Worker Agent
    3. 使用强类型输出确保决策可靠性
    """

    def __init__(self, llm):
        """
        初始化 Supervisor

        Args:
            llm: 语言模型实例
        """
        self.llm = llm
        self._chain = None
        self._build_chain()

    def _build_chain(self):
        """构建 Supervisor 决策链"""

        # 使用 with_structured_output 强制输出 Pydantic 模型
        supervisor_llm = self.llm.with_structured_output(RouteDecision)

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", AgentPrompts.SUPERVISOR),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        self._chain = prompt | supervisor_llm
        print("Supervisor 决策链已构建")

    async def decide(self, state: AgentState) -> Dict[str, Any]:
        """
        执行路由决策

        Args:
            state: 当前状态

        Returns:
            {"next": "agent_name"} 或 {"next": ["agent1", "agent2"]}
        """
        print("🧠 Supervisor 正在分析并决策...")

        # 检查结构化数据状态
        attractions_status = check_structured_data(state, "attractions")
        weather_status = check_structured_data(state, "weather_info")
        hotels_status = check_structured_data(state, "hotels")
        routes_status = check_structured_data(state, "routes")

        print(
            f"  数据状态: 景点={attractions_status}, 天气={weather_status}, 酒店={hotels_status}, 路线={routes_status}"
        )

        # 准备输入
        chain_input = {
            "messages": state["messages"],
            "city": state.get("city", "未知"),
            "transportation": state.get("transportation", "未知"),
            "accommodation": state.get("accommodation", "未知"),
            "free_text_input": state.get("free_text_input", "无"),
            "agent_call_count": state.get("agent_call_count", {}),
            "agent_results": state.get("agent_results", {}),
            "attractions_status": attractions_status,
            "weather_status": weather_status,
            "hotels_status": hotels_status,
            "routes_status": routes_status,
        }

        try:
            # 调用 LLM 获取决策
            decision: RouteDecision = await self._chain.ainvoke(chain_input)

            print(f"  [思考过程]: {decision.reasoning}")
            print(f"  [决定流向]: {decision.next}")
            if decision.parallel:
                print(f"  [并发模式]: 是")

            # 处理并发或串行
            if decision.parallel and isinstance(decision.next, list):
                return {"next": decision.next}
            elif isinstance(decision.next, list):
                # 如果返回的是列表但parallel=False，取第一个
                return {"next": decision.next[0]}
            else:
                return {"next": decision.next}

        except Exception as e:
            print(f"❌ Supervisor 决策失败: {e}")
            print("执行安全兜底路由: planner_agent")
            return {"next": "planner_agent"}

    def get_node(self):
        """
        获取 LangGraph 节点函数

        Returns:
            可用于 add_node 的异步函数
        """

        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            return await self.decide(state)

        return supervisor_node
