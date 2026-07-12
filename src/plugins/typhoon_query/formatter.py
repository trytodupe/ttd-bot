"""
Message formatter for typhoon information.
"""

from __future__ import annotations

from .api import TyphoonInfo, TyphoonPoint
from .geodata import get_province


def _format_coordinates(lon: float, lat: float) -> str:
    """Format longitude and latitude as string."""
    lon_dir = "E" if lon >= 0 else "W"
    lat_dir = "N" if lat >= 0 else "S"
    return f"{abs(lon):.1f}°{lon_dir}, {abs(lat):.1f}°{lat_dir}"


def _format_location_with_province(lon: float, lat: float) -> str:
    """Format coordinates with province name."""
    coords = _format_coordinates(lon, lat)
    province = get_province(lon, lat)
    if province:
        return f"{coords} 【{province}】"
    return coords


def _format_time(time_str: str) -> str:
    """Format time string for display."""
    if not time_str:
        return "未知"
    # API returns format like "2023-07-28 08:00"
    # Extract date and time parts
    parts = time_str.split(" ")
    if len(parts) == 2:
        date_part, time_part = parts
        # Simplify date: show only month-day
        date_parts = date_part.split("-")
        if len(date_parts) == 3:
            return f"{date_parts[1]}月{date_parts[2]}日 {time_part}"
    return time_str


def _format_strength(point: TyphoonPoint) -> str:
    """Format strength display."""
    return point.strength_display


def _format_wind_info(point: TyphoonPoint) -> str:
    """Format wind speed and level info."""
    level_text = f"{point.power}级" if point.power > 0 else ""
    speed_text = f"{point.speed:.0f}m/s" if point.speed > 0 else ""
    
    if level_text and speed_text:
        return f"{level_text}（{speed_text}）"
    elif level_text:
        return level_text
    elif speed_text:
        return speed_text
    return "未知"


def _format_movement(point: TyphoonPoint) -> str:
    """Format movement direction and speed."""
    if not point.direction or point.speed_km <= 0:
        return "少动"
    return f"{point.direction} {point.speed_km}km/h"


def format_typhoon_summary(typhoon: TyphoonInfo) -> str:
    """Format a concise typhoon summary."""
    if not typhoon.current_point:
        return f"🌀 {typhoon.name} ({typhoon.ename}) - 暂无数据"
    
    point = typhoon.current_point
    location = _format_location_with_province(point.longitude, point.latitude)
    
    lines = [
        f"🌀 台风信息",
        f"",
        f"名称：{typhoon.name} ({typhoon.ename})",
        f"编号：{typhoon.tfid}",
        f"中心位置：{location}",
        f"最大风力：{_format_wind_info(point)}",
        f"中心气压：{point.pressure} hPa",
        f"移向移速：{_format_movement(point)}",
        f"强度等级：{_format_strength(point)}",
        f"更新时间：{_format_time(point.time)}",
    ]
    
    return "\n".join(lines)


def format_typhoon_forecast(typhoon: TyphoonInfo, hours: int = 48) -> str:
    """Format typhoon forecast path."""
    if not typhoon.points or not typhoon.current_point:
        return ""
    
    # Get current observation time
    current_time = typhoon.current_point.time
    
    # Filter future points (after current observation time)
    future_points = []
    for point in typhoon.points:
        # Simple time comparison - API should return points in order
        if point.time > current_time:
            future_points.append(point)
    
    if not future_points:
        return ""
    
    lines = ["", "路径预报："]
    for point in future_points:
        location = _format_location_with_province(point.longitude, point.latitude)
        strength = _format_strength(point)
        lines.append(f"  {_format_time(point.time)} → {location}({strength})")
    
    return "\n".join(lines)


def format_typhoon_full(typhoon: TyphoonInfo, forecast_hours: int = 48) -> str:
    """Format full typhoon information with forecast."""
    summary = format_typhoon_summary(typhoon)
    forecast = format_typhoon_forecast(typhoon, forecast_hours)
    
    return summary + forecast


def format_typhoon_list(typhoons: list[TyphoonInfo]) -> str:
    """Format list of active typhoons."""
    if not typhoons:
        return "🌀 当前西太平洋无活跃台风"
    
    lines = [f"🌀 当前活跃台风：{len(typhoons)}个", ""]
    
    for i, typhoon in enumerate(typhoons, 1):
        if typhoon.current_point:
            point = typhoon.current_point
            location = _format_location_with_province(point.longitude, point.latitude)
            strength = _format_strength(point)
            lines.append(
                f"{i}. {typhoon.name} ({typhoon.ename})\n"
                f"   位置：{location}\n"
                f"   强度：{strength}\n"
                f"   风力：{_format_wind_info(point)}\n"
                f"   气压：{point.pressure} hPa"
            )
        else:
            lines.append(f"{i}. {typhoon.name} ({typhoon.ename}) - 暂无数据")
    
    return "\n".join(lines)
