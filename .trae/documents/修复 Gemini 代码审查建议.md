## 修复计划

### 高优先级（3个）

1. **SUPERVISOR 判断逻辑漏洞**
   - 文件: `backend/app/agents/prompts/agents.py`
   - 修改: 将 "agent_call_count > 0" 改为检查结果有效性

2. **PLANNER 预算计算太复杂**
   - 文件: `backend/app/agents/prompts/agents.py`
   - 修改: 移除 LLM 计算预算的要求，让代码层统一计算

3. **Agent Prompt 缓存问题**
   - 文件: `backend/app/agents/workers.py`
   - 修改: 每次使用当前 state 重新格式化 prompt，不缓存已格式化的 prompt

### 中优先级（2个）

4. **Weather Agent 输出格式**
   - 文件: `backend/app/agents/prompts/agents.py`
   - 修改: 要求结构化输出，便于 PLANNER 解析

5. **示例数据泄露**
   - 文件: `backend/app/agents/prompts/agents.py`
   - 修改: 北京坐标改为占位符

### 低优先级（2个）

6. **硬编码延迟**
   - 文件: `backend/app/agents/workers.py`
   - 修改: `sleep(2)` 改为可配置参数

7. **冗余代码清理**
   - 文件: `backend/app/agents/prompts/agents.py`
   - 修改: 移除未使用的 get_prompt 方法或常量导出