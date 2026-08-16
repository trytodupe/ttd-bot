from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.plugin import get_plugin


@pytest.fixture(scope="module")
def youmind_modules():
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(superusers={"12345"})
    if get_plugin("nonebot_plugin_localstore") is None:
        nonebot.load_plugin("nonebot_plugin_localstore")
    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    package = importlib.import_module("youmind_agent")
    return {
        "package": package,
        "forward": importlib.import_module("youmind_agent.forward_bundle"),
        "results": importlib.import_module("youmind_agent.results"),
        "service": importlib.import_module("youmind_agent.service"),
        "storage": importlib.import_module("youmind_agent.storage"),
    }


def test_group_access_is_restricted_to_configured_group(youmind_modules, monkeypatch):
    package = youmind_modules["package"]
    monkeypatch.setattr(package.config, "youmind_enabled", True)
    monkeypatch.setattr(package.config, "youmind_allowed_group_ids", {1015880675})

    assert package.Config().youmind_allowed_group_ids == {1015880675}
    assert package._group_id_allowed(1015880675)
    assert not package._group_id_allowed(725601182)
    assert package._group_allowed(
        GroupMessageEvent.model_construct(group_id=1015880675)
    )
    assert not package._group_allowed(
        GroupMessageEvent.model_construct(group_id=725601182)
    )
    assert not package._group_allowed(PrivateMessageEvent.model_construct(user_id=1))


def test_parse_forward_messages_builds_transcript_and_attachment_manifest(
    youmind_modules,
):
    forward = youmind_modules["forward"]
    bundle = forward.parse_forward_messages(
        [
            {
                "user_id": 1,
                "time": 123,
                "sender": {"nickname": "Alice"},
                "message": [
                    {"type": "text", "data": {"text": "参考这张图"}},
                    {
                        "type": "image",
                        "data": {
                            "file": "../../unsafe name.png",
                            "url": "https://example.com/a.png",
                            "size": "42",
                        },
                    },
                ],
            }
        ]
    )

    assert "## 1. Alice (123)" in bundle.transcript
    assert "参考这张图" in bundle.transcript
    assert "[@附件1: unsafe_name.png]" in bundle.transcript
    assert len(bundle.attachments) == 1
    assert bundle.attachments[0].name == "unsafe_name.png"
    assert bundle.attachments[0].declared_size == 42


@pytest.mark.asyncio
async def test_fetch_forward_messages_flattens_nested_forward(youmind_modules):
    forward = youmind_modules["forward"]

    class FakeBot:
        async def call_api(self, _name, **data):
            if data["id"] == "outer":
                return {
                    "messages": [
                        {
                            "sender": {"nickname": "outer"},
                            "message": Message(
                                [
                                    MessageSegment.text("before"),
                                    MessageSegment("forward", {"id": "inner"}),
                                ]
                            ),
                        }
                    ]
                }
            return [
                {
                    "sender": {"nickname": "inner"},
                    "message": [{"type": "text", "data": {"text": "inside"}}],
                }
            ]

    messages = await forward.fetch_forward_messages(FakeBot(), "outer", 2)

    assert [item["sender"]["nickname"] for item in messages] == ["outer", "inner"]


def test_parse_turn_extracts_pending_question_and_options(youmind_modules):
    results = youmind_modules["results"]
    turn = results.parse_turn(
        {
            "messages": [
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "status": "success",
                    "blocks": [
                        {
                            "type": "tool",
                            "status": "success",
                            "toolName": "ask_user_question",
                            "toolArguments": {
                                "questions": [
                                    {
                                        "question": "预计消耗 100 credits，继续吗？",
                                        "options": [
                                            {
                                                "label": "继续",
                                                "description": "开始生成",
                                            },
                                            {
                                                "label": "取消",
                                                "description": "停止任务",
                                            },
                                        ],
                                    }
                                ]
                            },
                            "toolResult": {"status": "waiting_for_user"},
                        }
                    ],
                }
            ]
        }
    )

    assert turn.kind == "waiting_user"
    assert turn.questions == ["预计消耗 100 credits，继续吗？"]
    assert turn.options == ["继续: 开始生成", "取消: 停止任务"]


def test_parse_turn_extracts_content_images_and_media_ids(youmind_modules):
    results = youmind_modules["results"]
    turn = results.parse_turn(
        {
            "messages": [
                {
                    "id": "assistant-2",
                    "role": "assistant",
                    "status": "success",
                    "blocks": [
                        {"type": "content", "status": "success", "data": "完成了"},
                        {
                            "type": "tool",
                            "status": "success",
                            "toolName": "image_generate",
                            "toolResult": {
                                "image_urls": ["https://cdn.example/image.png"],
                                "mediaIds": ["image-media-1"],
                            },
                        },
                        {
                            "type": "tool",
                            "status": "success",
                            "toolName": "generate_seedance_video",
                            "toolResult": {"gen_media_id": "media-1"},
                        },
                    ],
                }
            ]
        }
    )

    assert turn.kind == "completed"
    assert turn.text == "完成了"
    assert turn.image_urls == ["https://cdn.example/image.png"]
    assert turn.media_ids == ["image-media-1", "media-1"]


