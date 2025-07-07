import datetime
import logging

from nonebot import on_command, on_message, permission
from nonebot.rule import is_type, to_me
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent

from nonebot import CommandGroup
from sqlalchemy import select


from .citation_counter_db import *
from .__init__ import config


citation_db = config.citation_counter_db_path

command_rule = is_type(GroupMessageEvent) & to_me()
cite_cmd_group = CommandGroup("cite", rule=command_rule, priority=10, block=True)

cmd = cite_cmd_group.command(tuple())
today_cmd = cite_cmd_group.command("today")
yesterday_cmd = cite_cmd_group.command("yesterday")
total_cmd = cite_cmd_group.command("total")

async def handle_command(event: GroupMessageEvent, date: datetime.date, prompt: str, finish_cmd):
    group_id = event.group_id
    try:
        citation_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(citation_db.resolve()) as conn:
            await init_db(conn)
            if date is None:
                d = await get_all_data(conn, group_id)
            else:
                d = await get_data_by_date(conn, group_id, date)
        if not d:
            await finish_cmd.finish(f"{prompt}无数据")
        else:
            user_names = {}
            async with get_session() as db_session:
                for user_id in d.keys():
                    statement = select(UserModel.user_data).where(UserModel.user_id == user_id)
                    result = await db_session.execute(statement)
                    user_data = result.scalar_one_or_none()
                    if user_data:
                        user_names[user_id] = user_data.get("name", user_id)
                    else:
                        user_names[user_id] = user_id
            await finish_cmd.finish(f"{prompt}引用数：\n" + "\n".join([f"{user_names[k]}: {v}" for k, v in d.items()]))
    except sqlite3.Error as e:
        logging.error(f"An error occurred: {e}")
        await finish_cmd.finish("出错了..")

@today_cmd.handle()
async def _(event: GroupMessageEvent):
    await handle_command(event, datetime.date.today(), "今日", today_cmd)

@yesterday_cmd.handle()
async def _(event: GroupMessageEvent):
    await handle_command(event, datetime.date.today() - datetime.timedelta(days=1), "昨日", yesterday_cmd)

@total_cmd.handle()
async def _(event: GroupMessageEvent):
    await handle_command(event, None, "本群", total_cmd)


test = on_command("test", rule=to_me(), permission=permission.SUPERUSER)
@test.handle()
async def _(event: MessageEvent):
    await test.finish("t")

def is_reply(event: GroupMessageEvent) -> bool:
    # debug
    # print("===>")
    # print(event.message_id)
    # print(event.user_id)
    # print(event.group_id)
    # print(event.original_message)
    # print("<===")

    for seg in event.original_message:
        # print(seg.type, seg.data)
        if seg.type == 'reply':
            if int(seg.data['id']) != 0:
                return True
            else:
                return False
    return False


async def get_user_id_from_message_id(message_id: str) -> str:
    async with get_session() as db_session:
        statement = (
            select(UserModel.user_id)
            .where(MessageRecord.message_id == message_id)
            .join(SessionModel, UserModel.id == SessionModel.user_persist_id)
            .join(MessageRecord, SessionModel.id == MessageRecord.session_persist_id)
        )
        result = await db_session.execute(statement)
        user_ids = result.scalars().all()
        user_id = user_ids[-1] if user_ids else None
    return user_id

reply = on_message(rule=is_reply, block=False)

@reply.handle()
async def _(event: GroupMessageEvent):
    group_id = event.group_id

    for seg in event.original_message:
        # print(seg)
        if seg.type == 'reply':
            reply_message_id: str = seg.data['id']

    if reply_message_id is None:
        logging.warning(f"can't find reply message id in {event.original_message}")
        return

    replied_user_id = await get_user_id_from_message_id(reply_message_id)

    if replied_user_id is None:
        logging.warning(f"can't find replied user id in {reply_message_id}")
        return

    if str(event.user_id) in config.citation_counter_ignore_user_ids:
        return

    try:
        citation_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(citation_db.resolve()) as conn:
            await init_db(conn)
            await add_date(conn, group_id, datetime.date.today())
            await add_user(conn, group_id, replied_user_id)

            await iterate_number(conn, group_id, datetime.date.today(), replied_user_id)

    except sqlite3.Error as e:
        logging.error(f"An error occurred: {e}")

    # await reply.finish(f"replied to {reply_message_id}, sent by {replied_user_id}")


from nonebot_plugin_orm import get_session
from nonebot_plugin_uninfo.orm import UserModel, SessionModel
from nonebot_plugin_chatrecorder.model import MessageRecord
