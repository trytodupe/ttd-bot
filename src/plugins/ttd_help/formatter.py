from __future__ import annotations

from typing import Literal

from .registry import FeatureDoc, iter_visible_docs

HelpSection = Literal["public", "admin", "background"]


def format_help_index(section: HelpSection = "public") -> str:
    docs = iter_visible_docs(include_admin=True, include_background=True)
    section_docs = [doc for doc in docs if doc.visibility == section]

    title = {
        "public": "ttd-bot 帮助",
        "admin": "ttd-bot 管理功能",
        "background": "ttd-bot 自动触发 / 后台功能",
    }[section]
    heading = {
        "public": "常用功能：",
        "admin": "管理功能：",
        "background": "自动触发 / 后台功能：",
    }[section]

    lines = [title, "发送 ttd help <功能名> 可只查看单个功能。"]
    if section_docs:
        lines.append("")
        lines.append(heading)
        lines.extend(format_doc_detail(doc) for doc in section_docs)
    if section == "public":
        lines.append("")
        lines.append("管理员可发送 ttd help admin 查看管理功能。")
        lines.append("发送 ttd help auto 查看自动触发 / 后台功能。")
    return "\n\n".join(lines)


def format_doc_detail(doc: FeatureDoc) -> str:
    lines = [
        f"{doc.title}（{doc.key}）",
        f"说明：{doc.description}",
        f"可见性：{format_visibility(doc.visibility)}",
    ]
    if doc.commands:
        lines.append("用法：")
        lines.extend(f"- {command}" for command in doc.commands)
    if doc.more_info:
        lines.append(doc.more_info)
    if doc.notes:
        lines.append("备注：")
        lines.extend(f"- {note}" for note in doc.notes)
    return "\n".join(lines)


def format_visibility(visibility: str) -> str:
    if visibility == "admin":
        return "管理员"
    if visibility == "background":
        return "自动触发 / 后台"
    if visibility == "internal":
        return "内部基础设施"
    return "公开"
