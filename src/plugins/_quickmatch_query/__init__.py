from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

import httpx
from nonebot import get_plugin_config, on_message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="quickmatch-query",
    description="Query osu! quickmatch stats by osu! username.",
    usage="qm <osu username>",
    config=Config,
)

plugin_config = get_plugin_config(Config)
_LOOKUP_URL_TEMPLATE = "https://osu.ppy.sh/users/{username}"
_API_LOOKUP_URL_TEMPLATE = "https://osu.ppy.sh/api/v2/users/{username}/osu"
_USERNAME_PATTERN = re.compile(r"^(?:qm|quickmatch)\s+(.+?)\s*$", re.IGNORECASE)
_VALID_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9 _\-\[\]]+$")
_USER_ID_PATTERN = re.compile(r"/users/(?P<user_id>\d+)(?:[/?#]|$)")
_VARIANT_ID_TO_NAME = {
    0: "",
    4: "4k",
    7: "7k",
}

matcher = on_message(priority=20, block=True)
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


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.0f}"
    try:
        return f"{int(str(value)):,}"
    except (TypeError, ValueError):
        return "-"


def _format_rank(rank: Any) -> str:
    try:
        numeric_rank = int(rank)
    except (TypeError, ValueError):
        return "-"
    return f"#{numeric_rank:,}"


def _format_pool_display_name(pool: dict[str, Any] | None, pool_id: Any) -> str:
    if not isinstance(pool, dict):
        return f"Pool {pool_id}"

    name = str(pool.get("name", "")).strip() or f"Pool {pool_id}"
    try:
        variant_id = int(pool.get("variant_id", 0))
    except (TypeError, ValueError):
        variant_id = 0

    variant_name = _VARIANT_ID_TO_NAME.get(variant_id, "")
    if not variant_name:
        return name
    return f"[{variant_name}] {name}"


def _highest_active_rank(stats_list: list[dict[str, Any]]) -> str:
    highest_rank: int | None = None
    for stats in stats_list:
        pool = stats.get("pool")
        rank = stats.get("rank")
        if not isinstance(pool, dict) or not pool.get("active"):
            continue
        try:
            numeric_rank = int(rank)
        except (TypeError, ValueError):
            continue
        if highest_rank is None or numeric_rank < highest_rank:
            highest_rank = numeric_rank

    return _format_rank(highest_rank)


def _format_matchmaking_row(stats: dict[str, Any]) -> str:
    rating = _format_number(stats.get("rating"))
    if bool(stats.get("is_rating_provisional")) and rating != "-":
        rating = f"{rating}*"

    return " | ".join(
        [
            _format_pool_display_name(stats.get("pool"), stats.get("pool_id", "?")),
            f"Rank {_format_rank(stats.get('rank'))}",
            f"Wins {_format_number(stats.get('first_placements'))}",
            f"Plays {_format_number(stats.get('plays'))}",
            f"Points {_format_number(stats.get('total_points'))}",
            f"Rating {rating}",
        ]
    )


def _format_quickmatch_message(username: str, user_id: str, stats_list: list[dict[str, Any]]) -> str:
    sorted_stats = sorted(
        stats_list,
        key=lambda stats: int(stats.get("pool_id", 0) or 0),
        reverse=True,
    )

    lines = [
        f"{username} / {user_id}",
        f"Quickmatch: {_highest_active_rank(sorted_stats)}",
    ]
    lines.extend(_format_matchmaking_row(stats) for stats in sorted_stats)
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


async def _fetch_quickmatch_stats(client: httpx.AsyncClient, user_id: str) -> tuple[str, list[dict[str, Any]]]:
    access_token = await _get_osu_access_token(client)
    if not access_token:
        raise ValueError("osu OAuth is not configured")

    response = await client.get(
        _API_LOOKUP_URL_TEMPLATE.format(username=quote(user_id, safe="")),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected osu user response payload")

    actual_username = str(payload.get("username", "")).strip() or user_id
    stats_list = payload.get("matchmaking_stats")
    if not isinstance(stats_list, list):
        raise ValueError("osu user response missing matchmaking_stats")

    normalized_stats: list[dict[str, Any]] = [
        stats for stats in stats_list if isinstance(stats, dict)
    ]
    return actual_username, normalized_stats


@matcher.handle()
async def handle_quickmatch_query(event: MessageEvent) -> None:
    username = _extract_username(event.get_plaintext())
    if not username:
        return

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), trust_env=True) as client:
            user_info = await _lookup_user(client, username)
            if not user_info:
                result_message = f"User not found: {username}"
            else:
                user_id, fallback_username = user_info
                actual_username, stats_list = await _fetch_quickmatch_stats(client, user_id)
                display_username = actual_username or fallback_username

                if not stats_list:
                    result_message = f"No quickmatch stats: {display_username}"
                else:
                    result_message = _format_quickmatch_message(display_username, user_id, stats_list)
    except httpx.HTTPError as exc:
        return await matcher.finish(f"Quickmatch query failed: {type(exc).__name__}: {exc}")
    except Exception as exc:
        return await matcher.finish(f"Quickmatch query failed: {type(exc).__name__}: {exc}")

    return await matcher.finish(result_message)
