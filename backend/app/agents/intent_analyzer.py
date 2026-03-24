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


INTENT_PROMPT = """
你是旅行意图结构化引擎，职责有两个：
1. 从用户输入中提取结构化意图字段
2. 为下游 Agent 生成执行指令

━━━━━━━━━━━━━━━━━━━━━━━━
【全局优先级】自由文本 > 表单选项
用户在自由文本中的表达是最后意图，永远覆盖表单选项。
存在矛盾时：has_conflict=True，conflict_note 说明冲突内容和采用决策。
━━━━━━━━━━━━━━━━━━━━━━━━

【规则 R1】hotel_intent 三级判断

| 值        | 判断条件                                     | 典型触发表达                        |
|-----------|----------------------------------------------|-------------------------------------|
| "skip"    | 用户明确不需要订酒店                         | "住哥哥家"、"朋友接待"、"自己解决住宿" |
| "need"    | 用户明确需要，或表单选了酒店类型且无冲突     | —                                   |
| "unknown" | 完全未提及住宿（系统默认按 need 处理）       | —                                   |

【规则 R2】given_attractions —— 只提取明确表达的景点
✅ "想去故宫和颐和园" → ["故宫", "颐和园"]
❌ "喜欢历史文化" → []（偏好≠具体景点）

【规则 R3】special_requirements —— 语义理解，禁止关键词匹配
✅ "外婆要来" → ["携带老人"]
✅ "带着娃" → ["亲子游"]
✅ "妈妈腿脚不好" → ["携带老人", "无障碍需求"]

【规则 R4】hard_constraints —— 提取排他性，强意志表达
判断标准：用户是否使用了排他性语气 + 具体对象
✅ "我要吃老碗会" → 提取（排他性强）
❌ "想吃点好吃的" → 不提取（泛泛偏好，反映在 travel_style）
✅ "第一天必须轻松" → 提取
❌ "偏好休闲" → 不提取

【规则 R5】pre_selected_* —— 排他性语气 + 具体名称时锁定

| 字段                      | 锁定数量 | 触发词示例                                     |
|---------------------------|----------|------------------------------------------------|
| pre_selected_hotel        | 唯一     | "就住XX"、"必须住XX"、"已经订了XX"             |
| pre_selected_attractions  | 多个     | "就去这几个"、"其他不用安排"                   |
| pre_selected_restaurants  | 多个     | "就去那家XX"、"必须吃XX"                       |

注：填了 pre_selected_attractions → need_attraction_search 设为 False

【规则 R6】agent_instructions —— 将用户限制转化为下游执行指令

| 用户表达类型   | 目标 Agent      | 指令示例                                     |
|----------------|-----------------|----------------------------------------------|
| 地理限制       | attraction/hotel | "必须以朝阳区为中心推荐景点"                 |
| 携带老人       | attraction      | "推荐平缓、无需爬山的休闲景点"               |
| 亲子游         | attraction      | "优先推荐适合儿童、互动性强的景点"           |
| 腿脚不便       | attraction      | "选择无需爬坡、设施完善的景点"               |
| 地道美食偏好   | restaurant      | "搜索当地人常去的老字号餐厅"                 |

指令填写规则：
- 每条不超过 50 字
- 无特殊限制时留空字符串 ""
- 指令会直接注入目标 Agent 的 system prompt

━━━━━━━━━━━━━━━━━━━━━━━━
【输出要求】严格按 JSON schema 输出，不输出任何额外文字或 markdown 标记。
━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _resolve_itinerary_style(travel_days: int, hard_constraints: list[str]) -> str:
    """
    决定行程节奏。
    优先尊重用户在 hard_constraints 中的明确节奏要求；
    否则按天数自动判断：≤2天紧凑，>2天舒缓。
    """
    constraints_text = " ".join(hard_constraints or [])
    if any(kw in constraints_text for kw in ("轻松", "慢", "悠闲", "不赶")):
        return "relaxed"
    if any(kw in constraints_text for kw in ("紧凑", "多跑", "塞满", "效率")):
        return "compact"
    return "compact" if travel_days <= 2 else "relaxed"


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
        self.structured_llm = llm.with_structured_output(TripIntent)

    async def analyze(self, state: AgentState) -> Dict[str, Any]:
        """
        分析用户意图，将结果写入 state["trip_intent"]。

        这是整个系统唯一一次"理解用户说了什么"的LLM调用。
        之后所有节点都读取 state["trip_intent"]，不再重复推断。
        """
        rid = state.get("request_id", "unknown")

        if state.get("trip_intent"):
            logger.info(f"[{rid}] 意图已分析，跳过")
            return {}

        logger.info(f"[{rid}] 开始分析用户意图...")

        user_context = HumanMessage(
            content=f"""
请分析以下用户的旅行请求，提取结构化意图。

【表单填写内容】
目的地：{state.get("city", "未知")}
旅行天数：{state.get("travel_days", 0)}天
出行日期：{state.get("start_date", "未知")} 至 {state.get("end_date", "未知")}
交通方式（表单）：{state.get("transportation", "未选择")}
住宿偏好（表单）：{state.get("accommodation", "未选择")}
旅行风格标签（表单）：{state.get("preferences", [])}

【用户自由文本（优先级最高）】
{state.get("free_text_input", "（用户未填写额外说明）")}

【原始请求消息】
{state["messages"][0].content if state.get("messages") else "无"}
"""
        )

        try:
            intent: TripIntent = await self.structured_llm.ainvoke(
                [
                    SystemMessage(content=INTENT_PROMPT),
                    user_context,
                ]
            )

            logger.info(
                f"[{rid}] 意图分析完成 | "
                f"hotel_intent={intent.hotel_intent} | "
                f"given_attractions={intent.given_attractions} | "
                f"special_requirements={intent.special_requirements} | "
                f"has_conflict={intent.has_conflict}"
            )

            if intent.has_conflict:
                logger.warning(f"[{rid}] 检测到冲突: {intent.conflict_note}")

            travel_days = state.get("travel_days", 3)
            intent.itinerary_style = _resolve_itinerary_style(
                travel_days, intent.hard_constraints
            )
            logger.info(
                f"[{rid}] 行程节奏判定: travel_days={travel_days} "
                f"hard_constraints={intent.hard_constraints} "
                f"→ itinerary_style={intent.itinerary_style}"
            )

            first_wave = [
                agent
                for agent, needed in [
                    ("attraction_agent", intent.need_attraction_search),
                    ("weather_agent", intent.need_weather),
                ]
                if needed
            ]

            next_node = "planner_agent" if not first_wave else (
                first_wave if len(first_wave) > 1 else first_wave[0]
            )

            return {
                "trip_intent": intent.model_dump(),
                "next": next_node,
            }

        except Exception as e:
            logger.error(f"[{rid}] 意图分析失败: {e}，使用保守默认值")
            travel_days = state.get("travel_days", 3)
            fallback_intent = TripIntent(
                hotel_intent="unknown",
                need_attraction_search=True,
                need_weather=True,
                need_route=True,
                itinerary_style=_resolve_itinerary_style(travel_days, []),
                reasoning=f"意图分析失败，使用保守默认值。错误: {str(e)}",
            )
            return {
                "trip_intent": fallback_intent.model_dump(),
                "next": ["attraction_agent", "weather_agent"],
            }

    def get_node(self):
        async def intent_node(state: AgentState) -> Dict[str, Any]:
            return await self.analyze(state)
        return intent_node
