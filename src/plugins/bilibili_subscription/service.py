from __future__ import annotations

import asyncio
import json
from typing import Any

from bilibili_api.user import User
from httpx import AsyncClient, QueryParams
from nonebot import logger
from nonebot.exception import ActionFailed, NetworkError
from nonebot_plugin_alconna.uniseg import SupportAdapter, Target
from nonebot_plugin_parser.constants import COMMON_HEADER, COMMON_TIMEOUT
from nonebot_plugin_parser.matchers import get_parser_by_type
from nonebot_plugin_parser.parsers import BilibiliParser
from nonebot_plugin_parser.renders import render_messages

from .storage import SubscriptionManager

LIVE_STATUS_URL = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"


def extract_live_play_info(item: dict[str, Any]) -> dict[str, Any] | None:
    module_dynamic = item.get("modules", {}).get("module_dynamic", {})
    if not isinstance(module_dynamic, dict):
        return None
    major = module_dynamic.get("major")
    additional = module_dynamic.get("additional")
    live_rcmd = major.get("live_rcmd") if isinstance(major, dict) else None
    if not isinstance(live_rcmd, dict) and isinstance(additional, dict):
        live_rcmd = additional.get("live_rcmd")
    if not isinstance(live_rcmd, dict):
        return None

    content = live_rcmd.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return None
    if not isinstance(content, dict):
        return None
    live_play_info = content.get("live_play_info")
    return live_play_info if isinstance(live_play_info, dict) else None


def extract_live_start_time(item: dict[str, Any]) -> int | None:
    info = extract_live_play_info(item)
    if info is None:
        return None
    try:
        live_start = int(info.get("live_start_time", 0))
    except (TypeError, ValueError):
        return None
    return live_start or None


def extract_url_from_item(item: dict[str, Any]) -> str | None:
    dynamic_id = item.get("id_str")
    dynamic_url = f"https://t.bilibili.com/{dynamic_id}" if dynamic_id else None
    live_info = extract_live_play_info(item)
    if live_info and live_info.get("room_id"):
        return f"https://live.bilibili.com/{live_info['room_id']}"

    module_dynamic = item.get("modules", {}).get("module_dynamic", {})
    major = module_dynamic.get("major") if isinstance(module_dynamic, dict) else None
    if not isinstance(major, dict):
        return dynamic_url

    major_type = major.get("type")
    if major_type == "MAJOR_TYPE_ARCHIVE":
        bvid = (major.get("archive") or {}).get("bvid")
        return f"https://www.bilibili.com/video/{bvid}" if bvid else dynamic_url
    if major_type == "MAJOR_TYPE_OPUS":
        return (major.get("opus") or {}).get("jump_url") or dynamic_url
    if major_type == "MAJOR_TYPE_ARTICLE":
        article_id = (major.get("article") or {}).get("id")
        return (
            f"https://www.bilibili.com/read/cv{article_id}"
            if article_id
            else dynamic_url
        )
    if major_type == "MAJOR_TYPE_LIVE":
        room_id = (major.get("live") or {}).get("roomid")
        return f"https://live.bilibili.com/{room_id}" if room_id else dynamic_url
    if major_type == "MAJOR_TYPE_UGC_SEASON":
        bvid = (major.get("ugc_season") or {}).get("bvid")
        return f"https://www.bilibili.com/video/{bvid}" if bvid else dynamic_url
    if major_type == "MAJOR_TYPE_COMMON":
        return (major.get("common") or {}).get("jump_url") or dynamic_url
    if major_type == "MAJOR_TYPE_PGC":
        epid = (major.get("pgc") or {}).get("epid")
        return (
            f"https://www.bilibili.com/bangumi/play/ep{epid}" if epid else dynamic_url
        )
    if major_type == "MAJOR_TYPE_MUSIC":
        music_id = (major.get("music") or {}).get("id")
        return (
            f"https://www.bilibili.com/audio/au{music_id}" if music_id else dynamic_url
        )
    return dynamic_url


def active_live_start(status: dict[str, Any]) -> int | None:
    if status.get("live_status") != 1:
        return None
    try:
        live_start = int(status.get("live_time", 0))
    except (TypeError, ValueError):
        return None
    return live_start or None


async def fetch_live_statuses(uids: list[str]) -> dict[str, dict[str, Any]]:
    params = QueryParams([("uids[]", uid) for uid in uids])
    async with AsyncClient(headers=COMMON_HEADER, timeout=COMMON_TIMEOUT) as client:
        response = await client.get(LIVE_STATUS_URL, params=params)
        response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if payload.get("code") != 0 or not isinstance(data, dict):
        raise RuntimeError(
            f"B 站直播状态接口返回异常: {payload.get('code')} {payload.get('message', '')}"
        )
    return {
        str(uid): status for uid, status in data.items() if isinstance(status, dict)
    }


