# TripPlanner 日志缺失及进程冲突解决文档

## 1. 问题描述

在运行项目时，用户发现终端仅能看到 Uvicorn 的启动日志，却无法看到多智能体系统（Agents）内部的关键协作日志（如 Supervisor 的调度和 Worker 的搜索记录），且在尝试重启时频繁遇到端口占用（Bind Error）的情况。

## 2. 根因分析

### 2.1 日志系统的“静默”

- 项目中虽然大量使用了标准库 `logging.getLogger(__name__)`，但未在应用入口处调用 `logging.basicConfig()`。
- Python 默认情况下，如果根记录器（Root Logger）未配置，`INFO` 级别的日志会被丢弃。

### 2.2 Uvicorn 的日志干扰

- Uvicorn 在启动时会配置自己的 Loggers（`uvicorn.error`, `uvicorn.access`），这会导致应用层的标准日志与 Web 服务器日志产生割裂，甚至被 Web 服务器的配置覆盖。

### 2.3 Windows 进程存留（PID 728）

- 在使用 `reload=True` 模式运行 uvicorn 时，Windows 下有时会出现父进程已停但子进程（监听套接字）未释放的“僵尸”状态。
- 本次调试中发现 PID 728 一直占着 8000 端口，且该进程运行的是旧版本代码（无日志修复），导致用户观察到“修复无效”的假象。

## 3. 解决方案

### 3.1 强力日志初始化

我们创建了 `backend/app/utils/logging.py`，使用了 `force=True` 参数来重置全局配置：

```python
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
    force=True, # 强行覆盖已有配置
)
```

### 3.2 传播机制优化

通过设置 `u_logger.propagate = True`，让 Uvicorn 及自定义模块的日志全部汇聚到 Root Logger 统一处理，确保了输出格式的统一。

### 3.3 进程深度清理

使用了基于 PowerShell 的 PID 追溯与强制下线命令（`taskkill /F /T`），彻底清除了残留的 8000 端口监听者。

## 4. 验证结果

- **初始化验证**：成功输出 `🚀 日志系统强力初始化完成`。
- **业务验证**：前端发起请求后，终端成功刷新出 Agent 协作细节，系统可观测性大幅提升。

> [!TIP]
> **后续建议**：在 Windows 开发环境中，建议优先使用 `python run.py --no-reload` 进行调试，以避免 WatchFiles 产生的多进程复杂性。
