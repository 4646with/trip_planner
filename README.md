# TripPlanner 智能旅行助手 🌍✈️

基于 LangGraph 构建的多智能体旅行规划系统，采用 Supervisor-Worker 协作模式，集成高德地图 MCP 服务，提供个性化的旅行计划生成。

## ✨ 功能特点

- 🧠 **多智能体协作**: Supervisor 智能路由 + 多个专业 Worker Agent（景点、天气、酒店、路线）
- 🤖 **LangGraph 工作流**: 基于 LangGraph StateGraph 的有向无环图编排
- 🗺️ **高德地图集成**: 通过 MCP 协议接入高德地图服务，支持景点搜索、路线规划、天气查询
- 🔄 **自动工具调用**: Agent 自动调用 MCP 工具获取实时 POI、路线和天气信息
- 🎨 **现代化前端**: Vue3 + TypeScript + Vite，响应式设计
- 📱 **完整功能**: 包含住宿、交通、餐饮和景点游览时间推荐

## 🏗️ 技术栈

### 后端
- **核心框架**: LangGraph (StateGraph)
- **LLM框架**: LangChain
- **API**: FastAPI
- **MCP工具**: amap-mcp-server (高德地图)
- **LLM**: 支持多种 LLM 提供商 (OpenAI, DeepSeek 等)

### 前端
- **框架**: Vue 3 + TypeScript
- **构建工具**: Vite
- **UI组件库**: Ant Design Vue
- **地图服务**: 高德地图 JavaScript API
- **HTTP客户端**: Axios

## 📁 项目结构

```
trip_planner/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── agents/            # 多智能体系统
│   │   │   ├── graph/        # LangGraph 构建器
│   │   │   │   └── builder.py
│   │   │   ├── schemas/      # 状态和类型定义
│   │   │   │   └── state.py
│   │   │   ├── prompts/      # Agent 提示词
│   │   │   │   └── agents.py
│   │   │   ├── utils/        # 工具函数
│   │   │   │   └── parsers.py
│   │   │   ├── supervisor.py # Supervisor 路由决策
│   │   │   ├── workers.py    # Worker Agent 管理器
│   │   │   ├── main.py       # 系统入口
│   │   │   └── tools.py      # 工具定义
│   │   ├── api/              # FastAPI 路由
│   │   │   ├── main.py
│   │   │   └── routes/
│   │   ├── services/         # 服务层
│   │   │   ├── llm_service.py
│   │   │   ├── mcp_tools.py
│   │   │   └── unsplash_service.py
│   │   ├── models/           # 数据模型
│   │   │   └── schemas.py
│   │   └── config.py         # 配置管理
│   ├── requirements.txt
│   ├── .env.example
│   └── run.py
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── views/            # 页面视图
│   │   ├── services/         # API 服务
│   │   └── types/            # TypeScript 类型
│   ├── package.json
│   └── vite.config.ts
└── README.md
```

## 🚀 快速开始

### 前提条件

- Python 3.10+
- Node.js 16+
- 高德地图 API 密钥 (Web 服务 API 和 Web 端 JS API)
- LLM API 密钥 (OpenAI/DeepSeek 等)

### 后端安装

1. 进入后端目录
```bash
cd backend
```

2. 创建虚拟环境
```bash
python -m venv venv
.\venv\Scripts\activate
```

3. 安装依赖
```bash
pip install -r requirements.txt
```

4. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 文件，填入你的 API 密钥
```

5. 启动后端服务
```bash
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端安装

1. 进入前端目录
```bash
cd frontend
```

2. 安装依赖
```bash
npm install
```

3. 配置环境变量
```bash
# 创建 .env 文件，填入高德地图 Web API Key 和 Web 端 JS API Key
cp .env.example .env
```

4. 启动开发服务器
```bash
npm run dev
```

5. 打开浏览器访问 `http://localhost:5173`

## 📝 使用指南

1. 在首页填写旅行信息:
   - 目的地城市
   - 旅行日期和天数
   - 交通方式偏好
   - 住宿偏好
   - 旅行风格标签

2. 点击"生成旅行计划"按钮

3. 系统将启动多智能体协作:
   - **Supervisor** 分析请求，决定调用哪个 Worker
   - **景点 Agent** 搜索目标城市热门景点
   - **天气 Agent** 查询目的地天气情况
   - **酒店 Agent** 推荐合适住宿
   - **路线 Agent** 规划交通路线
   - **Planner** 整合所有信息生成完整行程

4. 查看结果:
   - 每日详细行程
   - 景点信息与地图标记
   - 交通路线规划
   - 天气预报
   - 餐饮推荐

## 🔧 核心架构

### LangGraph 多智能体设计

项目采用 **Supervisor-Worker** 模式，通过 LangGraph StateGraph 实现智能协作：

```
┌─────────────┐
│  Supervisor │ ◄── 路由决策中心
└──────┬──────┘
       │
   ┌───┴───┬────────┬────────┐
   ▼       ▼        ▼        ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│景点  │ │天气  │ │酒店  │ │路线  │  Workers
│Agent │ │Agent │ │Agent │ │Agent │
└──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘
   └────────┴────────┴────────┘
              │
              ▼
       ┌────────────┐
       │  Planner   │ ◄── 行程汇总
       └────────────┘
              │
              ▼
           [END]
```

### 核心组件

#### Supervisor (路由决策器)
- 分析当前状态和对话历史
- 使用 `with_structured_output` 强制输出 Pydantic 模型
- 决定下一步调用哪个 Worker Agent

#### WorkerManager (Worker 管理器)
- 管理各个 Worker Agent 的创建和缓存
- 使用 `create_react_agent` 创建预构建 Agent
- 维护调用计数和并发控制

#### GraphBuilder (图构建器)
- 定义和构建 StateGraph 工作流
- 配置节点和边的连接关系
- 提供编译后的可执行图

#### MCP 工具集成
- 使用 `langchain-mcp-adapters` 加载 MCP 工具
- 自定义工具包装器，添加参数 schema
- 支持高德地图 POI 搜索、天气查询、路线规划

### 代码示例

```python
from app.agents.main import MapAgentsSystem
from app.models.schemas import TripRequest

# 初始化系统
system = MapAgentsSystem()
await system.initialize()

# 创建请求
request = TripRequest(
    city="北京",
    travel_days=3,
    start_date="2024-05-01",
    end_date="2024-05-03",
    transportation="地铁",
    accommodation="酒店",
    preferences=["历史古迹", "美食"],
    free_text_input="想去看升旗仪式"
)

# 生成计划
result = system.plan_trip(request)
```

## 📄 API 文档

启动后端服务后，访问 `http://localhost:8000/docs` 查看完整的 API 文档。

主要端点:
- `POST /api/map/plan` - 生成旅行计划
- `GET /api/map/poi` - 搜索 POI
- `GET /api/map/weather` - 查询天气
- `POST /api/map/route` - 规划路线

## 🤝 贡献指南

欢迎提交 Pull Request 或 Issue!

## 📜 开源协议
