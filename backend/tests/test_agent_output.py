"""
单元测试：agent_output.py validators + parse_and_validate

运行方式：
    cd backend
    pytest tests/test_agent_output.py -v
"""

import pytest
from app.agents.schemas.agent_output import (
    AttractionData,
    WeatherData,
    HotelData,
    RouteData,
)
from app.agents.workers import parse_and_validate, AGENT_REGISTRY


# ============================================================
# AttractionData
# ============================================================


class TestAttractionData:
    def test_normal_input(self):
        a = AttractionData(name="故宫", address="北京市东城区")
        assert a.name == "故宫"
        assert a.ticket_price == 0
        assert a.rating == 0.0

    def test_coordinate_string_to_float(self):
        a = AttractionData(
            name="x", address="x", longitude="116.397", latitude="39.917"
        )
        assert isinstance(a.longitude, float)
        assert abs(a.longitude - 116.397) < 0.001

    def test_coordinate_invalid_returns_none(self):
        a = AttractionData(name="x", address="x", longitude="无效坐标")
        assert a.longitude is None

    def test_rating_clamp_upper(self):
        a = AttractionData(name="x", address="x", rating=9.9)
        assert a.rating == 5.0

    def test_rating_clamp_lower(self):
        a = AttractionData(name="x", address="x", rating=-1.0)
        assert a.rating == 0.0

    def test_rating_from_string(self):
        a = AttractionData(name="x", address="x", rating="4.5分")
        assert abs(a.rating - 4.5) < 0.01

    def test_ticket_price_with_unit(self):
        a = AttractionData(name="x", address="x", ticket_price="60元")
        assert a.ticket_price == 60

    def test_visit_duration_with_unit(self):
        a = AttractionData(name="x", address="x", visit_duration="120分钟")
        assert a.visit_duration == 120

    def test_ticket_price_invalid_defaults_zero(self):
        a = AttractionData(name="x", address="x", ticket_price="免费")
        assert a.ticket_price == 0


# ============================================================
# WeatherData
# ============================================================


class TestWeatherData:
    def test_date_standard_format(self):
        w = WeatherData(date="2025-06-01")
        assert w.date == "2025-06-01"

    def test_date_slash_format(self):
        w = WeatherData(date="2025/06/01")
        assert w.date == "2025-06-01"

    def test_date_chinese_format(self):
        w = WeatherData(date="2025年6月1日")
        assert w.date == "2025-06-01"

    def test_date_dot_format(self):
        w = WeatherData(date="2025.06.01")
        assert w.date == "2025-06-01"

    def test_date_unrecognized_kept_as_is(self):
        w = WeatherData(date="第一天")
        assert w.date == "第一天"

    def test_temperature_with_celsius_symbol(self):
        w = WeatherData(date="2025-06-01", day_temp="28℃", night_temp="24度")
        assert w.day_temp == 28
        assert w.night_temp == 24

    def test_negative_temperature(self):
        w = WeatherData(date="2025-01-01", day_temp="-3°C")
        assert w.day_temp == -3

    def test_temperature_integer_passthrough(self):
        w = WeatherData(date="2025-06-01", day_temp=30)
        assert w.day_temp == 30


# ============================================================
# HotelData
# ============================================================


class TestHotelData:
    def test_normal_input(self):
        h = HotelData(name="如家酒店", price_range="200-300元")
        assert h.name == "如家酒店"
        assert h.price_range == "200-300元"

    def test_rating_clamp(self):
        h = HotelData(name="x", rating=6.0)
        assert h.rating == 5.0

    def test_rating_from_string(self):
        h = HotelData(name="x", rating="4.8分")
        assert abs(h.rating - 4.8) < 0.01

    def test_price_range_kept_as_text(self):
        h = HotelData(name="x", price_range="约300-500元/晚")
        assert h.price_range == "约300-500元/晚"


# ============================================================
# RouteData
# ============================================================


class TestRouteData:
    def test_normal_input(self):
        r = RouteData(origin="A", destination="B", transportation="步行")
        assert r.duration == 0

    def test_duration_minutes_only(self):
        r = RouteData(
            origin="A", destination="B", transportation="x", duration="30分钟"
        )
        assert r.duration == 30

    def test_duration_hours_only(self):
        r = RouteData(origin="A", destination="B", transportation="x", duration="2小时")
        assert r.duration == 120

    def test_duration_hours_and_minutes(self):
        r = RouteData(
            origin="A", destination="B", transportation="x", duration="1小时30分钟"
        )
        assert r.duration == 90

    def test_duration_approximate(self):
        r = RouteData(
            origin="A", destination="B", transportation="x", duration="约45分"
        )
        assert r.duration == 45

    def test_duration_plain_integer(self):
        r = RouteData(origin="A", destination="B", transportation="x", duration=20)
        assert r.duration == 20

    def test_duration_invalid_defaults_zero(self):
        r = RouteData(origin="A", destination="B", transportation="x", duration="未知")
        assert r.duration == 0


# ============================================================
# parse_and_validate
# ============================================================


class TestParseAndValidate:
    def _config(self):
        return AGENT_REGISTRY["weather"]

    def test_valid_json_returns_items(self):
        text = '{"weather_info": [{"date": "2025-06-01", "day_temp": 28}]}'
        result = parse_and_validate(text, self._config())
        assert len(result) == 1
        assert result[0]["date"] == "2025-06-01"

    def test_invalid_json_returns_empty(self):
        result = parse_and_validate("这不是JSON", self._config())
        assert result == []

    def test_empty_list_returns_empty(self):
        result = parse_and_validate('{"weather_info": []}', self._config())
        assert result == []

    def test_wrong_key_returns_empty(self):
        result = parse_and_validate(
            '{"weather": [{"date": "2025-06-01"}]}', self._config()
        )
        assert result == []

    def test_partial_invalid_items_skipped(self):
        text = '{"weather_info": [{}, {"date": "2025-06-01", "day_temp": 25}]}'
        result = parse_and_validate(text, self._config())
        assert len(result) == 1
        assert result[0]["date"] == "2025-06-01"

    def test_validator_applied_inside_parse(self):
        text = '{"weather_info": [{"date": "2025年6月1日", "day_temp": "28℃"}]}'
        result = parse_and_validate(text, self._config())
        assert len(result) == 1
        assert result[0]["date"] == "2025-06-01"
        assert result[0]["day_temp"] == 28

    def test_attraction_config_with_data_cleaner(self):
        config = AGENT_REGISTRY.get("search") or AGENT_REGISTRY.get("attraction")
        if config is None:
            pytest.skip("attraction/search config not found")
        text = '{"attractions": [{"name": "故宫", "address": "暂无"}]}'
        result = parse_and_validate(text, config)
        if result:
            assert result[0].get("address", "") == ""

    def test_json_embedded_in_text(self):
        text = '好的，以下是天气数据：{"weather_info": [{"date": "2025-06-01"}]} 希望对您有帮助。'
        result = parse_and_validate(text, self._config())
        assert len(result) == 1
