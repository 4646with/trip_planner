# 部署约束

## 必须单进程异步模式运行

本项目使用 MCPToolsManager 单例管理高德 MCP 服务器进程（stdio 长连接）。
该单例基于 Python 模块级变量实现，**不支持多进程部署**。

多进程部署（如 gunicorn -w 4）会导致：
- 每个 worker 进程各自 fork 出一个 `npx @amap/amap-maps-mcp-server` 子进程
- 进程退出时 MCP 子进程清理不可靠，导致僵尸进程堆积

### 正确的启动命令

```bash
# 正确：单进程 + 异步并发
uvicorn app.main:app --workers 1 --host 0.0.0.0 --port 8000

# 错误：多进程会导致 MCP 实例泄漏
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

如需水平扩展，应在**容器层**扩展（多个单进程容器），而不是在进程层扩展。

## schema_version 说明

AgentState 中的 schema_version 当前为 1。
如果将来对 AgentState 做破坏性变更（删除或重命名字段），需要将 schema_version 递增，并在 _build_initial_state 中更新默认值。
如启用了 LangGraph 持久化（checkpointer），还需要编写对应的迁移脚本。
