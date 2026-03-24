"""数据模型定义"""

from typing import List, Optional, Union
from pydantic import BaseModel, Field
from datetime import date


# ============ 请求模型 ============


class TripRequest(BaseModel):
    """旅行规划请求"""

    city: str = Field(..., description="目的地城市", example="深圳南山")
    start_date: str = Field(
        ..., description="开始日期 YYYY-MM-DD", example="2025-06-01"
    )
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="步行")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    preferences: List[str] = Field(
        default=[], description="旅行偏好标签", example=["休闲", "美食"]
    )
    free_text_input: Optional[str] = Field(
        default="", description="额外要求", example="我住我哥家不用订酒店"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "city": "深圳南山",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "步行",
                "accommodation": "经济型酒店",
                "preferences": ["休闲", "美食"],
                "free_text_input": "我住我哥家不用订酒店",
            }
        }


# ============ 响应模型 (Moonshot 兼容版) ============


class Location(BaseModel):
    """位置信息(经纬度坐标)"""

    longitude: float = Field(..., description="经度", ge=-180, le=180)
    latitude: float = Field(..., description="纬度", ge=-90, le=90)


class Attraction(BaseModel):
    """景点信息"""

    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Optional[Location] = None  # Moonshot 兼容：不用 Field(default=None)
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: str = Field(default="景点", description="景点类别")
    rating: float = Field(default=0.0, description="评分")
    photos: List[str] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: str = Field(default="", description="POI ID")
    image_url: str = Field(default="", description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元)")


class Meal(BaseModel):
    """餐饮推荐（区域级别，避免幻觉）"""

    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    location_hint: str = Field(
        default="", description="推荐就餐区域，如'陈家祠周边'或'酒店附近步行街'"
    )
    style: str = Field(
        default="", description="餐饮品类/风格，如'粤式早茶'、'平价海鲜'"
    )
    price_range: str = Field(default="", description="预估人均价格区间，如'50-80元/人'")


class Hotel(BaseModel):
    """酒店信息"""

    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = None  # Moonshot 兼容
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")


class Budget(BaseModel):
    """预算信息"""

    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")


class DayPlan(BaseModel):
    """单日行程"""

    date: str = Field(default="", description="日期 YYYY-MM-DD")
    day_index: int = Field(default=0, description="第几天(从0开始)")
    description: str = Field(default="", description="当日行程描述")
    transportation: str = Field(default="", description="交通方式")
    accommodation: str = Field(default="", description="住宿")
    hotel: Optional[Hotel] = None  # Moonshot 兼容
    attractions: List[Attraction] = Field(default_factory=list, description="景点列表")
    meals: List[Meal] = Field(default_factory=list, description="餐饮列表")


class WeatherInfo(BaseModel):
    """天气信息"""

    date: str = Field(default="", description="日期")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: int = Field(default=0, description="白天温度(摄氏度)")
    night_temp: int = Field(default=0, description="夜间温度(摄氏度)")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")


class TripPlan(BaseModel):
    """旅行计划"""

    city: str = Field(default="", description="目的地城市")
    start_date: str = Field(default="", description="开始日期")
    end_date: str = Field(default="", description="结束日期")
    days: List[DayPlan] = Field(default_factory=list, description="每日行程")
    weather_info: List[WeatherInfo] = Field(
        default_factory=list, description="天气信息"
    )
    overall_suggestions: str = Field(default="", description="总体建议")
    budget: Optional[Budget] = None  # Moonshot 兼容


class TripPlanResponse(BaseModel):
    """旅行计划响应"""

    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = None  # Moonshot 兼容


# ============ 错误响应 ============


class ErrorResponse(BaseModel):
    """错误响应"""

    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = None  # Moonshot 兼容
