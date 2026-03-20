"""MCP 工具管理模块 - 共享 MCP 服务器实例

相对上一版的改动：
1. 删除 MCPToolsManagerWithRetry 子类（猴子补丁方式存在 session 重建后丢失的风险）
2. 重试逻辑移入 create_tool_wrapper，每个工具调用天然具备重试能力
3. 全局单例直接使用 MCPToolsManager，代码减少约 30 行
"""

import asyncio
import os
import logging
from typing import List, Optional, Dict

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
from ..config import get_settings

logger = logging.getLogger(__name__)


# ==========================================
# 工具输入 Schema 定义
# ==========================================


class WeatherToolInput(BaseModel):
    city: str = Field(..., description="完整的城市名称，如 '北京市' 或 '深圳'")


class TextSearchToolInput(BaseModel):
    keywords: str = Field(..., description="搜索关键词，如 '景点'、'酒店'")
    city: str = Field(..., description="城市名称，如 '北京'、'深圳'")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内搜索")
    district: Optional[str] = Field(default=None, description="区县名称，如 '南山区'")


class DirectionToolInput(BaseModel):
    origin: str = Field(..., description="起点地址")
    destination: str = Field(..., description="终点地址")
    city: str = Field(..., description="城市名称")


class SearchDetailToolInput(BaseModel):
    id: str = Field(..., description="POI ID")


# ==========================================
# 工具包装器
# ==========================================

_SCHEMA_MAPPING: Dict[str, type] = {
    "maps_weather": WeatherToolInput,
    "maps_text_search": TextSearchToolInput,
    "maps_direction_transit": DirectionToolInput,
    "maps_direction_walking": DirectionToolInput,
    "maps_direction_driving": DirectionToolInput,
    "maps_direction_bicycling": DirectionToolInput,
    "maps_search_detail": SearchDetailToolInput,
}


def register_tool_schemas(extra_schemas: Dict[str, type]) -> None:
    """注册额外的工具 schema（由 workers.py 调用）"""
    _SCHEMA_MAPPING.update(extra_schemas)


