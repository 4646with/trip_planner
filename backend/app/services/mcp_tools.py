"""MCP 工具管理模块 - 共享 MCP 服务器实例"""

import asyncio
import os
import functools
import logging
from typing import List, Optional, Dict, Any

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
    """天气查询工具输入参数"""

    city: str = Field(..., description="完整的城市名称，如 '北京市' 或 '深圳'")


class TextSearchToolInput(BaseModel):
    """文本搜索工具输入参数"""

    keywords: str = Field(..., description="搜索关键词，如 '景点'、'酒店'")
    city: str = Field(..., description="城市名称，如 '北京'、'深圳'")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内搜索")
    district: Optional[str] = Field(default=None, description="区县名称，如 '南山区'")


class DirectionToolInput(BaseModel):
    """路线规划工具输入参数（支持公交/步行/驾车/骑行）"""

    origin: str = Field(..., description="起点地址")
    destination: str = Field(..., description="终点地址")
    city: str = Field(..., description="城市名称")


class SearchDetailToolInput(BaseModel):
    """POI详情查询工具输入参数"""

    id: str = Field(..., description="POI ID")


# ==========================================
# 工具包装器函数
# ==========================================


def create_tool_wrapper(
    mcp_tool: BaseTool, args_schema: BaseModel, tool_name: str
) -> StructuredTool:
    """
    为 MCP 工具创建包装器，添加明确的参数 schema

    Args:
        mcp_tool: 原始 MCP 工具
        args_schema: 参数 schema
        tool_name: 工具名称

    Returns:
        包装后的 StructuredTool
    """

    async def wrapper(**kwargs) -> str:
        """工具包装器，确保参数正确传递"""
        try:
            # 调用底层 MCP 工具
            result = await mcp_tool.ainvoke(kwargs)
            return result
        except Exception as e:
            logger.error(f"工具 {tool_name} 调用失败: {e}")
            raise

    return StructuredTool(
        name=tool_name,
        description=mcp_tool.description,
        func=None,
        coroutine=wrapper,
        args_schema=args_schema,
    )


def wrap_mcp_tools(mcp_tools: List[BaseTool]) -> List[BaseTool]:
    """
    为所有 MCP 工具创建包装器

    Args:
        mcp_tools: 原始 MCP 工具列表

    Returns:
        包装后的工具列表
    """
    # 工具名称到 schema 的映射
    schema_mapping = {
        "maps_weather": WeatherToolInput,
        "maps_text_search": TextSearchToolInput,
        "maps_direction_transit": DirectionToolInput,
        "maps_direction_walking": DirectionToolInput,
        "maps_direction_driving": DirectionToolInput,
        "maps_direction_bicycling": DirectionToolInput,
        "maps_search_detail": SearchDetailToolInput,
    }

    wrapped_tools = []
    for tool in mcp_tools:
        if tool.name in schema_mapping:
            # 创建包装器
            wrapped_tool = create_tool_wrapper(
                tool, schema_mapping[tool.name], tool.name
            )
            wrapped_tools.append(wrapped_tool)
            logger.info(f"已为工具 {tool.name} 创建包装器")
        else:
            # 对于没有定义 schema 的工具，直接使用原始工具
            wrapped_tools.append(tool)
            logger.info(f"工具 {tool.name} 使用原始版本")

    return wrapped_tools


class MCPToolsManager:
    """
    MCP 工具管理器

    管理高德 MCP 服务器的生命周期，所有智能体共享同一个 MCP 服务器进程
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
        self._server_params: Optional[StdioServerParameters] = None
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

        self._server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@amap/amap-maps-mcp-server"],
            env={
                "AMAP_MAPS_API_KEY": settings.amap_api_key,
                "AMAP_API_KEY": settings.amap_api_key,  # 也设置 AMAP_API_KEY 作为备用
                **dict(os.environ),
            },
        )

        logger.info("正在启动高德 MCP 服务器...")

        try:
            logger.info("创建 MCP 客户端上下文...")
            self._client_context = stdio_client(self._server_params)

            logger.info("进入客户端上下文...")
            self._read, self._write = await self._client_context.__aenter__()

            logger.info("创建 MCP 会话...")
            self._session_context = ClientSession(self._read, self._write)

            logger.info("进入会话上下文...")
            self._session = await self._session_context.__aenter__()

            logger.info("初始化 MCP 会话...")
            await self._session.initialize()

            logger.info("加载 MCP 工具...")
            raw_tools = await load_mcp_tools(self._session)

            # 为工具创建包装器，添加明确的参数 schema
            logger.info("为 MCP 工具创建包装器...")
            self._tools = wrap_mcp_tools(raw_tools)
            self._tools_dict = {tool.name: tool for tool in self._tools}

            logger.info(f"MCP 服务器初始化成功，共加载 {len(self._tools)} 个工具")
            logger.info(f"已加载的工具: {list(self._tools_dict.keys())}")

        except Exception as e:
            logger.error(f"MCP 服务器初始化失败: {e}")
            import traceback

            traceback.print_exc()
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
        """获取所有可用工具"""
        return self._tools

    def get_tools_by_names(self, names: List[str]) -> List[BaseTool]:
        """根据名称获取指定工具"""
        return [self._tools_dict[name] for name in names if name in self._tools_dict]

    def list_available_tools(self) -> List[str]:
        """列出所有可用工具名称"""
        return list(self._tools_dict.keys())

    @property
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._session is not None


class MCPToolsManagerWithRetry(MCPToolsManager):
    """带重试机制的 MCP 工具管理器"""

    async def initialize(self) -> None:
        await super().initialize()
        if self._session and not hasattr(self, "_original_call_tool"):
            self._original_call_tool = self._session.call_tool

            async def safe_call_tool(name, arguments=None, **kwargs):
                for attempt in range(3):
                    try:
                        return await self._original_call_tool(name, arguments, **kwargs)
                    except Exception as e:
                        if attempt == 2:
                            raise
                        logger.warning(
                            f"⚠️ 工具 {name} 调用失败 ({attempt + 1}/3): {str(e)[:100]}"
                        )
                        await asyncio.sleep(1)

            self._session.call_tool = safe_call_tool
            logger.info("✅ 已添加 MCP 工具重试机制")


_mcp_manager: Optional[MCPToolsManagerWithRetry] = None


def get_mcp_manager() -> MCPToolsManagerWithRetry:
    """获取 MCP 工具管理器单例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPToolsManagerWithRetry()
    return _mcp_manager


async def initialize_mcp_tools() -> MCPToolsManagerWithRetry:
    """初始化 MCP 工具"""
    manager = get_mcp_manager()
    await manager.initialize()
    return manager


async def cleanup_mcp_tools() -> None:
    """清理 MCP 工具"""
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
