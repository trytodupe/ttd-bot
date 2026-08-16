from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class TurnResult:
    kind: Literal["pending", "waiting_user", "completed", "failed"]
    assistant_message_id: str = ""
    text: str = ""
    questions: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    media_ids: list[str] = field(default_factory=list)
    error: str = ""


def _latest_assistant(
    messages: Any, previous_assistant_id: str = ""
) -> dict[str, Any] | None:
    if isinstance(messages, dict):
        messages = messages.get("messages")
    if not isinstance(messages, list):
        return None
    assistants = [
        item
        for item in messages
        if isinstance(item, dict)
        and item.get("role") == "assistant"
        and str(item.get("id") or "") != previous_assistant_id
    ]
    return assistants[-1] if assistants else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def parse_turn(messages: Any, previous_assistant_id: str = "") -> TurnResult:
    assistant = _latest_assistant(messages, previous_assistant_id)
    if assistant is None:
        return TurnResult("pending")
    assistant_id = str(assistant.get("id") or "")
    status = str(assistant.get("status") or "")
    if status in {"errored", "aborted"}:
        error = assistant.get("error")
        return TurnResult("failed", assistant_id, error=str(error or status))

    texts: list[str] = []
    questions: list[str] = []
    options: list[str] = []
    image_urls: list[str] = []
    media_ids: list[str] = []
    blocks = (
        assistant.get("blocks") if isinstance(assistant.get("blocks"), list) else []
    )
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "content" and isinstance(block.get("data"), str):
            texts.append(block["data"].strip())
        tool_name = str(block.get("toolName") or "")
        arguments = (
            block.get("toolArguments")
            if isinstance(block.get("toolArguments"), dict)
            else {}
        )
        result = (
            block.get("toolResult") if isinstance(block.get("toolResult"), dict) else {}
        )
        if tool_name == "ask_user_question" and result.get("status") != "answered":
            raw_questions = arguments.get("questions")
            if isinstance(raw_questions, list):
                for question in raw_questions:
                    if not isinstance(question, dict):
                        continue
                    prompt = str(question.get("question") or "").strip()
                    if prompt:
                        questions.append(prompt)
                    raw_options = question.get("options")
                    if isinstance(raw_options, list):
                        for option in raw_options:
                            if not isinstance(option, dict):
                                continue
                            label = str(option.get("label") or "").strip()
                            description = str(option.get("description") or "").strip()
                            if label:
                                options.append(
                                    f"{label}: {description}" if description else label
                                )
        if "image" in tool_name:
            raw_urls = result.get("image_urls") or result.get("imageUrls")
            if isinstance(raw_urls, list):
                image_urls.extend(
                    str(value) for value in raw_urls if isinstance(value, str)
                )
            for key in ("url", "image_url", "imageUrl"):
                value = result.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    image_urls.append(value)
        for key in ("gen_media_id", "genMediaId", "file_id", "fileId"):
            value = result.get(key)
            if isinstance(value, str):
                media_ids.append(value)
        for key in ("media_ids", "mediaIds"):
            values = result.get(key)
            if isinstance(values, list):
                media_ids.extend(
                    str(value) for value in values if isinstance(value, str)
                )

    if questions:
        return TurnResult(
            "waiting_user",
            assistant_id,
            questions=questions,
            options=options,
        )
    if status in {"queued", "generating"} or any(
        isinstance(block, dict) and block.get("status") in {"generating", "executing"}
        for block in blocks
    ):
        return TurnResult("pending", assistant_id)
    return TurnResult(
        "completed",
        assistant_id,
        text="\n\n".join(value for value in texts if value),
        image_urls=_unique(image_urls),
        media_ids=_unique(media_ids),
    )


def media_from_download(payload: Any) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        return "", "", ""
    file_data = payload.get("file") if isinstance(payload.get("file"), dict) else {}
    kind = str(file_data.get("type") or "")
    title = str(file_data.get("title") or "")
    candidates = [
        payload.get("downloadUrl"),
        file_data.get("playUrl"),
        file_data.get("url"),
    ]
    webpage = file_data.get("webpage")
    if isinstance(webpage, dict):
        candidates.append(webpage.get("url"))
    url = next(
        (
            str(value)
            for value in candidates
            if isinstance(value, str) and value.startswith(("http://", "https://"))
        ),
        "",
    )
    return kind, url, title
