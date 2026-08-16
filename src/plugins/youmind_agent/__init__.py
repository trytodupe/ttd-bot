from __future__ import annotations

from typing import Any

from nonebot import (
    get_driver,
    get_plugin_config,
    logger,
    on_command,
    on_message,
    require,
)
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, to_me

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as localstore

from .config import Config
from .service import YouMindService, forward_id_from_message, send_message_id, utc_now
from .storage import StateStore

__plugin_meta__ = PluginMetadata(
    name="YouMind 项目代理",
    description="把 QQ 合并转发素材导入 YouMind Project，并转发 Agent 的多轮交互和生成结果。",
    usage="回复一条合并转发消息并 @bot，描述要让 YouMind 完成的任务。",
    type="application",
    supported_adapters={"~onebot.v11"},
    config=Config,
)

config = get_plugin_config(Config)
store = StateStore(
    localstore.get_data_file(plugin_name="youmind_agent", filename="state.json")
)
service = YouMindService(
    config,
    store,
    localstore.get_cache_dir(plugin_name="youmind_agent"),
)
driver = get_driver()


def _reply_message_id(event: MessageEvent) -> int | None:
    reply = getattr(event, "reply", None)
    value = getattr(reply, "message_id", None) if reply is not None else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _explicitly_at_bot(bot: Bot, event: MessageEvent) -> bool:
    return any(
        segment.type == "at" and str(segment.data.get("qq")) == str(bot.self_id)
        for segment in event.original_message
    )


def _reply_forward_id(event: MessageEvent) -> str:
    reply = getattr(event, "reply", None)
    return (
        forward_id_from_message(getattr(reply, "message", None))
        if reply is not None
        else ""
    )


async def _resolve_reply_forward_id(bot: Bot, event: MessageEvent) -> str:
    if direct := _reply_forward_id(event):
        return direct
    reply_message_id = _reply_message_id(event)
    if reply_message_id is None:
        return ""
    try:
        result = await bot.call_api("get_msg", message_id=reply_message_id)
    except Exception:  # noqa: BLE001 - unavailable quote lookup means this rule does not match
        return ""
    message = result.get("message") if isinstance(result, dict) else None
    if isinstance(message, list):
        message = Message(message)
    return forward_id_from_message(message)


async def _start_rule(bot: Bot, event: MessageEvent) -> bool:
    return (
        config.youmind_enabled
        and isinstance(event, GroupMessageEvent)
        and event.group_id in config.youmind_allowed_group_ids
        and _explicitly_at_bot(bot, event)
        and bool(await _resolve_reply_forward_id(bot, event))
    )


async def _continuation_rule(event: MessageEvent) -> bool:
    if not config.youmind_enabled or not isinstance(event, GroupMessageEvent):
        return False
    if event.group_id not in config.youmind_allowed_group_ids:
        return False
    reply_message_id = _reply_message_id(event)
    if reply_message_id is None:
        return False
    return await store.route(reply_message_id) is not None


continuation_matcher = on_message(rule=Rule(_continuation_rule), priority=2, block=True)
start_matcher = on_message(rule=Rule(_start_rule), priority=3, block=True)


@start_matcher.handle()
async def handle_start(bot: Bot, event: GroupMessageEvent) -> None:
    instruction = event.get_message().extract_plain_text().strip()
    if not instruction:
        await start_matcher.finish("请在回复合并转发时写明要让 YouMind 完成的任务。")
        return
    forward_message_id = _reply_message_id(event)
    forward_id = await _resolve_reply_forward_id(bot, event)
    if forward_message_id is None or not forward_id:
        await start_matcher.finish("无法读取这条合并转发消息。")
        return
    chat = await service.create_pending_chat(
        group_id=event.group_id,
        user_id=event.user_id,
        request_message_id=event.message_id,
        forward_message_id=forward_message_id,
        instruction=instruction,
    )
    acknowledgement = MessageSegment.reply(event.message_id) + MessageSegment.text(
        "已接收，正在创建 YouMind Project 并导入这批素材。"
    )
    result = await bot.call_api(
        "send_group_msg", group_id=event.group_id, message=acknowledgement
    )
    if (message_id := send_message_id(result)) is not None:
        await store.bind_route(message_id, chat["local_id"])
    service.launch(chat["local_id"], service.run_start(bot, chat, forward_id))


@continuation_matcher.handle()
async def handle_continuation(bot: Bot, event: GroupMessageEvent) -> None:
    reply_message_id = _reply_message_id(event)
    if reply_message_id is None:
        return
    chat = await store.route(reply_message_id)
    if chat is None:
        return
    if event.group_id != int(chat["group_id"]) or event.user_id != int(chat["user_id"]):
        await continuation_matcher.finish(
            "只有这个 YouMind 请求的发起人可以继续该会话。"
        )
        return
    answer = event.get_message().extract_plain_text().strip()
    if not answer:
        await continuation_matcher.finish("请在回复中写明你的回答或下一步要求。")
        return
    service.launch(
        f"{chat['local_id']}:{event.message_id}",
        service.continue_chat(bot, chat, event.message_id, answer),
    )


status_matcher = on_command("youmind", rule=to_me(), priority=5, block=True)
command_arg = CommandArg()


@status_matcher.handle()
async def handle_status(event: MessageEvent, args: Message = command_arg) -> None:
    if (
        not config.youmind_enabled
        or not isinstance(event, GroupMessageEvent)
        or event.group_id not in config.youmind_allowed_group_ids
    ):
        await status_matcher.finish("当前会话不能使用 YouMind。")
        return
    if args.extract_plain_text().strip().lower() != "status":
        await status_matcher.finish("用法：ttd youmind status")
        return
    chats = await store.chats_for(event.group_id, event.user_id)
    if not chats:
        await status_matcher.finish("你在本群还没有 YouMind 请求。")
        return
    lines = ["最近的 YouMind 请求："]
    for chat in chats[:5]:
        preview = " ".join(str(chat.get("instruction") or "").split())[:40]
        lines.append(
            f"{str(chat.get('local_id'))[:8]} · {chat.get('status')} · {preview}"
        )
    await status_matcher.finish("\n".join(lines))


@driver.on_bot_connect
async def resume_tasks(bot: Bot) -> None:
    for chat in await store.unfinished_chats():
        if int(chat.get("group_id", 0)) not in config.youmind_allowed_group_ids:
            continue
        # Project import source URLs may have expired. Running chats can be safely polled again.
        if chat.get("chat_id"):

            async def resume(current: dict[str, Any] = chat) -> None:
                try:
                    async with service.client() as client:
                        turn = await service._wait_for_turn(
                            client,
                            str(current["chat_id"]),
                            str(current.get("last_assistant_id") or ""),
                        )
                        await service._deliver_turn(bot, current, turn)
                except Exception as exc:  # noqa: BLE001 - resume boundary persists every failure
                    await service._fail(bot, current, exc)

            service.launch(chat["local_id"], resume())
        else:
            chat["status"] = "failed"
            chat["error"] = "bot restarted before the YouMind Chat was created"
            chat["updated_at"] = utc_now()
            await store.put_chat(chat)
            try:
                message = MessageSegment.reply(
                    int(chat["request_message_id"])
                ) + MessageSegment.text(
                    "Bot 在素材导入期间重启了，请重新回复原合并转发消息发起任务。"
                )
                await bot.call_api(
                    "send_group_msg", group_id=int(chat["group_id"]), message=message
                )
            except Exception as exc:  # noqa: BLE001 - reconnect reporting is best effort
                logger.warning("Failed to report interrupted YouMind import: %r", exc)
