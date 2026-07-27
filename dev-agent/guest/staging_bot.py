from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# Running a script by path puts only its own directory (``.dev-agent``) on
# sys.path.  Staged plugins live below the workspace's ``src`` package, so the
# workspace root must be importable before NoneBot resolves manifest entries
# such as ``src/plugins/example``.
sys.path.insert(0, str(Path.cwd()))


def load_manifest() -> list[str]:
    path = Path(os.environ.get("TTD_STAGING_PLUGIN_MANIFEST", ".dev-agent/plugins.json"))
    value = json.loads(path.read_text())
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError("invalid staging plugin manifest")
    return value


nonebot.init(
    driver="~fastapi+~httpx+~aiohttp",
    host=os.environ.get("HOST", "127.0.0.1"),
    port=int(os.environ["PORT"]),
    nickname={"ttd", "Ttd", "TTD"},
    command_start={""},
    command_sep={" "},
    localstore_data_dir=os.environ["LOCALSTORE_DATA_DIR"],
    sqlalchemy_database_url=os.environ["SQLALCHEMY_DATABASE_URL"],
)
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# Only infrastructure plus plugins explicitly discovered on the agent branch are loaded.
nonebot.load_plugin("nonebot_plugin_orm")
for plugin_path in load_manifest():
    loaded = nonebot.load_plugin(Path(plugin_path))
    if loaded is None:
        raise RuntimeError(f"failed to load staging plugin: {plugin_path}")

app = nonebot.get_asgi()


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


if __name__ == "__main__" and sys.argv[1:] == ["validate"]:
    pass
elif __name__ == "__main__" and sys.argv[1:] == ["migrate"]:
    from nonebot_plugin_orm.__main__ import main as orm_main

    orm_main(args=["upgrade"], standalone_mode=False)
elif __name__ == "__main__":
    nonebot.run()