def test_media_from_download_prefers_video_play_url(youmind_modules):
    results = youmind_modules["results"]

    assert results.media_from_download(
        {
            "file": {
                "type": "video",
                "title": "demo",
                "playUrl": "https://cdn.example/demo.mp4",
            }
        }
    ) == ("video", "https://cdn.example/demo.mp4", "demo")


@pytest.mark.asyncio
async def test_state_store_persists_project_chat_and_reply_route(
    youmind_modules, tmp_path
):
    storage = youmind_modules["storage"]
    path = tmp_path / "state.json"
    store = storage.StateStore(path)
    project = {
        "group_id": 10,
        "forward_message_id": 20,
        "board_id": "board-1",
        "status": "ready",
    }
    chat = {
        "local_id": "local-1",
        "group_id": 10,
        "user_id": 30,
        "status": "waiting_user",
        "updated_at": "now",
    }

    await store.put_project(project)
    await store.put_chat(chat)
    await store.bind_route(40, "local-1")

    reloaded = storage.StateStore(path)
    assert (await reloaded.get_project(10, 20))["board_id"] == "board-1"
    assert (await reloaded.route(40))["user_id"] == 30
    assert (await reloaded.chats_for(10, 30))[0]["local_id"] == "local-1"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_agent_prompt_records_requested_model_preferences(youmind_modules, tmp_path):
    service_module = youmind_modules["service"]
    storage = youmind_modules["storage"]
    config = SimpleNamespace(
        youmind_text_model="gpt-5.6-luna",
        youmind_image_model="gpt-image-2-2026-04-21",
    )
    service = service_module.YouMindService(
        config, storage.StateStore(tmp_path / "state.json"), tmp_path
    )

    prompt = service._agent_prompt("生成一张图", "task-id")

    assert "gpt-5.6-luna" in prompt
    assert "gpt-image-2-2026-04-21" in prompt
    assert "qq-task:task-id" in prompt


@pytest.mark.asyncio
async def test_recover_chat_id_supports_current_data_envelope(
    youmind_modules, tmp_path
):
    service_module = youmind_modules["service"]
    storage = youmind_modules["storage"]
    config = SimpleNamespace(
        youmind_text_model="gpt-5.6-luna",
        youmind_image_model="gpt-image-2-2026-04-21",
    )
    service = service_module.YouMindService(
        config, storage.StateStore(tmp_path / "state.json"), tmp_path
    )

    class FakeClient:
        async def call(self, operation, payload):
            if operation == "listChats":
                return {"data": [{"id": "chat-1"}]}
            return {
                "messages": [{"role": "user", "content": "<!-- qq-task:local-1 -->"}]
            }

    assert (
        await service._recover_chat_id(FakeClient(), "board-1", "local-1") == "chat-1"
    )


@pytest.mark.asyncio
async def test_deliver_turn_saves_hidden_image_without_sending_duplicate(
    youmind_modules, tmp_path
):
    results = youmind_modules["results"]
    service_module = youmind_modules["service"]
    storage = youmind_modules["storage"]
    store = storage.StateStore(tmp_path / "state.json")
    service = service_module.YouMindService(SimpleNamespace(), store, tmp_path)
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def call(self, operation, payload):
            calls.append((operation, payload))
            if operation == "download":
                return {
                    "downloadUrl": "https://cdn.example/original.png",
                    "file": {"id": "media-1", "type": "image", "isHidden": True},
                }
            return {}

    class FakeBot:
        async def call_api(self, operation, **payload):
            calls.append((operation, payload))
            return {"message_id": 999}

    service.client = lambda: FakeClient()
    chat = {
        "local_id": "local-1",
        "group_id": 10,
        "user_id": 20,
        "request_message_id": 30,
        "status": "running",
    }
    await store.put_chat(chat)
    turn = results.TurnResult(
        "completed",
        assistant_message_id="assistant-1",
        text="完成",
        image_urls=["https://cdn.example/compressed.jpg"],
        media_ids=["media-1"],
    )

    await service._deliver_turn(FakeBot(), chat, turn)

    assert ("saveFileToBoard", {"fileId": "media-1"}) in calls
    sent = next(
        payload["message"]
        for operation, payload in calls
        if operation == "send_group_msg"
    )
    assert [segment.type for segment in sent] == ["reply", "image", "text"]
    assert (await store.route(999))["local_id"] == "local-1"
