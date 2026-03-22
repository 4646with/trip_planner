"""Supervisor 模块 - 智能路由决策中心

重构后的职责变化：
- 旧版：既要理解用户意图，又要感知运行时状态，职责混乱
- 新版：只负责感知运行时状态（数据是否获取成功、是否需要容错）
        用户意图由 IntentAnalyzer 节点负责，Supervisor 直接读取结果

这个改变让 Supervisor 的每次 LLM 调用都更加聚焦，
prompt 更短，决策更准确。
"""

import logging
from typing import Dict, Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from .schemas.state import AgentState, RouteDecision
from .prompts.agents import AgentPrompts
from .rule_router import smart_router

logger = logging.getLogger(__name__)

MAX_AGENT_CALLS = 10


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
                ("user", "【任务上下文】{context}"),
            ]
        )
        self._chain = prompt | supervisor_llm

    def _build_context(self, state: AgentState) -> str:
        """
        构建传给 Supervisor 的上下文。

        关键改变：分为两个清晰的层次：
        1. 意图层：用户想要什么（直接从 trip_intent 读取，不重复推断）
        2. 运行时层：执行情况如何（Supervisor 真正需要感知的部分）

        这样 Supervisor 的 prompt 更短更聚焦，
        不再需要从消息历史里猜测用户说了什么。
        """
        intent = state.get("trip_intent", {})

        # ── 意图层：直接读取意图节点的结论 ──────────────────────
        # Supervisor 不需要知道"用户说了什么"，只需要知道"系统应该做什么"
        hotel_intent = intent.get("hotel_intent", "unknown")
        need_hotel = hotel_intent != "skip"  # skip=明确不需要, 其他情况都搜

        intent_summary = (
            f"需要订酒店: {'是' if need_hotel else '否（用户明确不需要）'} | "
            f"需要搜景点: {'是' if intent.get('need_attraction_search', True) else '否'} | "
            f"需要查天气: {'是' if intent.get('need_weather', True) else '否'} | "
            f"需要规划路线: {'是' if intent.get('need_route', True) else '否'} | "
            f"已指定景点: {intent.get('given_attractions', [])} | "
            f"特殊需求: {intent.get('special_requirements', [])}"
        )

        # ── 运行时层：Supervisor 真正需要感知的 ─────────────────
        # 这部分是动态变化的，需要实时感知
        has_attractions = len(state.get("attractions", [])) > 0
        has_weather = bool(state.get("weather_info"))
        has_hotels = len(state.get("hotels", [])) > 0
        has_routes = len(state.get("routes", [])) > 0

        execution_summary = (
            f"景点数据已获取: {'是' if has_attractions else '否'} | "
            f"天气数据已获取: {'是' if has_weather else '否'} | "
            f"酒店数据已获取: {'是' if has_hotels else '否'} | "
            f"路线数据已获取: {'是' if has_routes else '否'} | "
            f"已调用统计: {state.get('agent_call_count', {})} | "
            f"错误数: {len(state.get('errors', []))}"
        )

        return f"【意图层】{intent_summary}\n【运行时层】{execution_summary}"

    async def decide(self, state: AgentState) -> Dict[str, Any]:
        rid = state.get("request_id", "unknown")
        
        # 首先尝试规则路由（零LLM调用）
        try:
            result = smart_router(state)
            
            if result.get("next") == "planner_agent":
                return result
            
            if result.get("next"):
                return result
            
        except Exception as e:
            logger.error(f"[{rid}] 规则路由失败: {e}，降级到LLM决策")
        
        # 规则路由无法处理，降级到LLM决策
        logger.info(f"[{rid}] 进入LLM决策模式（降级）")
        
        # 构建上下文：意图层 + 运行时层
        context = self._build_context(state)
        
        # 只保留最近的消息作为对话上下文
        MAX_SUPERVISOR_MESSAGES = 3
        recent_messages = state["messages"][-MAX_SUPERVISOR_MESSAGES:]
        
        chain_input = {
            "messages": recent_messages,
            "context": context,
        }
        
        try:
            decision: RouteDecision = await self._chain.ainvoke(chain_input)
            logger.info(f"[{rid}] LLM决策结果: {decision.next}")
            
            if decision.parallel and isinstance(decision.next, list):
                return {"next": decision.next}
            elif isinstance(decision.next, list):
                return {"next": decision.next[0]}
            else:
                return {"next": decision.next}
        
        except Exception as e:
            logger.error(f"[{rid}] LLM决策失败: {e}，兜底到 planner_agent")
            return {"next": "planner_agent"}


    def get_node(self):
        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            return await self.decide(state)

        return supervisor_node
