from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any, Optional

import httpx
import nonebot_plugin_localstore as store
from nonebot import CommandGroup, get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

from .config import Config
from .user_storage import UserStorage

__plugin_meta__ = PluginMetadata(
    name="tetr_chercher",
    description="Query TETR.IO player stats.",
    usage="ttd tetr bind <username> / tetr / ttd tetr",
    config=Config,
)

config = get_plugin_config(Config)

history_data: dict[str, dict[str, Any]] = {}
PROXY = None

DATA_FILE: Path = store.get_data_file(
    plugin_name="nonebot_plugin_tetr_chercher",
    filename="user_bindings.json",
)
user_storage = UserStorage(DATA_FILE)

tetr_group = CommandGroup("tetr", rule=to_me(), priority=2, block=True)
bind_cmd = tetr_group.command("bind ")


def _case_variants(text: str) -> set[str]:
    variants: list[tuple[str, ...]] = []
    for char in text:
        lowered = char.lower()
        uppered = char.upper()
        if lowered == uppered:
            variants.append((char,))
        else:
            variants.append((lowered, uppered))
    return {"".join(chars) for chars in product(*variants)}


def _command_case_aliases(*commands: tuple[str, ...]) -> set[tuple[str, ...]]:
    aliases: set[tuple[str, ...]] = set()
    for command in commands:
        variants: list[set[str]] = []
        for part in command:
            variants.append(_case_variants(part))
        aliases.update(product(*variants))
    return aliases


query_matcher = on_command(
    "tetr",
    aliases=_command_case_aliases(("tetr",), ("ttd", "tetr")),
    priority=1,
    block=True,
)


def format_playtime(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours}小时 {minutes}分钟 {secs}秒"


def get_diff(curr: float, prev: float | None, is_rank: bool = False, is_time: bool = False) -> str:
    if prev is None:
        return ""
    diff = curr - prev
    if abs(diff) < 0.0001:
        return ""
    if is_rank or is_time:
        return f" (↑{abs(diff)})" if diff < 0 else f" (↓{abs(diff)})"
    return f" (↑{diff:,.2f})" if diff > 0 else f" (↓{abs(diff):,.2f})"


def _safe_stats(raw: dict[str, Any], mode_key: str, field: str) -> float:
    mode = raw.get(mode_key)
    if not isinstance(mode, dict):
        return 0
    record = mode.get("record")
    if not isinstance(record, dict):
        return 0
    results = record.get("results")
    if not isinstance(results, dict):
        return 0
    stats = results.get("stats")
    if not isinstance(stats, dict):
        return 0
    value = stats.get(field, 0)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def fetch_user_data(username: str) -> Optional[dict[str, Any]]:
    clean_name = username.strip().lower()
    if not clean_name:
        return None

    url = f"https://ch.tetr.io/api/users/{clean_name}/summaries"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": "https://tetr.io/",
    }

    try:
        async with httpx.AsyncClient(proxy=PROXY, headers=headers, timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            json_data = resp.json()

        if not isinstance(json_data, dict) or not json_data.get("success"):
            return None

        raw = json_data.get("data")
        if not isinstance(raw, dict):
            return None

        mode_40l = raw.get("40l")
        record = mode_40l.get("record") if isinstance(mode_40l, dict) else None
        user_obj = record.get("user") if isinstance(record, dict) else None
        if not isinstance(user_obj, dict):
            return None

        country_code = str(user_obj.get("country") or "??").upper()
        league_raw = raw.get("league")
        league = league_raw if isinstance(league_raw, dict) else {}
        tr_val = _coerce_float(league.get("tr", 0))

        v_val = league.get("v")
        if v_val is None:
            v_val = league.get("volatility", 0)
        v_val = _coerce_float(v_val)

        rank_val = str(league.get("rank") or "z").upper()
        gl_stand = _coerce_int(league.get("standing", -1), -1)
        lc_stand = _coerce_int(league.get("standing_local", -1), -1)

        achievements = raw.get("achievements")
        xp_val = 0
        if isinstance(achievements, list):
            for achievement in achievements:
                if isinstance(achievement, dict) and achievement.get("n") == "xp":
                    xp_val = _coerce_int(achievement.get("v", 0))
                    break

        zen_raw = raw.get("zen")
        zen = zen_raw if isinstance(zen_raw, dict) else {}

        return {
            "username": str(user_obj.get("username") or raw.get("username") or clean_name),
            "tr": tr_val,
            "v": v_val,
            "rank": rank_val,
            "gl_standing": gl_stand,
            "country": country_code,
            "country_rank": lc_stand,
            "sprint": (_safe_stats(raw, "40l", "finaltime") / 1000) or None,
            "blitz": _safe_stats(raw, "blitz", "score") or None,
            "zen_score": _coerce_int(zen.get("score", 0)),
            "zen_level": _coerce_int(zen.get("level", 0)),
            "xp": xp_val,
            "playtime": _coerce_int(raw.get("gametime", 0)),
        }
    except Exception as exc:
        logger.error(f"[Tetrio] data fetch failed: {exc}")
        return None


async def handle_query(event: MessageEvent, matcher: Any) -> None:
    uid = str(event.get_user_id())

    if not user_storage.has_user(uid):
        await matcher.finish("❌ 请先绑定账号：ttd tetr bind <id>.")
        return

    username = user_storage.get_single_user(uid)
    data = await fetch_user_data(username)
    if not data:
        await matcher.finish("❌ 获取数据失败。")
        return

    hist_key = f"{uid}_{username}"
    prev = history_data.get(hist_key)
    history_data[hist_key] = data.copy()

    title = data["username"]

    res = [f"{title}的个人信息—TETR.IO {data['country']}", ""]
    tr_diff = get_diff(data["tr"], prev["tr"] if prev else None)
    res.append(f"{data['tr']:,.2f} TR±{data['v']:.2f}, {data['rank']}段{tr_diff}")

    if data["gl_standing"] != -1:
        gl_diff = get_diff(data["gl_standing"], prev["gl_standing"] if prev else None, is_rank=True)
        res.append(f"#{data['gl_standing']:,} 全球排名{gl_diff}")

    if data["country_rank"] != -1:
        lc_diff = get_diff(data["country_rank"], prev["country_rank"] if prev else None, is_rank=True)
        res.append(f"{data['country']} #{data['country_rank']:,}{lc_diff}")

    res.append(f"{int(data['xp']):,} Exp 玩家总经验{get_diff(data['xp'], prev['xp'] if prev else None)}")

    if data["sprint"]:
        res.append(f"{data['sprint']:.3f}s 40L成绩")
    if data["blitz"]:
        res.append(f"{data['blitz']:,} Blitz成绩")
    if data["zen_score"]:
        res.append(f"{int(data['zen_score']):,} Zen分数 (Lv.{data['zen_level']})")
    if data["playtime"] > 0:
        t_diff = get_diff(data["playtime"], prev["playtime"] if prev else None)
        res.append(f"{format_playtime(data['playtime'])}{t_diff}")

    await matcher.finish("\n".join(res))
    return


@bind_cmd.handle()
async def _handle_bind(event: MessageEvent, args: Message = CommandArg()) -> None:
    user_id = str(event.get_user_id())
    username = args.extract_plain_text().strip()
    if not username:
        await bind_cmd.finish("❌ 格式：ttd tetr bind <username>")
        return

    user_storage.add_user(user_id=user_id, username=username)
    await bind_cmd.finish("✅ 绑定成功！")
    return


@query_matcher.handle()
async def _handle_query(event: MessageEvent) -> None:
    await handle_query(event, query_matcher)
