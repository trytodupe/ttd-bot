from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from nonebot import get_plugin_config, on_message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="etx-query",
    description="Query Elitebotix duel ratings by osu! username.",
    usage="etx <osu username>",
    config=Config,
)

plugin_config = get_plugin_config(Config)
_LOOKUP_URL_TEMPLATE = "https://osu.ppy.sh/users/{username}"
_API_LOOKUP_URL_TEMPLATE = "https://osu.ppy.sh/api/v2/users/{username}/osu"
_DUEL_RATING_URL_TEMPLATE = (
    "https://www.eliteronix.de/elitebotix/api/player-duelrating?u={user_id}"
)
_USERNAME_PATTERN = re.compile(r"^etx\s+(.+?)\s*$", re.IGNORECASE)
_VALID_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9 _\-\[\]]+$")
_USER_ID_PATTERN = re.compile(r"/users/(?P<user_id>\d+)(?:[/?#]|$)")

matcher = on_message(priority=20, block=True)
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_OSU_TOKEN_CACHE: tuple[str, float] | None = None


def _extract_username(plain_text: str) -> str | None:
    match = _USERNAME_PATTERN.match(plain_text.strip())
    if not match:
        return None
    username = match.group(1).strip()
    if not username:
        return None
    if not _VALID_USERNAME_PATTERN.fullmatch(username):
        return None
    return username


def _extract_user_id_from_location(location: str | None) -> str | None:
    if not location:
        return None
    match = _USER_ID_PATTERN.search(location)
    if not match:
        return None
    return match.group("user_id")


def _has_osu_oauth_config() -> bool:
    return bool(
        str(plugin_config.etx_osu_client_id).strip()
        and str(plugin_config.etx_osu_client_secret).strip()
    )


def _parse_rating(value: str | None) -> str:
    try:
        return f"{float(str(value)):.3f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_relative_age(updated_at_local: datetime, now: datetime | None = None) -> str:
    reference_now = now or datetime.now(_LOCAL_TZ)
    delta_seconds = max(0, int((reference_now - updated_at_local).total_seconds()))

    if delta_seconds < 60:
        return "~1m"
    if delta_seconds < 3600:
        return f"~{delta_seconds // 60}m"
    if delta_seconds < 86400:
        return f"~{delta_seconds // 3600}h"

    delta_days = delta_seconds // 86400
    if delta_days < 30:
        return f"~{delta_days}d"
    return f"~{max(1, delta_days // 30)}mo"


def _format_updated_at(updated_at: str | None, now: datetime | None = None) -> str:
    if not updated_at:
        return "updated at unknown"

    normalized = updated_at.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    local_dt = dt.astimezone(_LOCAL_TZ)
    formatted = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"updated at {formatted} ({_format_relative_age(local_dt, now=now)})"


def _format_duel_rating_message(
    username: str,
    payload: dict,
    now: datetime | None = None,
) -> str:
    user_id = str(payload.get("osuUserId", "")).strip() or "unknown"
    duel_rating = payload.get("duelRating") or {}
    lines = [
        f"{username} / {user_id}:",
        f"SR: {_parse_rating(duel_rating.get('osuDuelStarRating'))}",
        f"NM: {_parse_rating(duel_rating.get('osuNoModDuelStarRating'))}",
        f"HD: {_parse_rating(duel_rating.get('osuHiddenDuelStarRating'))}",
        f"HR: {_parse_rating(duel_rating.get('osuHardRockDuelStarRating'))}",
        f"DT: {_parse_rating(duel_rating.get('osuDoubleTimeDuelStarRating'))}",
        f"FM: {_parse_rating(duel_rating.get('osuFreeModDuelStarRating'))}",
        _format_updated_at(duel_rating.get("updatedAt"), now=now),
    ]
    return "\n".join(lines)


async def _get_osu_access_token(client: httpx.AsyncClient) -> str | None:
    global _OSU_TOKEN_CACHE

    if not _has_osu_oauth_config():
        return None

    now = time.time()
    if _OSU_TOKEN_CACHE and _OSU_TOKEN_CACHE[1] > now + 30:
        return _OSU_TOKEN_CACHE[0]

    response = await client.post(
        "https://osu.ppy.sh/oauth/token",
        headers={"Accept": "application/json"},
        data={
            "client_id": str(plugin_config.etx_osu_client_id).strip(),
            "client_secret": str(plugin_config.etx_osu_client_secret).strip(),
            "grant_type": "client_credentials",
            "scope": "public",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected osu OAuth response payload")

    access_token = str(payload.get("access_token", "")).strip()
    expires_in = int(payload.get("expires_in", 0) or 0)
    if not access_token:
        raise ValueError("osu OAuth token response missing access_token")

    _OSU_TOKEN_CACHE = (access_token, now + max(0, expires_in))
    return access_token


async def _lookup_user_by_api(
    client: httpx.AsyncClient,
    username: str,
) -> tuple[str, str] | None:
    access_token = await _get_osu_access_token(client)
    if not access_token:
        return None

    response = await client.get(
        _API_LOOKUP_URL_TEMPLATE.format(username=quote(f"@{username}", safe="@")),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected osu lookup response payload")

    user_id = str(payload.get("id", "")).strip()
    actual_username = str(payload.get("username", "")).strip() or username
    if not user_id.isdigit():
        raise ValueError("osu lookup response missing numeric id")
    return (user_id, actual_username)


async def _lookup_user_by_redirect(
    client: httpx.AsyncClient,
    username: str,
) -> tuple[str, str] | None:
    response = await client.get(
        _LOOKUP_URL_TEMPLATE.format(username=quote(username, safe="")),
        headers={"User-Agent": "Mozilla/5.0"},
        follow_redirects=False,
    )

    if response.status_code in (301, 302, 303, 307, 308):
        user_id = _extract_user_id_from_location(response.headers.get("Location"))
        if user_id:
            return (user_id, username)
        return None

    if response.status_code == 200:
        user_id = _extract_user_id_from_location(str(response.url))
        if user_id:
            return (user_id, username)
        return None

    if response.status_code == 404:
        return None

    response.raise_for_status()
    return None


async def _lookup_user(
    client: httpx.AsyncClient,
    username: str,
) -> tuple[str, str] | None:
    if _has_osu_oauth_config():
        try:
            api_result = await _lookup_user_by_api(client, username)
        except Exception:
            api_result = None
        if api_result is not None:
            return api_result

    return await _lookup_user_by_redirect(client, username)


async def _fetch_duel_rating(client: httpx.AsyncClient, user_id: str) -> dict:
    response = await client.get(
        _DUEL_RATING_URL_TEMPLATE.format(user_id=user_id),
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected ETX response payload")
    return payload


@matcher.handle()
async def handle_etx_query(event: MessageEvent) -> None:
    username = _extract_username(event.get_plaintext())
    if not username:
        return

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), trust_env=True) as client:
            user_info = await _lookup_user(client, username)
            if not user_info:
                return await matcher.finish(f"User not found: {username}")

            user_id, actual_username = user_info
            payload = await _fetch_duel_rating(client, user_id)
    except httpx.HTTPError as exc:
        return await matcher.finish(f"ETX query failed: {type(exc).__name__}: {exc}")
    except Exception as exc:
        return await matcher.finish(f"ETX query failed: {type(exc).__name__}: {exc}")

    return await matcher.finish(_format_duel_rating_message(actual_username, payload))