def collect_new_live_sessions(
    statuses: dict[str, dict[str, Any]],
    manager: SubscriptionManager,
) -> list[tuple[str, str]]:
    sessions: list[tuple[str, str]] = []
    for uid, status in statuses.items():
        live_start = active_live_start(status)
        previous = manager.get_last_live_start(uid)
        if previous is None:
            manager.set_last_live_start(uid, live_start or 0)
            continue
        if live_start is None or live_start == previous:
            continue
        room_id = status.get("room_id")
        if room_id:
            manager.set_last_live_start(uid, live_start)
            sessions.append((uid, str(room_id)))
    return sessions


def is_announced_live_item(
    item: dict[str, Any], manager: SubscriptionManager, uid: str
) -> bool:
    live_start = extract_live_start_time(item)
    return live_start is not None and live_start == manager.get_last_live_start(uid)


async def initialize_uid(manager: SubscriptionManager, uid: str) -> None:
    if manager.get_last_seen(uid) == "0":
        try:
            data = await User(int(uid)).get_dynamics_new(offset="")
            items = data.get("items", [])
            if items and items[0].get("id_str"):
                manager.set_last_seen(uid, str(items[0]["id_str"]))
        except Exception:  # noqa: BLE001
            logger.exception(f"初始化 UID {uid} 动态书签失败，将在首次轮询时重试")

    if manager.get_last_live_start(uid) is None:
        try:
            status = (await fetch_live_statuses([uid])).get(uid, {})
            manager.set_last_live_start(uid, active_live_start(status) or 0)
        except Exception:  # noqa: BLE001
            logger.exception(f"初始化 UID {uid} 直播状态失败，将在首次轮询时重试")


async def send_subscription_url(
    uid: str,
    url: str,
    groups: list[tuple[str, str]],
    *,
    context: str,
) -> None:
    try:
        parser = get_parser_by_type(BilibiliParser)
        keyword, searched = parser.search_url(url)
        result = await parser.parse(keyword, searched)
    except Exception:  # noqa: BLE001
        logger.exception(f"解析订阅内容失败 UID={uid} {context} url={url}")
        return

    for scope, group_id in groups:
        try:
            target = Target(group_id, scope=scope, adapter=SupportAdapter.onebot11)
            async for message in render_messages(result):
                await message.send(target=target)
            await asyncio.sleep(0.5)
        except ActionFailed as error:
            logger.warning(f"发送失败 {scope}_{group_id}: {error}")
        except NetworkError as error:
            logger.warning(f"网络错误 {scope}_{group_id}: {error}")


async def check_live_updates(manager: SubscriptionManager) -> None:
    uids = manager.get_all_uids()
    if not uids:
        return
    try:
        sessions = collect_new_live_sessions(await fetch_live_statuses(uids), manager)
    except Exception:  # noqa: BLE001
        logger.exception("检查 B 站直播状态时出错")
        return
    for uid, room_id in sessions:
        await send_subscription_url(
            uid,
            f"https://live.bilibili.com/{room_id}",
            manager.get_groups_for_uid(uid),
            context="live",
        )


async def check_dynamic_updates(manager: SubscriptionManager) -> None:
    for uid in manager.get_all_uids():
        try:
            await check_single_uid(uid, manager)
        except Exception:  # noqa: BLE001
            logger.exception(f"检查 UID {uid} 动态更新时出错")
        await asyncio.sleep(2)


async def check_single_uid(uid: str, manager: SubscriptionManager) -> None:
    data = await User(int(uid)).get_dynamics_new(offset="")
    items = data.get("items")
    if not items:
        return
    last_seen = manager.get_last_seen(uid)
    if last_seen == "0":
        manager.set_last_seen(uid, str(items[0].get("id_str", "0")))
        return

    new_items: list[dict[str, Any]] = []
    for item in items:
        dynamic_id = str(item.get("id_str", "0"))
        if int(dynamic_id) <= int(last_seen):
            break
        new_items.append(item)
    if not new_items:
        return

    manager.set_last_seen(uid, str(items[0].get("id_str", last_seen)))
    groups = manager.get_groups_for_uid(uid)
    for item in reversed(new_items):
        if is_announced_live_item(item, manager, uid):
            continue
        url = extract_url_from_item(item)
        if url:
            await send_subscription_url(
                uid,
                url,
                groups,
                context=f"id={item.get('id_str', '?')} type={item.get('type', '?')}",
            )
