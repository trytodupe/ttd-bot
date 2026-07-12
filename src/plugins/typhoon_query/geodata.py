"""
Simplified province-level geodata for China.
Uses bounding boxes + simplified polygons for fast point-in-polygon lookup.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProvinceInfo:
    name: str
    abbreviation: str
    # Simplified polygon as list of (lon, lat) tuples
    # For coastal provinces, this is a rough bounding polygon
    polygon: tuple[tuple[float, float], ...]


def _point_in_polygon(lon: float, lat: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# Simplified province boundaries (lon, lat) polygons
# Coastal and near-coastal provinces that typhoons typically affect
_PROVINCE_DATA: list[ProvinceInfo] = [
    ProvinceInfo(
        name="台湾省",
        abbreviation="台",
        polygon=(
            (120.0, 21.8), (122.0, 21.8), (122.0, 25.3), (120.0, 25.3),
        ),
    ),
    ProvinceInfo(
        name="海南省",
        abbreviation="琼",
        polygon=(
            (108.5, 18.0), (111.5, 18.0), (111.5, 20.2), (108.5, 20.2),
        ),
    ),
    ProvinceInfo(
        name="广东省",
        abbreviation="粤",
        polygon=(
            (109.6, 20.2), (117.3, 20.2), (117.3, 25.5), (109.6, 25.5),
        ),
    ),
    ProvinceInfo(
        name="广西壮族自治区",
        abbreviation="桂",
        polygon=(
            (104.3, 20.9), (112.0, 20.9), (112.0, 26.4), (104.3, 26.4),
        ),
    ),
    ProvinceInfo(
        name="福建省",
        abbreviation="闽",
        polygon=(
            (115.8, 23.5), (120.5, 23.5), (120.5, 28.3), (115.8, 28.3),
        ),
    ),
    ProvinceInfo(
        name="浙江省",
        abbreviation="浙",
        polygon=(
            (118.0, 27.1), (122.5, 27.1), (122.5, 31.2), (118.0, 31.2),
        ),
    ),
    ProvinceInfo(
        name="上海市",
        abbreviation="沪",
        polygon=(
            (120.9, 30.7), (122.0, 30.7), (122.0, 31.5), (120.9, 31.5),
        ),
    ),
    ProvinceInfo(
        name="江苏省",
        abbreviation="苏",
        polygon=(
            (116.4, 30.8), (121.5, 30.8), (121.5, 35.1), (116.4, 35.1),
        ),
    ),
    ProvinceInfo(
        name="山东省",
        abbreviation="鲁",
        polygon=(
            (114.8, 34.4), (122.7, 34.4), (122.7, 38.4), (114.8, 38.4),
        ),
    ),
    ProvinceInfo(
        name="河北省",
        abbreviation="冀",
        polygon=(
            (113.5, 36.0), (120.0, 36.0), (120.0, 42.6), (113.5, 42.6),
        ),
    ),
    ProvinceInfo(
        name="天津市",
        abbreviation="津",
        polygon=(
            (116.7, 38.6), (118.0, 38.6), (118.0, 40.0), (116.7, 40.0),
        ),
    ),
    ProvinceInfo(
        name="辽宁省",
        abbreviation="辽",
        polygon=(
            (118.8, 38.7), (125.8, 38.7), (125.8, 43.5), (118.8, 43.5),
        ),
    ),
    ProvinceInfo(
        name="江西省",
        abbreviation="赣",
        polygon=(
            (113.6, 24.5), (118.5, 24.5), (118.5, 30.1), (113.6, 30.1),
        ),
    ),
    ProvinceInfo(
        name="湖南省",
        abbreviation="湘",
        polygon=(
            (108.8, 24.6), (114.3, 24.6), (114.3, 30.1), (108.8, 30.1),
        ),
    ),
    ProvinceInfo(
        name="安徽省",
        abbreviation="皖",
        polygon=(
            (114.9, 29.4), (119.9, 29.4), (119.9, 34.6), (114.9, 34.6),
        ),
    ),
    ProvinceInfo(
        name="湖北省",
        abbreviation="鄂",
        polygon=(
            (108.4, 29.0), (116.1, 29.0), (116.1, 33.3), (108.4, 33.3),
        ),
    ),
    ProvinceInfo(
        name="河南省",
        abbreviation="豫",
        polygon=(
            (110.4, 31.4), (116.6, 31.4), (116.6, 36.4), (110.4, 36.4),
        ),
    ),
    ProvinceInfo(
        name="吉林省",
        abbreviation="吉",
        polygon=(
            (121.6, 40.8), (131.3, 40.8), (131.3, 46.3), (121.6, 46.3),
        ),
    ),
    ProvinceInfo(
        name="黑龙江省",
        abbreviation="黑",
        polygon=(
            (121.2, 43.4), (135.1, 43.4), (135.1, 53.6), (121.2, 53.6),
        ),
    ),
    ProvinceInfo(
        name="香港特别行政区",
        abbreviation="港",
        polygon=(
            (113.8, 22.1), (114.4, 22.1), (114.4, 22.6), (113.8, 22.6),
        ),
    ),
    ProvinceInfo(
        name="澳门特别行政区",
        abbreviation="澳",
        polygon=(
            (113.5, 22.0), (113.7, 22.0), (113.7, 22.3), (113.5, 22.3),
        ),
    ),
]


def get_province(lon: float, lat: float) -> str | None:
    """
    Get province name from longitude and latitude.
    Returns province name if found, None otherwise.
    """
    for province in _PROVINCE_DATA:
        if _point_in_polygon(lon, lat, province.polygon):
            return province.name
    return None


def get_province_abbreviation(lon: float, lat: float) -> str | None:
    """
    Get province abbreviation from longitude and latitude.
    Returns abbreviation if found, None otherwise.
    """
    for province in _PROVINCE_DATA:
        if _point_in_polygon(lon, lat, province.polygon):
            return province.abbreviation
    return None
