# backend/app/agents/tools.py
"""LangGraph 工具定义 - 基于 MCP 的高德地图工具"""

import json
import os
import requests
from typing import List, Dict
import httpx
from langchain_core.tools import tool
from ..config import get_settings


@tool
async def web_search(query: str) -> str:
    """搜索网络获取景点攻略、餐厅推荐菜、旅行tips等详细信息

    使用方式：直接传入完整的搜索查询字符串（模板已固定，Agent 只填入实体名称）
    例如：
    - 景点："故宫博物院 游览攻略 必去理由 2024"
    - 餐厅："全聚德 北京 招牌菜 人均"
    """
    api_key = os.getenv("BOCHA_API_KEY")
    if not api_key:
        return "错误：未配置 BOCHA_API_KEY"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.bochaai.com/v1/web-search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "count": 3, "summary": True},
                timeout=10.0,
            )
            resp.raise_for_status()

        pages = resp.json().get("webPages", {}).get("value", [])
        if not pages:
            return "未找到相关搜索结果"

        return "\n".join(p.get("summary", "") for p in pages)
    except Exception as e:
        return f"搜索出错: {str(e)}"


# 保留 Unsplash 工具（非 MCP，直接调用）
@tool
def unsplash_image_search(query: str, count: int = 1) -> str:
    """从Unsplash搜索图片"""
    settings = get_settings()

    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query,
        "client_id": settings.unsplash_access_key,
        "per_page": count,
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    return json.dumps(data, ensure_ascii=False)


# 注意：高德地图工具现在通过 MCP 服务器提供
# 这些工具在 services/mcp_tools.py 中通过 get_mcp_manager().get_tools() 获取
#
# 可用的 MCP 工具包括：
# - maps_text_search: 搜索 POI（景点、酒店、餐厅等）
# - maps_weather: 查询城市天气
# - maps_direction_driving: 驾车路线规划（免费）
# - maps_direction_walking: 步行路线规划（免费）
# - maps_direction_transit: 公交路线规划（免费）
# - maps_direction_bicycling: 骑行路线规划（免费）
# - maps_search_detail: 获取 POI 详情
# - maps_geocode: 地址解析
# - maps_reverse_geocode: 逆地址解析
# - maps_ip_location: IP 定位
#
# 使用方式：
# from ..services.mcp_tools import get_mcp_manager, AmapTools
#
# mcp_manager = get_mcp_manager()
# tools = mcp_manager.get_tools_by_names([
#     AmapTools.TEXT_SEARCH,
#     AmapTools.WEATHER,
#     AmapTools.DIRECTION_DRIVING
# ])
# agent = create_react_agent(llm, tools=tools)
