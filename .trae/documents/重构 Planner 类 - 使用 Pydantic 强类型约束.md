## 修改目标
将 `workers.py` 中的 `Planner` 类从脆弱的 if...else 防御式解析，重构为使用 Pydantic + `with_structured_output` 的主动约束模式。

## 具体修改

### 1. 添加导入
在文件顶部添加：
```python
from pydantic import BaseModel, Field
from typing import Optional
```

### 2. 添加数据契约类（在 Planner 类之前）
- `DailyPlan` - 每日行程结构
- `TravelPlan` - 完整旅行计划结构

### 3. 完全替换 Planner 类
- 使用 `llm.with_structured_output(TravelPlan)` 强制结构化输出
- 删除 `_parse_plan` 方法中的脆弱 if...else 判断
- 简化 `generate` 方法逻辑
- 保留优雅降级机制

## 预期效果
- 代码量减少约 50%
- 消除手动 JSON 解析的脆弱性
- 大模型输出格式得到强制约束
- 类型安全得到保障