"""Supervisor 模块 - 智能路由决策中心"""

import logging
from typing import Dict, Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from .schemas.state import AgentState, RouteDecision
from .prompts.agents import AgentPrompts

logger = logging.getLogger(__name__)

MAX_AGENT_CALLS = 10


def check_structured_data(state: AgentState, key: str) -> str:
    data = state.get(key, [])
    if isinstance(data, list) and len(data) > 0:
        return f"✅ 已获取 {len(data)} 条"
    return "❌ 未获取或为空"


class Supervisor:
    def __init__(self, llm):
        self.llm = llm
        self._chain = None
        self._build_chain()

    def _build_chain(self):
        supervisor_llm = self.llm.with_structured_output(RouteDecision)
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", AgentPrompts.SUPERVISOR),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        self._chain = prompt | supervisor_llm

    async def decide(self, state: AgentState) -> Dict[str, Any]:
        rid = state.get("request_id", "unknown")

        total_calls = sum(state.get("agent_call_count", {}).values())
        if total_calls >= MAX_AGENT_CALLS:
            logger.error(
                f"[{rid}] Supervisor 达到最大调用次数 ({MAX_AGENT_CALLS})，"
                f"强制进入 planner_agent，当前 call_count: {state.get('agent_call_count')}"
            )
            return {"next": "planner_agent"}

        fatal_errors = [e for e in state.get("errors", []) if e.get("fatal")]
        if fatal_errors:
            logger.error(
                f"[{rid}] 检测到致命错误 {fatal_errors}，强制进入 planner_agent"
            )
            return {"next": "planner_agent"}

        logger.info(f"[{rid}] Supervisor 开始决策，已调用 {total_calls} 次")

        attractions_status = check_structured_data(state, "attractions")
        weather_status = check_structured_data(state, "weather_info")
        hotels_status = check_structured_data(state, "hotels")
        routes_status = check_structured_data(state, "routes")

        MAX_SUPERVISOR_MESSAGES = 6
        recent_messages = state["messages"][-MAX_SUPERVISOR_MESSAGES:]

        chain_input = {
            "messages": recent_messages,
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
            decision: RouteDecision = await self._chain.ainvoke(chain_input)
            logger.info(
                f"[{rid}] 决策结果: {decision.next} | 理由: {decision.reasoning}"
            )

            if decision.parallel and isinstance(decision.next, list):
                return {"next": decision.next}
            elif isinstance(decision.next, list):
                return {"next": decision.next[0]}
            else:
                return {"next": decision.next}

        except Exception as e:
            logger.error(f"[{rid}] Supervisor 决策失败: {e}，兜底路由到 planner_agent")
            return {"next": "planner_agent"}

    def get_node(self):
        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            return await self.decide(state)

        return supervisor_node
