"""
Tests for typhoon query plugin.
"""

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def typhoon_modules():
    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    api_module = importlib.import_module("typhoon_query.api")
    formatter_module = importlib.import_module("typhoon_query.formatter")
    geodata_module = importlib.import_module("typhoon_query.geodata")
    return api_module, formatter_module, geodata_module


# =============================================================================
# Geodata tests
# =============================================================================


class TestGeodata:
    def test_point_in_polygon_basic(self, typhoon_modules):
        """Test basic point-in-polygon logic."""
        _, _, geodata_module = typhoon_modules
        _point_in_polygon = geodata_module._point_in_polygon
        
        square = ((0, 0), (10, 0), (10, 10), (0, 10))
        assert _point_in_polygon(5, 5, square) is True
        assert _point_in_polygon(15, 5, square) is False
        assert _point_in_polygon(5, 15, square) is False

    def test_point_in_polygon_edge(self, typhoon_modules):
        """Test point on edge of polygon."""
        _, _, geodata_module = typhoon_modules
        _point_in_polygon = geodata_module._point_in_polygon
        
        square = ((0, 0), (10, 0), (10, 10), (0, 10))
        # Edge points may vary by implementation, but should not crash
        result = _point_in_polygon(0, 5, square)
        assert isinstance(result, bool)

    def test_get_province_fujian(self, typhoon_modules):
        """Test coordinate in Fujian province."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Xiamen: ~118.1°E, 24.5°N
        result = get_province(118.1, 24.5)
        assert result == "福建省"

    def test_get_province_guangdong(self, typhoon_modules):
        """Test coordinate in Guangdong province."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Guangzhou: ~113.3°E, 23.1°N
        result = get_province(113.3, 23.1)
        assert result == "广东省"

    def test_get_province_zhejiang(self, typhoon_modules):
        """Test coordinate in Zhejiang province."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Hangzhou: ~120.2°E, 30.3°N
        result = get_province(120.2, 30.3)
        assert result == "浙江省"

    def test_get_province_shanghai(self, typhoon_modules):
        """Test coordinate in Shanghai."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Shanghai: ~121.5°E, 31.2°N
        result = get_province(121.5, 31.2)
        assert result == "上海市"

    def test_get_province_taiwan(self, typhoon_modules):
        """Test coordinate in Taiwan."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Taipei: ~121.5°E, 25.0°N
        result = get_province(121.5, 25.0)
        assert result == "台湾省"

    def test_get_province_hainan(self, typhoon_modules):
        """Test coordinate in Hainan."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Haikou: ~110.3°E, 20.0°N
        result = get_province(110.3, 20.0)
        assert result == "海南省"

    def test_get_province_inland(self, typhoon_modules):
        """Test coordinate in inland province."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Wuhan: ~114.3°E, 30.6°N
        result = get_province(114.3, 30.6)
        assert result == "湖北省"

    def test_get_province_unknown(self, typhoon_modules):
        """Test coordinate outside China."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Tokyo: ~139.7°E, 35.7°N
        result = get_province(139.7, 35.7)
        assert result is None

    def test_get_province_ocean(self, typhoon_modules):
        """Test coordinate in Pacific Ocean."""
        _, _, geodata_module = typhoon_modules
        get_province = geodata_module.get_province
        
        # Pacific: ~140.0°E, 10.0°N
        result = get_province(140.0, 10.0)
        assert result is None

    def test_get_province_abbreviation(self, typhoon_modules):
        """Test province abbreviation lookup."""
        _, _, geodata_module = typhoon_modules
        get_province_abbreviation = geodata_module.get_province_abbreviation
        
        # Xiamen: ~118.1°E, 24.5°N
        result = get_province_abbreviation(118.1, 24.5)
        assert result == "闽"


# =============================================================================
# API parsing tests
# =============================================================================


