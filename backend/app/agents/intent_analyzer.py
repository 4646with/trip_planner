"""意图分析模块 - 唯一的LLM前置节点

核心设计原则：
- 这是整个系统唯一一个"理解用户说了什么"的地方
- 它的输出 TripIntent 是所有后续节点的唯一可信来源
- 后续节点只需读取结构化的 intent state，不需要重新扫描消息历史
"""

import logging
from typing import List, Literal, Dict, Any

from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from .schemas.state import AgentState, TripIntent

logger = logging.getLogger(__name__)


INTENT_PROMPT = """你是旅行意图分析专家。你的唯一任务是从用户的输入中提取结构化的意图信息。

【核心规则 - 必须遵守】

规则1：自由文本的优先级 > 表单选项
用户在自由文本里说"住哥哥家"，即使表单选了"豪华型酒店"，hotel_intent也必须是"skip"。
自由文本是用户更主动、更后置的表达，永远优先。

规则2：hotel_intent的三级状态判断
- "skip"：用户明确表达不需要订酒店
  触发词例子："住哥哥家"、"住朋友家"、"有亲戚接待"、"不用订酒店"、"自己解决住宿"
- "need"：用户明确需要，或表单选了酒店类型且无冲突
- "unknown"：完全没有提到住宿，保守处理（系统会默认按need处理）

规则3：given_attractions的判断标准
只有用户明确说"要去XX地方"才算。偏好标签（如"历史文化"）不能推断为具体景点。
例如："想去故宫和颐和园" → ["故宫", "颐和园"] ✅
例如："喜欢历史文化" → [] ✅（不能推断出具体景点）

规则4：special_requirements必须用语义理解，不能用关键词匹配
"我外婆要来" → ["携带老人"] ✅（"外婆"不是关键词"老人"，但语义上是）
"带着娃" → ["亲子游"] ✅
"妈妈腿脚不好" → ["携带老人", "无障碍需求"] ✅

规则5：冲突检测
当自由文本和表单选项矛盾时，设has_conflict=True，并在conflict_note中说明冲突内容和采用的决策。

【输出要求】
严格按照JSON schema输出，不要输出任何额外文字或markdown标记。
"""


class IntentAnalyzer:
    """
    意图分析节点 - 系统的入口，运行一次，结果永久存入state。

    它解决的核心问题：
    用户说"我住哥哥家，想去几个好玩的地方"，系统不应该再去订酒店。
    但这句话里没有任何关键词能被规则匹配到，只有LLM才能理解。

    这个节点让LLM做它最擅长的事——理解自然语言；
    让规则引擎做它最擅长的事——执行确定性逻辑。
    """

    def __init__(self, llm):
        # with_structured_output 强制LLM输出符合TripIntent schema的JSON
        # 这样我们永远不需要手动解析LLM的输出
        self.structured_llm = llm.with_structured_output(TripIntent)

    async def analyze(self, state: AgentState) -> Dict[str, Any]:
        """
        分析用户意图，将结果写入state["trip_intent"。

        这是整个系统唯一一次"理解用户说了什么"的LLM调用。
        之后所有节点都读取 state["trip_intent"，不再重复推断。
        """
        rid = state.get("request_id", "unknown")
        
        # 如果已经分析过，直接跳过
        if state.get("trip_intent"):
            logger.info(f"[{rid}] 意图已分析，跳过")
            return {}
        
        logger.info(f"[{rid}] 开始分析用户意图...")

        # 构建输入消息：同时提供表单数据和自由文本
        # 这样LLM能检测到两者之间的冲突
        user_context = HumanMessage(content=f"""
请分析以下用户的旅行请求，提取结构化意图。

【表单填写内容】
目的地：{state.get('city', '未知')}
旅行天数：{state.get('travel_days', 0)}天
出行日期：{state.get('start_date', '未知')} 至 {state.get('end_date', '未知')}
交通方式（表单）：{state.get('transportation', '未选择')}
住宿偏好（表单）：{state.get('accommodation', '未选择')}
旅行风格标签（表单）：{state.get('preferences', [])}

【用户自由文本（优先级最高）】
{state.get('free_text_input', '（用户未填写额外说明）')}

【原始请求消息】
{state['messages'][0].content if state.get('messages') else '无'}
""")

        try:
            intent: TripIntent = await self.structured_llm.ainvoke([
                SystemMessage(content=INTENT_PROMPT),
                user_context,
            ])

            # 记录关键决策，方便调试
            logger.info(
                f"[{rid}] 意图分析完成 | "
                f"hotel_intent={intent.hotel_intent} | "
                f"given_attractions={intent.given_attractions} | "
                f"special_requirements={intent.special_requirements} | "
                f"has_conflict={intent.has_conflict}"
            )

            if intent.has_conflict:
                logger.warning(f"[{rid}] 检测到冲突: {intent.conflict_note}")

            # 根据意图构建路由决策
            agents = []
            if intent.need_attraction_search:
                agents.append("attraction_agent")
            if intent.need_weather:
                agents.append("weather_agent")
            if intent.hotel_intent != "skip":  # skip=明确不需要，其他情况都搜
                agents.append("hotel_agent")
            if intent.need_route:
                agents.append("route_agent")

            # 将结构化意图写入state，供所有后续节点读取
            return {
                "trip_intent": intent.model_dump(),
                "next": agents if len(agents) > 1 else (agents[0] if agents else "planner_agent"),
            }

        except Exception as e:
            logger.error(f"[{rid}] 意图分析失败: {e}，使用保守默认值")
            # 失败时使用保守默认值：所有东西都搜，避免漏掉任何信息
            fallback_intent = TripIntent(
                hotel_intent="unknown",
                need_attraction_search=True,
                need_weather=True,
                need_route=True,
                reasoning=f"意图分析失败，使用保守默认值。错误: {str(e)}"
            )
            return {
                "trip_intent": fallback_intent.model_dump(),
                "next": ["attraction_agent", "weather_agent", "hotel_agent", "route_agent"],
            }

    def get_node(self):
        """返回可被GraphBuilder使用的节点函数"""
        async def intent_node(state: AgentState) -> Dict[str, Any]:
            return await self.analyze(state)
        return intent_node
