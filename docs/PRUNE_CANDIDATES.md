## 模块裁剪候选清单（保留 / 降级 / 删除）

这份清单的目的不是立刻重构，而是帮助你在“学习—评估—裁剪”的循环里，做出不纠结的取舍决策：哪些模块对质量确实重要，哪些只是让复杂度上升但并没有提供对应的 grounding/稳定性收益。

建议你用本文件作为“裁剪前决策记录”。后续每次改动都可以回到这里对照：改完后收益是否兑现。

---

### 0. 裁剪原则（先守住不变量）

1. 所有写入 `AgentState` 并参与 Planner 生成的闭环字段（`trip_intent/attractions/weather_info/hotels/routes/final_plan`）相关逻辑优先保留。
2. “没有被系统读取/没有进入 Planner 上下文”的数据源或工具，优先降级或删除。
3. 若没有相应检索证据支持（例如 meals 没有餐饮检索 worker），生成端必须强约束为“留空或通用建议”，否则会胡编。

---

### 1. 候选列表（按模块粒度）


| 模块/位置                                                                                       | 当前作用                            | 主要复杂度来源                       | 对质量的贡献                                        | 裁剪建议                                   | 预期收益                        |
| ------------------------------------------------------------------------------------------- | ------------------------------- | ----------------------------- | --------------------------------------------- | -------------------------------------- | --------------------------- |
| `IntentAnalyzer`（`backend/app/agents/intent_analyzer.py`）                                   | 只做一次意图抽取，输出 `trip_intent`       | prompt + 结构化输出                | 高：下游约束来自这里                                    | 保留                                     | 降低“重复理解用户”带来的不一致            |
| `rule_router/smart_router/fault_check_router`（`backend/app/agents/rule_router.py`）          | 规则路由决定 `next`（支持并发）             | 状态条件分支 + 幂等保护                 | 中高：稳定驱动 worker 执行                             | 降级（可合并到 `Supervisor` 或直接纯函数暴露）         | 减少职责重叠，降低学习成本               |
| `Supervisor` 包装器（`backend/app/agents/supervisor.py`）                                        | 纯包装，调用 `smart_router`           | 多余 LLM 依赖参数 + 包装层             | 低到中（对效果不决定）                                   | 降级/合并（让 Supervisor 彻底纯逻辑）              | 让“路由中心”更清晰，可删掉无意义抽象         |
| `GraphBuilder`（`backend/app/agents/graph/builder.py`）                                       | 构建 StateGraph，定义节点和边            | 并发路由与边的连接方式                   | 中：框架决定是否会丢状态                                  | 保留                                     | 保证并发写入的状态合并语义仍正确            |
| `WorkerExecutor/BaseWorker`（`backend/app/agents/workers.py`）                                | 创建 react agent + 工具调用 + 解析/校验写回 | agent 构建、工具 schema、解析/清洗、并发控制 | 中高：能提升证据质量（attractions/hotels/weather/routes） | 保留；但删/合并未使用函数、统一清洗策略                   | 降低“读代码成本”，减少失败分支引发的质量波动     |
| `web_search` 与增强包装（`backend/app/agents/tools.py`，以及 workers 的 `create_enhanced_web_search`） | 通过外部检索补充攻略/避坑等                  | 网络不确定性 + 可能的多次调用              | 中：能增强描述，但若没缓存会让成本/波动大                         | 保留但降级为“受控模式”（缓存/限频/降低 count）           | 降低胡编概率（可通过减少不必要自由发挥），同时稳定输出 |
| `Planner`（`backend/app/agents/workers.py` 中 `class Planner`）                                | 强类型汇总成 `TripPlan`               | 输出规范 + 字段清洗 + meals/叙事生成      | 高：最终质量看这里                                     | 保留，但必须做 grounding 强约束（尤其 meals、routes） | 让“最终文案质量”与“证据是否存在”一致        |
| routes 字段清洗 `_extract_planner_fields`（`workers.py`）                                         | 控制进入 Planner 的字段体量              | 字段白名单错误/不匹配会直接清空 routes       | 高：若 routes 清空，Planner 无证据                     | 保留并修复；不要只做“叙事变好”                       | 提升 routes 相关信息的真实性与稳定性      |
| `TripPlanStore`（`backend/app/agents/utils/trip_store.py`）                                   | 保存历史行程（描述称还要做相似检索）              | SQLite 读写 + 如果没被读取就是无效复杂度     | 低到中（当前看仅 save，无检索注入闭环）                        | 降级/删除 retrieval 部分；保留简单 save 可作为日志     | 减少“看似有 RAG 但其实没用”的复杂度       |
| `utils/parsers.py`                                                                          | 解析 final_plan 并构造 `TripPlan`    | JSON 解析兜底逻辑                   | 低到中：保障兜底但不直接提升质量                              | 保留但裁剪未使用路径（如某些分支永不到）                   | 降低兜底逻辑的阅读成本                 |


---

### 2. “局部死代码/冗余抽象”的裁剪候选（会显著降复杂度）

这部分不是为了质量提升，而是为了你“重新能掌控系统”。裁剪后你会更快理解闭环在哪、失败点在哪。

1. `backend/.trae/documents/代码重构计划 - 消除死代码与架构优化.md` 中列出的未使用变量/函数（例如 `agent_output.py` 的死代码、`workers.py` 的未用方法等）属于高收益裁剪。
2. `backend/app/agents/workers.py` 中存在看起来“为某节点服务”的辅助函数，但实际节点走的是别的 context_builder（例如 route 坐标上下文函数是否被用到，需要以实际调用链为准）。这类未被引用的函数优先删除。

---

### 3. meals/避雷 的裁剪建议（直接影响“少胡编”）

在你当前系统没有餐饮检索 worker 的前提下，建议把 meals 的策略明确成下面之一（选定后才能稳定评估）：

1. 降级路线：允许 meals 字段存在，但强约束 `days[].meals` 为空数组或只给通用“就餐建议”，不输出具体餐厅名/避雷事实。
2. 升级路线：新增 `meal_agent`（需要有餐饮检索证据：MCP POI 或 web_search），Planner 只负责排版和把证据写进餐饮条目。

如果你不选路线，而是继续让 Planner 自行“想象餐厅/避雷”，那么无论你怎样重构代码，质量评估都会长期不稳定。