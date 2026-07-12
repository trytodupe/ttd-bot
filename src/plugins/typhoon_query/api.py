"""
API client for China Meteorological Administration (CMA) typhoon data.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# CMA typhoon API endpoints
_TYPHOON_LIST_URL = "https://typhoon.nmc.cn/weatherApi/typhoon/info"
_TYPHOON_DETAIL_URL = "https://typhoon.nmc.cn/weatherApi/typhoon/info/{tfid}"

# Strength level mapping
STRENGTH_LEVELS: dict[str, str] = {
    "TD": "热带低压",
    "TS": "热带风暴",
    "STS": "强热带风暴",
    "TY": "台风",
    "STY": "强台风",
    "SuperTY": "超强台风",
    "": "未知",
}


@dataclass(frozen=True)
class TyphoonPoint:
    """A single point in typhoon's track."""
    time: str
    longitude: float
    latitude: float
    strong: str  # Strength code
    power: int  # Wind level (等级)
    speed: float  # Wind speed (m/s)
    pressure: int  # Central pressure (hPa)
    direction: str  # Movement direction
    speed_km: int  # Movement speed (km/h)
    strength_cn: str = ""  # Chinese strength name

    @property
    def strength_display(self) -> str:
        return self.strength_cn or STRENGTH_LEVELS.get(self.strong, self.strong)


@dataclass(frozen=True)
class TyphoonInfo:
    """Basic typhoon information."""
    tfid: str  # Typhoon ID
    name: str  # Chinese name
    ename: str  # English name
    starttime: str
    endtime: str | None = None
    current_point: TyphoonPoint | None = None
    points: tuple[TyphoonPoint, ...] = ()


@dataclass
class TyphoonCache:
    """Cache for typhoon data."""
    data: Any = None
    timestamp: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Global cache
_cache = TyphoonCache()


def _parse_point(point_data: dict[str, Any]) -> TyphoonPoint:
    """Parse a single typhoon point from API response."""
    return TyphoonPoint(
        time=str(point_data.get("time", "")),
        longitude=float(point_data.get("longitude", 0)),
        latitude=float(point_data.get("latitude", 0)),
        strong=str(point_data.get("strong", "")),
        power=int(point_data.get("power", 0)),
        speed=float(point_data.get("speed", 0)),
        pressure=int(point_data.get("pressure", 0)),
        direction=str(point_data.get("movedirection", "")),
        speed_km=int(point_data.get("movespeed", 0)),
        strength_cn=str(point_data.get("strength_cn", "")),
    )


def _parse_typhoon(typhoon_data: dict[str, Any]) -> TyphoonInfo:
    """Parse typhoon info from API response."""
    points_data = typhoon_data.get("points", [])
    points = tuple(_parse_point(p) for p in points_data) if points_data else ()
    
    # Current point is the last one
    current_point = points[-1] if points else None
    
    return TyphoonInfo(
        tfid=str(typhoon_data.get("tfid", "")),
        name=str(typhoon_data.get("name", "")),
        ename=str(typhoon_data.get("ename", "")),
        starttime=str(typhoon_data.get("starttime", "")),
        endtime=typhoon_data.get("endtime"),
        current_point=current_point,
        points=points,
    )


async def _fetch_json(url: str, timeout: int) -> dict[str, Any] | None:
    """Fetch JSON from URL with error handling."""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


async def get_typhoon_list(timeout: int = 10) -> list[TyphoonInfo]:
    """
    Get list of currently active typhoons.
    Returns list of TyphoonInfo objects.
    """
    now = time.time()
    
    # Check cache
    async with _cache.lock:
        if _cache.data and (now - _cache.timestamp) < 600:
            return _cache.data
    
    # Fetch from API
    data = await _fetch_json(_TYPHOON_LIST_URL, timeout)
    if not data:
        return []
    
    typhoon_list = data.get("typhoon", [])
    if not typhoon_list:
        return []
    
    result = [_parse_typhoon(t) for t in typhoon_list]
    
    # Update cache
    async with _cache.lock:
        _cache.data = result
        _cache.timestamp = now
    
    return result


async def get_typhoon_detail(tfid: str, timeout: int = 10) -> TyphoonInfo | None:
    """
    Get detailed typhoon information by ID.
    Returns TyphoonInfo or None if not found.
    """
    url = _TYPHOON_DETAIL_URL.format(tfid=tfid)
    data = await _fetch_json(url, timeout)
    if not data:
        return None
    
    typhoon_data = data.get("typhoon")
    if not typhoon_data:
        return None
    
    return _parse_typhoon(typhoon_data)
