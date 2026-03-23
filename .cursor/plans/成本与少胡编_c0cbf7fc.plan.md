---
name: 成本与少胡编
overview: 针对你当前“成本/性能”和“少胡编/少乱写”的目标，梳理攻略生成链路中的主要耗费点（web_search 与多 Agent 重试/并发）以及主要胡编风险点（Planner 生成 meals、routes 清洗丢失、预算由 LLM 自算），给出一套可落地的改造顺序。
todos:
  - id: fix-route-prompt
    content: 统一 `route_agent` 的提示词与 MCP 工具参数要求：`backend/app/agents/prompts/agents.py` 中 `ROUTE` prompt 将 `origin/destination` 的约束改为“必须是经度,纬度坐标字符串”（与 `backend/app/services/mcp_tools.py` 的 `DirectionToolInput` 一致），避免 route_agent 调用工具时参数错误导致重试与成本放大。
    status: pending
  - id: fix-planner-route-cleaner
    content: 修复 `backend/app/agents/workers.py` 中 `_extract_planner_fields()` 对 `routes` 的过滤逻辑：为 attractions/hotels/routes 分别定义 essential keys，且不要用 `name` 作为通用门槛；确保 `routes_context` 能正确进入 `Planner.generate()`，减少 planner 用“叙事补全路线细节”的胡编概率，同时避免 route_agent 输出被浪费。
    status: pending
  - id: enforce-meals-empty-constraint
    content: |-
      落地“少胡编的餐饮/避雷策略（选择 B：不新增 meal_agent）”：
      - 修改 `backend/app/agents/prompts/agents.py` 的 `PLANNER` 输出规范，明确当系统未检索到餐饮数据时 `days[].meals` 必须为空数组（不要给出具体店名/菜名/避雷事实）。
      - 在 `backend/app/agents/workers.py` 的 `Planner.generate()` 后处理：强制 `final_plan.days[*].meals=[]`，并将“避雷/餐饮”改为只给通用安全建议放在 `overall_suggestions`（不引用具体店/口碑细节）。
    status: pending
  - id: deterministic-budget
    content: |-
      减少预算胡编：在 `backend/app/agents/workers.py` 中对 `budget` 做确定性计算或直接留空。
      - 至少保证 `total_attractions` 来自工具返回的 `ticket_price`。
      - meals 由于选择 B 设为 0。
      - hotel/transportation 若缺少可验证数值则可设 0 或不返回 `budget`（让前端不显示预算卡），以保持 grounding。
    status: pending
  - id: web-search-cache-and-reduce
    content: |-
      降低 web_search 成本：
      - 在 `backend/app/agents/tools.py` 给 `web_search()` 增加缓存（内存 LRU + 可选 sqlite 持久化，key=标准化后的 query；设置 TTL）。
      - 将 `count` 从 3 降到 1（或 2）并确认 summary 的使用方式不影响 grounding。
      - 记录每次请求的 web_search 命中/未命中次数，为后续调参提供依据。
    status: pending
  - id: gemini-real-concurrency
    content: 修复 `backend/app/agents/workers.py` 中 gemini 分支的“只打印不生效”问题：当 `LLM_PROVIDER=gemini` 时，将 `WorkerExecutor` 的 semaphore/max_concurrency 实际设置为 1（避免 429→重试→成本放大）。
    status: pending
  - id: api-route-method-mismatch
    content: 修复 `backend/app/api/routes/map_agents_router.py` 调用 `system.plan_trip_async(request)` 与 `backend/app/agents/main.py` 当前实现不一致的问题（应调用 `plan_trip` 或补齐 `plan_trip_async` 包装）。避免接口失败后触发上层重试，间接增加成本与超时。
    status: pending
isProject: false
---

## 现状关键点（基于代码审阅）

1. `IntentAnalyzer` 只做一次意图抽取，后续节点依赖 `state["trip_intent"]` 路由。
2. `BaseWorker.execute()`：每个 worker 都会调用 `create_react_agent(...).ainvoke()`，并可能触发 MCP 工具 + `web_search`。
3. `ATTRACTION/HOTEL` worker 启用了增强 `web_search`（目前没有缓存/调用上限），这通常是主要成本来源。
4. `Planner.generate()`：
  - 目前输入只包含 `attractions/hotels/routes/weather`，但 Planner 仍要求输出 `days[].meals`（餐饮列表）与“避雷”内容，易胡编。
  - `workers.py` 的 planner 数据清洗 `_extract_planner_fields()` 对 `routes` 使用了不匹配的字段白名单，并且还用 `name` 作为门槛，导致 `routes_context` 大概率为空，从而 planner 可能用“叙事”补全细节。
5. 预算 `budget` 目前由 Planner（LLM）按 prompt 规则自算，缺少 meal/hotel 成本的可验证输入时也会胡编。

## 改造目标

- 性能/成本：减少 web_search 调用次数与单次成本；降低 429/重试导致的“放大成本”；避免不必要的 route/meal 生成。
- 少胡编/grounding：对 `days[].meals` 与“餐饮/避雷”类内容提供硬约束（尽量留空或仅给通用建议），并将预算中可确定部分改为确定性计算。

## 实施顺序建议

1. 先做“硬约束/丢失数据修复”（routes清洗、meals约束），立刻提升 grounding。
2. 再做“成本控制”（web_search 缓存/降频/降count、gemini 并发真实降到 1）。
3. 最后做“预算确定性计算”和“API路由方法修复”（避免失败重试）。

