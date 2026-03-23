"""Supervisor 模块 - 智能路由决策中心

职责：作为 fault_check_router 的纯包装，所有路由决策由 rule_router 处理
"""

import logging
from typing import Dict, Any

from .schemas.state import AgentState
from .rule_router import smart_router

logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, llm):
        self.llm = llm

    async def decide(self, state: AgentState) -> Dict[str, Any]:
        """路由决策 - 完全委托给 smart_router"""
        rid = state.get("request_id", "unknown")
        result = smart_router(state)
        logger.info(f"[{rid}] Supervisor 路由决策: {result.get('next')}")
        return result

    def get_node(self):
        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            return await self.decide(state)

        return supervisor_node