class TestApiParsing:
    def test_parse_point_basic(self, typhoon_modules):
        """Test parsing a typhoon point."""
        api_module, _, _ = typhoon_modules
        _parse_point = api_module._parse_point
        
        data = {
            "time": "2023-07-28 08:00",
            "longitude": 118.7,
            "latitude": 24.6,
            "strong": "TY",
            "power": 14,
            "speed": 42,
            "pressure": 955,
            "movedirection": "WNW",
            "movespeed": 20,
        }
        point = _parse_point(data)
        
        assert point.time == "2023-07-28 08:00"
        assert point.longitude == 118.7
        assert point.latitude == 24.6
        assert point.strong == "TY"
        assert point.power == 14
        assert point.speed == 42
        assert point.pressure == 955
        assert point.direction == "WNW"
        assert point.speed_km == 20

    def test_parse_point_missing_fields(self, typhoon_modules):
        """Test parsing point with missing fields."""
        api_module, _, _ = typhoon_modules
        _parse_point = api_module._parse_point
        
        data = {
            "time": "2023-07-28 08:00",
            "longitude": 118.7,
            "latitude": 24.6,
        }
        point = _parse_point(data)
        
        assert point.time == "2023-07-28 08:00"
        assert point.strong == ""
        assert point.power == 0
        assert point.speed == 0.0

    def test_parse_typhoon_basic(self, typhoon_modules):
        """Test parsing a typhoon object."""
        api_module, _, _ = typhoon_modules
        _parse_typhoon = api_module._parse_typhoon
        
        data = {
            "tfid": "2305",
            "name": "杜苏芮",
            "ename": "Doksuri",
            "starttime": "2023-07-21 02:00",
            "endtime": None,
            "points": [
                {
                    "time": "2023-07-28 08:00",
                    "longitude": 118.7,
                    "latitude": 24.6,
                    "strong": "TY",
                    "power": 14,
                    "speed": 42,
                    "pressure": 955,
                    "movedirection": "WNW",
                    "movespeed": 20,
                }
            ],
        }
        typhoon = _parse_typhoon(data)
        
        assert typhoon.tfid == "2305"
        assert typhoon.name == "杜苏芮"
        assert typhoon.ename == "Doksuri"
        assert len(typhoon.points) == 1
        assert typhoon.current_point is not None
        assert typhoon.current_point.longitude == 118.7

    def test_parse_typhoon_empty_points(self, typhoon_modules):
        """Test parsing typhoon with no points."""
        api_module, _, _ = typhoon_modules
        _parse_typhoon = api_module._parse_typhoon
        
        data = {
            "tfid": "2305",
            "name": "杜苏芮",
            "ename": "Doksuri",
            "starttime": "2023-07-21 02:00",
            "points": [],
        }
        typhoon = _parse_typhoon(data)
        
        assert typhoon.tfid == "2305"
        assert typhoon.points == ()
        assert typhoon.current_point is None


# =============================================================================
# Formatter tests
# =============================================================================


