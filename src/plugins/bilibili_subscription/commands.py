from __future__ import annotations

from bilibili_api.user import User
from nonebot import logger, on_command
from nonebot.adapters import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot_plugin_uninfo import ADMIN, Session, UniSession

from .service import initialize_uid
from .storage import SubscriptionManager


def register_commands(manager: SubscriptionManager) -> None:
    command = on_command(
        "sub",
        rule=to_me(),
        permission=SUPERUSER | ADMIN(),
        priority=10,
        block=True,
    )

    @command.handle()
    async def handle(
        matcher: Matcher,
        args: Message = CommandArg(),  # noqa: B008
        session: Session = UniSession(),  # noqa: B008
    ) -> None:
        if session.scene.is_private:
            await matcher.finish("订阅功能仅在群聊中可用")

        parts = args.extract_plain_text().strip().split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        uid = parts[1].strip() if len(parts) > 1 else ""
        scope = session.scope
        group_id = session.scene_path

        if action == "add":
            if not uid.isdigit():
                await matcher.finish("请提供有效的 B 站用户 UID（纯数字）")
            if not manager.add_sub(scope, group_id, uid):
                await matcher.finish(f"UID {uid} 已在本群订阅列表中，无需重复添加")
            await initialize_uid(manager, uid)
            try:
                info = await User(int(uid)).get_user_info()
                name = str(info.get("name", ""))
            except Exception:  # noqa: BLE001
                logger.exception(f"获取 UID {uid} 用户信息失败")
                name = ""
            if name:
                await matcher.finish(f"已订阅 {name}（UID: {uid}）")
            await matcher.finish(f"已订阅 UID: {uid}（无法获取用户名，订阅已生效）")

        if action == "remove":
            if not uid.isdigit():
                await matcher.finish("请提供有效的 B 站用户 UID（纯数字）")
            if manager.remove_sub(scope, group_id, uid):
                await matcher.finish(f"已取消订阅 UID: {uid}")
            await matcher.finish(f"本群未订阅 UID: {uid}")

        if action == "list":
            uids = manager.get_subs_for_group(scope, group_id)
            if not uids:
                await matcher.finish("本群暂无 B 站订阅")
            lines = ["本群 B 站订阅列表:"]
            for subscribed_uid in uids:
                try:
                    info = await User(int(subscribed_uid)).get_user_info()
                    lines.append(f"  {info.get('name', '?')}（UID: {subscribed_uid}）")
                except Exception:  # noqa: BLE001
                    lines.append(f"  UID: {subscribed_uid}")
            await matcher.finish("\n".join(lines))

        await matcher.finish("用法: ttd sub add/remove <uid> 或 ttd sub list")