def create_tool_wrapper(
    mcp_tool: BaseTool,
    args_schema: type,
    tool_name: str,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> StructuredTool:
    """
    为 MCP 工具创建包装器，内置重试逻辑。

    重试逻辑放在这里而不是 MCPToolsManager 子类的原因：
    - 子类用猴子补丁替换 session.call_tool，session 重建后补丁丢失
    - 包装器与 session 生命周期无关，重试能力始终有效
    """

    async def wrapper(**kwargs) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return await mcp_tool.ainvoke(kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"[{tool_name}] 调用失败 ({attempt}/{max_retries}): "
                        f"{str(e)[:100]}，{retry_delay}s 后重试..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(
                        f"[{tool_name}] 已重试 {max_retries} 次，全部失败: {e}"
                    )
        raise last_error

    return StructuredTool(
        name=tool_name,
        description=mcp_tool.description,
        func=None,
        coroutine=wrapper,
        args_schema=args_schema,
    )


def wrap_mcp_tools(mcp_tools: List[BaseTool]) -> List[BaseTool]:
    """为所有 MCP 工具创建包装器"""
    wrapped = []
    for tool in mcp_tools:
        if tool.name in _SCHEMA_MAPPING:
            wrapped_tool = create_tool_wrapper(
                tool, _SCHEMA_MAPPING[tool.name], tool.name
            )
            wrapped.append(wrapped_tool)
            logger.info(f"[MCP] 已包装工具: {tool.name}")
        else:
            wrapped.append(tool)
            logger.info(f"[MCP] 工具 {tool.name} 使用原始版本（无 schema 定义）")
    return wrapped


# ==========================================
# MCPToolsManager - 单例
# ==========================================


class MCPToolsManager:
    """
    MCP 工具管理器

    管理高德 MCP 服务器的生命周期，所有 Agent 共享同一个 MCP 服务器进程。
    重试逻辑已下沉至 create_tool_wrapper，此类只负责生命周期管理。
    """

    _instance: Optional["MCPToolsManager"] = None
    _initialized: bool = False

    def __new__(cls) -> "MCPToolsManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if MCPToolsManager._initialized:
            return
        self._session: Optional[ClientSession] = None
        self._tools: List[BaseTool] = []
        self._tools_dict: Dict[str, BaseTool] = {}
        self._client_context = None
        self._session_context = None
        MCPToolsManager._initialized = True
        logger.info("MCPToolsManager 实例已创建")

    async def initialize(self) -> None:
        """初始化 MCP 服务器连接"""
        if self._session is not None:
            logger.info("MCP 服务器已初始化，跳过")
            return

        settings = get_settings()
        if not settings.amap_api_key:
            raise ValueError("未配置高德地图 API Key，请在环境变量中设置 AMAP_API_KEY")

        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@amap/amap-maps-mcp-server"],
            env={
                "AMAP_MAPS_API_KEY": settings.amap_api_key,
                "AMAP_API_KEY": settings.amap_api_key,
                **dict(os.environ),
            },
        )

        logger.info("正在启动高德 MCP 服务器...")
        try:
            self._client_context = stdio_client(server_params)
            self._read, self._write = await self._client_context.__aenter__()

            self._session_context = ClientSession(self._read, self._write)
            self._session = await self._session_context.__aenter__()
            await self._session.initialize()

            raw_tools = await load_mcp_tools(self._session)
            self._tools = wrap_mcp_tools(raw_tools)
            self._tools_dict = {tool.name: tool for tool in self._tools}

            logger.info(
                f"MCP 服务器初始化成功，共加载 {len(self._tools)} 个工具: "
                f"{list(self._tools_dict.keys())}"
            )
        except Exception as e:
            logger.error(f"MCP 服务器初始化失败: {e}", exc_info=True)
            await self.cleanup()
            raise

    async def cleanup(self) -> None:
        """清理 MCP 服务器资源"""
        logger.info("正在清理 MCP 服务器资源...")
        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"关闭会话时出错: {e}")
        if self._client_context:
            try:
                await self._client_context.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"关闭客户端时出错: {e}")

        self._session = None
        self._client_context = None
        self._session_context = None
        self._tools = []
        self._tools_dict = {}
        logger.info("MCP 服务器资源已清理")

    def get_tools(self) -> List[BaseTool]:
        return self._tools

    def get_tools_by_names(self, names: List[str]) -> List[BaseTool]:
        # 支持混合类型：字符串工具名称 或 实际工具对象
        result = []
        missing = []

        for name in names:
            if isinstance(name, str):
                # 字符串工具名称
                if name in self._tools_dict:
                    result.append(self._tools_dict[name])
                else:
                    missing.append(name)
            elif isinstance(name, BaseTool):
                # 已经是工具对象，直接使用
                result.append(name)
            else:
                missing.append(str(name))

        if missing:
            logger.warning(f"[MCP] 以下工具未找到: {missing}")

        return result

    def list_available_tools(self) -> List[str]:
        return list(self._tools_dict.keys())

    @property
    def is_initialized(self) -> bool:
        return self._session is not None


# ==========================================
# 全局单例 + 便捷函数
# ==========================================

_mcp_manager: Optional[MCPToolsManager] = None


def get_mcp_manager() -> MCPToolsManager:
    """获取 MCP 工具管理器单例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPToolsManager()
    return _mcp_manager


async def initialize_mcp_tools() -> MCPToolsManager:
    manager = get_mcp_manager()
    await manager.initialize()
    return manager


async def cleanup_mcp_tools() -> None:
    global _mcp_manager
    if _mcp_manager:
        await _mcp_manager.cleanup()
        _mcp_manager = None


class AmapTools:
    """高德 MCP 工具名称常量"""

    TEXT_SEARCH = "maps_text_search"
    WEATHER = "maps_weather"
    DIRECTION_DRIVING = "maps_direction_driving"
    DIRECTION_WALKING = "maps_direction_walking"
    DIRECTION_TRANSIT = "maps_direction_transit"
    DIRECTION_BICYCLING = "maps_direction_bicycling"
    SEARCH_DETAIL = "maps_search_detail"
    GEOCODE = "maps_geocode"
    REVERSE_GEOCODE = "maps_reverse_geocode"
    IP_LOCATION = "maps_ip_location"
