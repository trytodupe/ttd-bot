from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "src" / "plugins"
sys.path.insert(0, str(PLUGIN_DIR))


def _live_item(content: object) -> dict[str, object]:
    return {
        "id_str": "1228276701565288466",
        "type": "DYNAMIC_TYPE_LIVE_RCMD",
        "modules": {
            "module_dynamic": {
                "major": {
                    "type": "MAJOR_TYPE_LIVE_RCMD",
                    "live_rcmd": {"content": content},
                },
                "additional": None,
            }
        },
    }


def test_legacy_state_round_trip(tmp_path):
    from bilibili_subscription.storage import SubscriptionManager

    path = tmp_path / "bilibili_subscriptions.json"
    path.write_text(
        json.dumps(
            {
                "subscriptions": [
                    {"scope": "QQClient", "group_id": "123", "uid": "456"}
                ],
                "last_seen": {
                    "456": {
                        "last_dynamic_id": "789",
                        "last_checked": 1.0,
                        "last_live_start": 42,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    manager = SubscriptionManager(path)
    manager.add_sub("QQClient", "123", "999")
    manager.set_last_seen("999", "1000")

    reloaded = SubscriptionManager(path)
    assert reloaded.get_subs_for_group("QQClient", "123") == ["456", "999"]
    assert reloaded.get_last_seen("456") == "789"
    assert reloaded.get_last_live_start("456") == 42
    assert reloaded.get_last_seen("999") == "1000"


def test_legacy_production_shape_loads_without_migration(tmp_path):
    from bilibili_subscription.storage import SubscriptionManager

    relations = [
        ("101", "1"),
        ("102", "2"),
        ("103", "3"),
        ("104", "4"),
        ("105", "4"),
        ("105", "5"),
        ("105", "6"),
        ("105", "7"),
        ("105", "8"),
        ("106", "1"),
    ]
    path = tmp_path / "bilibili_subscriptions.json"
    path.write_text(
        json.dumps(
            {
                "subscriptions": [
                    {"scope": "QQClient", "group_id": group_id, "uid": uid}
                    for group_id, uid in relations
                ],
                "last_seen": {
                    str(uid): {"last_dynamic_id": str(uid), "last_live_start": 0}
                    for uid in range(1, 9)
                },
            }
        ),
        encoding="utf-8",
    )

    manager = SubscriptionManager(path)

    assert len(manager.get_all_uids()) == 8
    assert (
        sum(len(manager.get_groups_for_uid(uid)) for uid in manager.get_all_uids())
        == 10
    )
    assert {
        group_id
        for uid in manager.get_all_uids()
        for _, group_id in manager.get_groups_for_uid(uid)
    } == {"101", "102", "103", "104", "105", "106"}


def test_extract_live_url_from_current_major_layout():
    from bilibili_subscription.service import extract_url_from_item

    content = json.dumps(
        {"live_play_info": {"room_id": 242721, "live_start_time": 123}}
    )

    assert extract_url_from_item(_live_item(content)) == (
        "https://live.bilibili.com/242721"
    )


def test_live_status_transition_is_announced_once(tmp_path):
    from bilibili_subscription.service import collect_new_live_sessions
    from bilibili_subscription.storage import SubscriptionManager

    manager = SubscriptionManager(tmp_path / "subscriptions.json")
    manager.set_last_live_start("74152480", 0)
    statuses = {
        "74152480": {
            "live_status": 1,
            "live_time": 1785423611,
            "room_id": 1796293407,
        }
    }

    assert collect_new_live_sessions(statuses, manager) == [("74152480", "1796293407")]
    assert collect_new_live_sessions(statuses, manager) == []


def test_unknown_live_status_is_initialized_without_announcement(tmp_path):
    from bilibili_subscription.service import collect_new_live_sessions
    from bilibili_subscription.storage import SubscriptionManager

    manager = SubscriptionManager(tmp_path / "subscriptions.json")
    statuses = {
        "23396430": {
            "live_status": 1,
            "live_time": 1785418703,
            "room_id": 242721,
        }
    }

    assert collect_new_live_sessions(statuses, manager) == []
    assert manager.get_last_live_start("23396430") == 1785418703


def test_delayed_live_dynamic_is_recognized_as_already_announced(tmp_path):
    from bilibili_subscription.service import is_announced_live_item
    from bilibili_subscription.storage import SubscriptionManager

    manager = SubscriptionManager(tmp_path / "subscriptions.json")
    manager.set_last_live_start("74152480", 1785423611)
    item = _live_item(
        json.dumps(
            {
                "live_play_info": {
                    "room_id": 1796293407,
                    "live_start_time": 1785423611,
                }
            }
        )
    )

    assert is_announced_live_item(item, manager, "74152480")


@pytest.mark.asyncio
async def test_live_statuses_are_fetched_in_one_batch(monkeypatch):
    from bilibili_subscription import service

    requests: list[tuple[str, list[tuple[str, str]]]] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "74152480": {"live_status": 1},
                    "23396430": {"live_status": 0},
                },
            }

    class Client:
        def __init__(self, **kwargs):
            assert not kwargs["headers"]["User-Agent"].startswith("python-httpx")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str, *, params):
            requests.append((url, list(params.multi_items())))
            return Response()

    monkeypatch.setattr(service, "AsyncClient", Client)

    statuses = await service.fetch_live_statuses(["74152480", "23396430"])

    assert set(statuses) == {"74152480", "23396430"}
    assert requests == [
        (
            service.LIVE_STATUS_URL,
            [("uids[]", "74152480"), ("uids[]", "23396430")],
        )
    ]


@pytest.mark.asyncio
async def test_scheduler_jobs_follow_plugin_lifecycle():
    from bilibili_subscription import __main__ as plugin_main
    from nonebot_plugin_apscheduler import scheduler

    await plugin_main.register_jobs()
    try:
        assert scheduler.get_job(plugin_main.LIVE_JOB_ID) is not None
        assert scheduler.get_job(plugin_main.DYNAMIC_JOB_ID) is not None
    finally:
        await plugin_main.remove_jobs()

    assert scheduler.get_job(plugin_main.LIVE_JOB_ID) is None
    assert scheduler.get_job(plugin_main.DYNAMIC_JOB_ID) is None
