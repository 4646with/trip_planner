"""Agent 输出结构定义 - 含完整 field_validator 的数据模型

设计原则：
- 模型负责"类型层"脏数据修复（字符串转数值、范围夹紧、格式归一化）
- data_cleaner 钩子负责"业务层"脏数据修复（写在 AGENT_REGISTRY 里）
- 两层分离，职责清晰

已删除：
- AgentOutput（包装类，state 架构下不需要）
- OUTPUT_KEYS（信息已内联至 AGENT_REGISTRY）
- OUTPUT_SCHEMAS（信息已内联至 AGENT_REGISTRY）
"""

import re
import logging
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ==========================================
# AttractionData
# ==========================================


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

    @field_validator("longitude", "latitude", mode="before")
    @classmethod
    def parse_coordinate(cls, v) -> Optional[float]:
        """处理字符串坐标，如 "113.xxx" -> 113.xxx，无法解析则返回 None"""
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    @field_validator("rating", mode="before")
    @classmethod
    def clamp_rating(cls, v) -> float:
        """评分范围夹紧到 [0.0, 5.0]，处理字符串如 "4.5分" """
        if isinstance(v, str):
            match = re.search(r"\d+\.?\d*", v)
            v = float(match.group()) if match else 0.0
        try:
            return max(0.0, min(5.0, float(v)))
        except (ValueError, TypeError):
            return 0.0

    @field_validator("ticket_price", "visit_duration", mode="before")
    @classmethod
    def parse_int_field(cls, v) -> int:
        """处理 LLM 返回 "60元" 或 "120分钟" 这类附带单位的字符串"""
        if isinstance(v, str):
            match = re.search(r"\d+", v)
            return int(match.group()) if match else 0
        try:
            return int(v) if v is not None else 0
        except (ValueError, TypeError):
            return 0


# ==========================================
# WeatherData
# ==========================================


class WeatherData(BaseModel):
    """单天天气数据"""

    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field("", description="白天天气")
    night_weather: str = Field("", description="夜间天气")
    day_temp: int = Field(0, description="白天温度(摄氏度)")
    night_temp: int = Field(0, description="夜间温度(摄氏度)")
    wind_direction: str = Field("", description="风向")
    wind_power: str = Field("", description="风力")

    @field_validator("date", mode="before")
    @classmethod
    def normalize_date(cls, v) -> str:
        """
        统一日期格式为 YYYY-MM-DD。
        处理高频变体：
          "2025/06/01"  -> "2025-06-01"
          "2025年6月1日" -> "2025-06-01"
          "2025.06.01"  -> "2025-06-01"
          无法解析则保留原值并记录警告（容错优先）
        """
        v = str(v).strip()
        formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"]
        for fmt in formats:
            try:
                return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        logger.warning(f"[WeatherData] 无法解析日期格式: '{v}'，保留原始值")
        return v

    @field_validator("day_temp", "night_temp", mode="before")
    @classmethod
    def parse_temperature(cls, v) -> int:
        """
        处理带单位的温度字符串：
          "28℃"  -> 28
          "28度"  -> 28
          "-3°C" -> -3
        """
        if isinstance(v, str):
            match = re.search(r"-?\d+", v)
            return int(match.group()) if match else 0
        try:
            return int(v) if v is not None else 0
        except (ValueError, TypeError):
            return 0


# ==========================================
# HotelData
# ==========================================


class HotelData(BaseModel):
    """酒店数据结构"""

    name: str = Field(..., description="酒店名称")
    address: str = Field("", description="酒店地址")
    price_range: str = Field("", description="价格范围，纯文本如 '300-500元'")
    rating: float = Field(0.0, description="评分")
    distance: str = Field("", description="距离景点距离，纯文本")

    @field_validator("rating", mode="before")
    @classmethod
    def clamp_rating(cls, v) -> float:
        """评分范围夹紧到 [0.0, 5.0]，处理字符串如 "4.5分"。

        price_range 刻意保持纯文本：下游 Planner 按文本展示，
        无需在此做数值解析，避免处理无消费方的数据。
        """
        if isinstance(v, str):
            match = re.search(r"\d+\.?\d*", v)
            v = float(match.group()) if match else 0.0
        try:
            return max(0.0, min(5.0, float(v)))
        except (ValueError, TypeError):
            return 0.0


# ==========================================
# RouteData
# ==========================================


class RouteData(BaseModel):
    """路线数据"""

    origin: str = Field(..., description="起点")
    destination: str = Field(..., description="终点")
    transportation: str = Field(..., description="交通方式")
    duration: int = Field(0, description="预计耗时(分钟)")
    distance: str = Field("", description="距离，纯文本如 '2公里'")
    route_detail: str = Field("", description="路线详情")

    @field_validator("duration", mode="before")
    @classmethod
    def parse_duration(cls, v) -> int:
        """
        处理带单位的耗时字符串，支持混合格式：
          "30分钟"       -> 30
          "1小时30分钟"  -> 90
          "约45分"       -> 45
          "2小时"        -> 120
        """
        if isinstance(v, str):
            hours = re.search(r"(\d+)\s*小时", v)
            minutes = re.search(r"(\d+)\s*分", v)
            total = 0
            if hours:
                total += int(hours.group(1)) * 60
            if minutes:
                total += int(minutes.group(1))
            if total > 0:
                return total
            plain = re.search(r"\d+", v)
            return int(plain.group()) if plain else 0
        try:
            return int(v) if v is not None else 0
        except (ValueError, TypeError):
            return 0