class TestFormatter:
    def test_format_coordinates_east_north(self, typhoon_modules):
        """Test formatting east/north coordinates."""
        _, formatter_module, _ = typhoon_modules
        _format_coordinates = formatter_module._format_coordinates
        
        result = _format_coordinates(118.7, 24.6)
        assert result == "118.7°E, 24.6°N"

    def test_format_coordinates_west_south(self, typhoon_modules):
        """Test formatting west/south coordinates."""
        _, formatter_module, _ = typhoon_modules
        _format_coordinates = formatter_module._format_coordinates
        
        result = _format_coordinates(-118.7, -24.6)
        assert result == "118.7°W, 24.6°S"

    def test_format_location_with_province(self, typhoon_modules):
        """Test formatting location with province."""
        _, formatter_module, _ = typhoon_modules
        _format_location_with_province = formatter_module._format_location_with_province
        
        result = _format_location_with_province(118.7, 24.6)
        assert "118.7°E, 24.6°N" in result
        assert "【福建省】" in result

    def test_format_location_without_province(self, typhoon_modules):
        """Test formatting location without province."""
        _, formatter_module, _ = typhoon_modules
        _format_location_with_province = formatter_module._format_location_with_province
        
        result = _format_location_with_province(139.7, 35.7)
        assert "139.7°E, 35.7°N" in result
        assert "【" not in result

    def test_format_strength_ty(self, typhoon_modules):
        """Test formatting typhoon strength."""
        api_module, formatter_module, _ = typhoon_modules
        _format_strength = formatter_module._format_strength
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        assert _format_strength(point) == "台风"

    def test_format_strength_super_ty(self, typhoon_modules):
        """Test formatting super typhoon strength."""
        api_module, formatter_module, _ = typhoon_modules
        _format_strength = formatter_module._format_strength
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="SuperTY",
            power=17,
            speed=65,
            pressure=900,
            direction="WNW",
            speed_km=20,
        )
        assert _format_strength(point) == "超强台风"

    def test_format_wind_info_with_level_and_speed(self, typhoon_modules):
        """Test formatting wind info with both level and speed."""
        api_module, formatter_module, _ = typhoon_modules
        _format_wind_info = formatter_module._format_wind_info
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        assert _format_wind_info(point) == "14级（42m/s）"

    def test_format_wind_info_unknown(self, typhoon_modules):
        """Test formatting wind info with unknown values."""
        api_module, formatter_module, _ = typhoon_modules
        _format_wind_info = formatter_module._format_wind_info
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=0,
            speed=0,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        assert _format_wind_info(point) == "未知"

    def test_format_movement_normal(self, typhoon_modules):
        """Test formatting normal movement."""
        api_module, formatter_module, _ = typhoon_modules
        _format_movement = formatter_module._format_movement
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        assert _format_movement(point) == "WNW 20km/h"

    def test_format_movement_stationary(self, typhoon_modules):
        """Test formatting stationary movement."""
        api_module, formatter_module, _ = typhoon_modules
        _format_movement = formatter_module._format_movement
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="",
            speed_km=0,
        )
        assert _format_movement(point) == "少动"

    def test_format_time_with_date(self, typhoon_modules):
        """Test formatting time with date."""
        _, formatter_module, _ = typhoon_modules
        _format_time = formatter_module._format_time
        
        assert _format_time("2023-07-28 08:00") == "07月28日 08:00"

    def test_format_time_empty(self, typhoon_modules):
        """Test formatting empty time."""
        _, formatter_module, _ = typhoon_modules
        _format_time = formatter_module._format_time
        
        assert _format_time("") == "未知"

    def test_format_typhoon_summary(self, typhoon_modules):
        """Test formatting typhoon summary."""
        api_module, formatter_module, _ = typhoon_modules
        format_typhoon_summary = formatter_module.format_typhoon_summary
        TyphoonInfo = api_module.TyphoonInfo
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        typhoon = TyphoonInfo(
            tfid="2305",
            name="杜苏芮",
            ename="Doksuri",
            starttime="2023-07-21 02:00",
            current_point=point,
            points=(point,),
        )
        summary = format_typhoon_summary(typhoon)
        
        assert "杜苏芮" in summary
        assert "Doksuri" in summary
        assert "2305" in summary
        assert "118.7°E, 24.6°N" in summary
        assert "【福建省】" in summary
        assert "14级" in summary
        assert "42m/s" in summary
        assert "955 hPa" in summary

    def test_format_typhoon_summary_no_point(self, typhoon_modules):
        """Test formatting typhoon with no current point."""
        api_module, formatter_module, _ = typhoon_modules
        format_typhoon_summary = formatter_module.format_typhoon_summary
        TyphoonInfo = api_module.TyphoonInfo
        
        typhoon = TyphoonInfo(
            tfid="2305",
            name="杜苏芮",
            ename="Doksuri",
            starttime="2023-07-21 02:00",
        )
        summary = format_typhoon_summary(typhoon)
        assert "暂无数据" in summary

    def test_format_typhoon_list_empty(self, typhoon_modules):
        """Test formatting empty typhoon list."""
        _, formatter_module, _ = typhoon_modules
        format_typhoon_list = formatter_module.format_typhoon_list
        
        result = format_typhoon_list([])
        assert "无活跃台风" in result

    def test_format_typhoon_list_single(self, typhoon_modules):
        """Test formatting single typhoon in list."""
        api_module, formatter_module, _ = typhoon_modules
        format_typhoon_list = formatter_module.format_typhoon_list
        TyphoonInfo = api_module.TyphoonInfo
        TyphoonPoint = api_module.TyphoonPoint
        
        point = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        typhoon = TyphoonInfo(
            tfid="2305",
            name="杜苏芮",
            ename="Doksuri",
            starttime="2023-07-21 02:00",
            current_point=point,
            points=(point,),
        )
        result = format_typhoon_list([typhoon])
        
        assert "1个" in result
        assert "杜苏芮" in result
        assert "福建省" in result

    def test_format_typhoon_list_multiple(self, typhoon_modules):
        """Test formatting multiple typhoons."""
        api_module, formatter_module, _ = typhoon_modules
        format_typhoon_list = formatter_module.format_typhoon_list
        TyphoonInfo = api_module.TyphoonInfo
        TyphoonPoint = api_module.TyphoonPoint
        
        point1 = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        point2 = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=130.0,
            latitude=20.0,
            strong="TS",
            power=8,
            speed=20,
            pressure=990,
            direction="NW",
            speed_km=15,
        )
        typhoon1 = TyphoonInfo(
            tfid="2305",
            name="杜苏芮",
            ename="Doksuri",
            starttime="2023-07-21 02:00",
            current_point=point1,
            points=(point1,),
        )
        typhoon2 = TyphoonInfo(
            tfid="2306",
            name="卡努",
            ename="Khanun",
            starttime="2023-07-28 08:00",
            current_point=point2,
            points=(point2,),
        )
        result = format_typhoon_list([typhoon1, typhoon2])
        
        assert "2个" in result
        assert "杜苏芮" in result
        assert "卡努" in result

    def test_format_typhoon_full_with_forecast(self, typhoon_modules):
        """Test formatting full typhoon with forecast."""
        api_module, formatter_module, _ = typhoon_modules
        format_typhoon_full = formatter_module.format_typhoon_full
        TyphoonInfo = api_module.TyphoonInfo
        TyphoonPoint = api_module.TyphoonPoint
        
        current = TyphoonPoint(
            time="2023-07-28 08:00",
            longitude=118.7,
            latitude=24.6,
            strong="TY",
            power=14,
            speed=42,
            pressure=955,
            direction="WNW",
            speed_km=20,
        )
        forecast1 = TyphoonPoint(
            time="2023-07-28 20:00",
            longitude=117.5,
            latitude=25.8,
            strong="TY",
            power=13,
            speed=38,
            pressure=960,
            direction="WNW",
            speed_km=18,
        )
        forecast2 = TyphoonPoint(
            time="2023-07-29 08:00",
            longitude=116.2,
            latitude=27.1,
            strong="STS",
            power=11,
            speed=30,
            pressure=975,
            direction="WNW",
            speed_km=15,
        )
        typhoon = TyphoonInfo(
            tfid="2305",
            name="杜苏芮",
            ename="Doksuri",
            starttime="2023-07-21 02:00",
            current_point=current,
            points=(current, forecast1, forecast2),
        )
        result = format_typhoon_full(typhoon)
        
        assert "杜苏芮" in result
        assert "路径预报" in result
        assert "07月28日 20:00" in result
        assert "07月29日 08:00" in result
