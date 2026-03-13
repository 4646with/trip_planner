"""Agent 输出结构定义 - 所有 Agent 必须返回结构化数据"""

from typing import List, Optional
from pydantic import BaseModel, Field


class AttractionData(BaseModel):
    """景点数据结构"""

    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="景点地址")
    longitude: Optional[float] = Field(None, description="经度")
    latitude: Optional[float] = Field(None, description="纬度")
    visit_duration: int = Field(120, description="建议游览时间(分钟)")
    description: str = Field("", description="景点描述")
    category: str = Field("景点", description="景点类别")
    rating: float = Field(0.0, description="评分")
    ticket_price: int = Field(0, description="门票价格(元)")


class WeatherData(BaseModel):
    """单天天气数据"""

    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field("", description="白天天气")
    night_weather: str = Field("", description="夜间天气")
    day_temp: int = Field(0, description="白天温度(摄氏度)")
    night_temp: int = Field(0, description="夜间温度(摄氏度)")
    wind_direction: str = Field("", description="风向")
    wind_power: str = Field("", description="风力")


class HotelData(BaseModel):
    """酒店数据结构"""

    name: str = Field(..., description="酒店名称")
    address: str = Field("", description="酒店地址")
    price_range: str = Field("", description="价格范围")
    rating: float = Field(0.0, description="评分")
    distance: str = Field("", description="距离景点距离")


class RouteData(BaseModel):
    """路线数据"""

    origin: str = Field(..., description="起点")
    destination: str = Field(..., description="终点")
    transportation: str = Field(..., description="交通方式")
    duration: int = Field(0, description="预计耗时(分钟)")
    distance: str = Field("", description="距离")
    route_detail: str = Field("", description="路线详情")


class AgentOutput(BaseModel):
    """Agent 统一输出结构 - 所有 Agent 必须遵循此格式"""

    success: bool = Field(..., description="是否成功获取数据")
    data: dict = Field(..., description="结构化数据")
    raw_text: str = Field("", description="原始文本(可选)")


# Agent 输出键名定义
OUTPUT_KEYS = {
    "attraction": "attractions",
    "weather": "weather_info",
    "hotel": "hotels",
    "route": "routes",
}

# Agent 输出数据模型
OUTPUT_SCHEMAS = {
    "attraction": List[AttractionData],
    "weather": List[WeatherData],
    "hotel": List[HotelData],
    "route": List[RouteData],
}
