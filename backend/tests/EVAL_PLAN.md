## 质量优先评估计划（EVAL_PLAN）

目标：在你“学会并裁剪”这套系统时，能用同一套指标判断“改复杂了但效果不增”到底是不是真的没提升，还是被某些矛盾/胡编问题掩盖了。

本计划只关注质量（accuracy/grounding/可用性），不以成本/延迟为主。

---

### 1. 评估对象

- 系统输出的 `TripPlan`（前端展示主要依赖 `days[].attractions`、`days[].meals`、`days[].hotel`、`weather_info`、`overall_suggestions`、`budget`）
- 规划链路是否稳定：是否能成功返回、是否出现错误列表、结构是否符合 schema

---

### 2. 指标定义（质量优先）

下面这些指标可以从 `backend/tests/output/current.json` 直接读出来（`test_refactor_quality.py` 已经把 result 保存到该文件）。

#### 2.1 结构可用性（必须达标）

1. **成功率**：调用 `plan_trip_async`（或你实际使用的接口）没有抛异常，返回值存在 `days` 字段。
2. **基础字段完整率**：
   - 每天 `days[i].attractions` 是数组
   - 每个 attraction 至少具备：`name/address`（以及可选的 `visit_duration/description`）
3. **天气完整率**（如果开启 weather 获取）：
   - `weather_info` 数量 > 0
   - 每条 weather 至少具备 `date/day_weather/night_weather/day_temp/night_temp`（字段名正确）

#### 2.2 Grounding 可信度（少胡编）

1. **routes 可信度代理指标（可自动化）**
   - 由于 `route_agent` 侧会做幻觉过滤（只允许 origin/destination 出现在 attractions 名称集合里），因此可以用：
     - `routes`（如果 Planner 进而写入）不应包含 attractions 里不存在的名字
   - 若你的版本没有把 `routes` 直接展示出来，那么至少检查系统中 `final_plan` 的相关路线描述是否避免“明显不在行程内的地名/地址”。

2. **meals 胡编风险（建议人工抽样为主）**
   - 当前系统缺少餐饮检索类 worker（只有 attraction/weather/hotel/route），但 schema 和 prompt 都允许 meals。
   - 评估时重点抽样检查：
     - `days[].meals` 中是否出现了“非常具体的餐厅名 + 价格 + 评价/避坑细节”，但系统却没有任何餐饮证据来源（这属于高风险胡编形态）
   - 量化建议（可选）：
     - meals 非空率（>0 就算“用了 meals”）
     - meals 具体度（包含“具体套餐/评分/避雷说法”的条目占比，手动打分 0/1）

#### 2.3 内容质量（可用性 + 细节）

1. **可执行性**：
   - `overall_suggestions` 是否是“可落地建议”（例如时间分配/交通方式/穿着提醒），而不是纯空泛话术
2. **不自相矛盾**：
   - 与用户约束（`free_text_input/trip_intent.hard_constraints`）是否明显冲突

---

### 3. 基线运行方式（对照用）

使用现有测试脚本：[`backend/tests/test_refactor_quality.py`](backend/tests/test_refactor_quality.py)

建议的运行方式：

1. 进入后端目录：
   - `cd backend`
2. 运行质量验证：
   - `python tests/test_refactor_quality.py`
3. 查看输出：
   - `backend/tests/output/current.json`
   - `backend/tests/output/REPORT.md`（脚本会生成一个简版报告）

你可以把每次运行的 `current.json` 另存为带时间戳的文件（比如 `baseline_YYYYMMDD_HHMMSS.json`），以便对比“改动前后”的差异。

---

### 4. 对比策略（怎么判断“真的提升了”）

建议你按“先修矛盾点 -> 再观察质量指标变化”的节奏做对比：

1. 每次只做一类关键修改（例如先修 route prompt/tool 输入不一致，再做 planner 清洗修复）。
2. 对照维度只看：
   - attraction/hotel/weather 是否更完整（结构完整率）
   - routes 相关描述是否更少胡编（或至少更不离谱）
   - meals/避雷内容是否更符合“缺证据时只能通用建议”的策略
3. 如果你发现所有指标变化都很小，才考虑进一步裁剪模块，而不是继续加功能复杂度。

---

### 5. 输出检查清单（做抽样评审用）

每次运行后，打开 `backend/tests/output/current.json`，快速检查：

- `days.length` 是否符合 `travel_days` 的预期（或至少不为 0）
- 每天 `attractions.length` 是否合理（不为空，且条目字段不缺失）
- `weather_info.length` 是否 > 0（如果开启了天气）
- `days[*].meals.length`：
  - 若系统没有餐饮证据来源：优先确保 meals 为空或仅给通用建议（避免具体餐厅名的“看似很准但其实无证据”内容）
- `overall_suggestions` 是否与硬约束不冲突

