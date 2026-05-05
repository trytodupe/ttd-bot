from __future__ import annotations

import time
from dataclasses import replace
from typing import Iterable

from nonebot import get_driver, get_bots, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, PrivateMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.params import CommandArg
from nonebot.rule import is_type

from .config import AccessRequestConfig
from .storage import AccessRequestRecord, create_request, load_requests, save_requests


__plugin_meta__ = PluginMetadata(
    name="access-request",
    description="Generic capability request and approval flow for plugins.",
    usage='Use "申请" in private chat to request access, and use superuser commands to approve or reject it.',
)


class AccessRequestService:
    def __init__(self, config: AccessRequestConfig | None = None):
        self.config = config or AccessRequestConfig()

    def _records(self) -> list[AccessRequestRecord]:
        return load_requests()

    def _persist(self, records: Iterable[AccessRequestRecord]) -> None:
        save_requests(list(records))

    def is_allowed(self, user_id: int, capability: str | None = None) -> bool:
        target_capability = capability or self.config.capability_name
        for record in self._records():
            if record.user_id == int(user_id) and record.capability == target_capability and record.status == "approved":
                return True
        return False

    def find_pending(self, user_id: int, capability: str | None = None) -> AccessRequestRecord | None:
        target_capability = capability or self.config.capability_name
        for record in self._records():
            if record.user_id == int(user_id) and record.capability == target_capability and record.status == "pending":
                return record
        return None

    def request_access(self, user_id: int, request_text: str, capability: str | None = None) -> AccessRequestRecord:
        target_capability = capability or self.config.capability_name
        records = self._records()
        pending = self.find_pending(user_id, target_capability)
        if pending is not None:
            return pending

        approved = self.is_allowed(user_id, target_capability)
        if approved:
            return AccessRequestRecord(
                request_id="",
                capability=target_capability,
                user_id=int(user_id),
                request_text=request_text,
                status="approved",
                created_at=int(time.time()),
            )

        request = create_request(target_capability, user_id, request_text)
        records.append(request)
        self._persist(records)
        return request

    def build_notification_message(self, record: AccessRequestRecord, requester_name: str) -> str:
        return (
            "收到新的权限申请：\n"
            f"能力：{record.capability}\n"
            f"申请人：{requester_name}\n"
            f"QQ号：{record.user_id}\n"
            f"申请内容：{record.request_text}\n"
            f"使用命令“同意权限申请 {record.request_id}”同意。\n"
            f"使用命令“拒绝权限申请 {record.request_id}”拒绝。"
        )

    async def notify_primary_superuser(self, bot: Bot, record: AccessRequestRecord, requester_name: str) -> bool:
        driver = get_driver()
        superusers = list(getattr(driver.config, "superusers", set()) or [])
        if not superusers:
            return False

        target_user = sorted(
            (str(item).strip() for item in superusers if str(item).strip()),
            key=lambda item: (not item.isdigit(), int(item) if item.isdigit() else item),
        )[0]
        if not target_user.isdigit():
            return False

        await bot.call_api(
            "send_private_msg",
            user_id=int(target_user),
            message=self.build_notification_message(record, requester_name),
        )
        return True

    def list_pending(self, capability: str | None = None) -> list[AccessRequestRecord]:
        target_capability = capability or self.config.capability_name
        return [
            record
            for record in self._records()
            if record.capability == target_capability and record.status == "pending"
        ]

    def approve(self, request_id: str, reviewer_id: int) -> AccessRequestRecord | None:
        records = self._records()
        updated: list[AccessRequestRecord] = []
        result: AccessRequestRecord | None = None
        for record in records:
            if record.request_id == request_id:
                result = replace(
                    record,
                    status="approved",
                    reviewed_by=int(reviewer_id),
                    reviewed_at=int(time.time()),
                )
                updated.append(result)
            else:
                updated.append(record)
        if result is None:
            return None
        self._persist(updated)
        return result

    def reject(self, request_id: str, reviewer_id: int) -> AccessRequestRecord | None:
        records = self._records()
        updated: list[AccessRequestRecord] = []
        result: AccessRequestRecord | None = None
        for record in records:
            if record.request_id == request_id:
                result = replace(
                    record,
                    status="rejected",
                    reviewed_by=int(reviewer_id),
                    reviewed_at=int(time.time()),
                )
                updated.append(result)
            else:
                updated.append(record)
        if result is None:
            return None
        self._persist(updated)
        return result


service = AccessRequestService()


view_pending = on_command("查看权限申请", rule=is_type(PrivateMessageEvent), permission=SUPERUSER, priority=1, block=True)
approve_pending = on_command("同意权限申请", rule=is_type(PrivateMessageEvent), permission=SUPERUSER, priority=1, block=True)
reject_pending = on_command("拒绝权限申请", rule=is_type(PrivateMessageEvent), permission=SUPERUSER, priority=1, block=True)


@view_pending.handle()
async def handle_view_pending() -> None:
    pending = service.list_pending()
    if not pending:
        await view_pending.finish("当前没有待处理申请。")
    lines = [
        f"{item.request_id} | {item.capability} | {item.user_id} | {item.request_text}"
        for item in pending
    ]
    await view_pending.finish("待处理申请：\n" + "\n".join(lines))


@approve_pending.handle()
async def handle_approve_pending(event: MessageEvent, arg: Message = CommandArg()) -> None:
    request_id = arg.extract_plain_text().strip()
    if not request_id:
        await approve_pending.finish("请输入 request_id。")
    result = service.approve(request_id, int(event.user_id))
    if result is None:
        await approve_pending.finish("未找到对应申请。")
    await approve_pending.finish(f"已同意申请：{request_id}")


@reject_pending.handle()
async def handle_reject_pending(event: MessageEvent, arg: Message = CommandArg()) -> None:
    request_id = arg.extract_plain_text().strip()
    if not request_id:
        await reject_pending.finish("请输入 request_id。")
    result = service.reject(request_id, int(event.user_id))
    if result is None:
        await reject_pending.finish("未找到对应申请。")
    await reject_pending.finish(f"已拒绝申请：{request_id}")
