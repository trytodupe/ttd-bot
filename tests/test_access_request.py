import importlib
import sys
from pathlib import Path

import nonebot
import pytest


@pytest.fixture(scope="module")
def access_request_modules():
    try:
        driver = nonebot.get_driver()
    except ValueError:
        nonebot.init(superusers={"12345"})
        driver = nonebot.get_driver()

    from nonebot.adapters.onebot.v11 import Adapter

    try:
        driver.register_adapter(Adapter)
    except ValueError:
        pass

    nonebot.load_plugin("nonebot_plugin_localstore")

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    package = importlib.import_module("access_request")
    service_module = importlib.import_module("access_request.service")
    storage_module = importlib.import_module("access_request.storage")
    return package, service_module, storage_module


def test_access_request_roundtrip(access_request_modules, tmp_path):
    _, service_module, storage_module = access_request_modules
    storage_file = tmp_path / "requests.json"
    storage_module.DATA_FILE = storage_file

    service = service_module.AccessRequestService()

    assert service.is_allowed(10001, "moellmchats.private_chat") is False
    assert service.find_pending(10001, "moellmchats.private_chat") is None

    first = service.request_access(10001, "申请", "moellmchats.private_chat")
    assert first.status == "pending"
    assert first.request_id
    assert service.find_pending(10001, "moellmchats.private_chat") is not None

    duplicate = service.request_access(10001, "申请", "moellmchats.private_chat")
    assert duplicate.request_id == first.request_id

    approved = service.approve(first.request_id, reviewer_id=12345)
    assert approved is not None
    assert approved.status == "approved"
    assert service.is_allowed(10001, "moellmchats.private_chat") is True
    assert service.find_pending(10001, "moellmchats.private_chat") is None
