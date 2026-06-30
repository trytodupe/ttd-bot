# CONTEXT.md

Domain glossary and architecture reference for ttd-bot. Keeps terminology consistent across sessions.

---

## 1. Domain Terminology

### QQ / OneBot Concepts

- **NapCat** — QQ protocol implementation running on this machine. Bot connects to it via WebSocket. Avoid: "QQ server", "go-cqhttp"
- **OneBot V11** — The message protocol standard between NapCat and NoneBot. All events/types use this spec
- **PrivateMessageEvent** — A DM (私聊). The bot receives all DMs directly, no `to_me()` needed
- **GroupMessageEvent** — A group chat message. Group commands must use `to_me()` to trigger only when bot is addressed
- **to_me()** — Rule that matches when the bot is addressed by nickname ("ttd", "Ttd", "TTD"). Required for group commands, unnecessary for DMs
- **MessageSegment** — Rich content unit: text, image, at, reply, etc. Don't rely on `get_plaintext()` alone — inspect segments directly for precision

### NoneBot2 Concepts

- **Matcher** — A reactive handler that triggers on events. Types: `on_command`, `on_message`, `on_notice`
- **CommandGroup** — Groups related commands under a shared prefix. Use `rule=to_me()` for group commands
- **Plugin** — A unit of functionality. Lives in `src/plugins/<name>/` (local) or installed from PyPI/git (third-party)
- **PluginMetadata** — Required `__plugin_meta__` in every plugin's `__init__.py`
- **require()** — Declares dependency on another NoneBot plugin. Must be called before importing from it. Example: `require("nonebot_plugin_apscheduler")`
- **priority** — Lower number = runs first. Most commands use `priority=10`. Background watchers use `priority=1000`
- **block=True** — Stops lower-priority matchers from running after this one handles the message

### Plugin Conventions

- **`__init__.py`** — Entry point. Defines `__plugin_meta__`, optionally loads config, imports `__main__`
- **`__main__.py`** — Actual handler logic, matcher registration, scheduler setup
- **`config.py`** — Pydantic BaseModel for env-based config from `.env`
- **`model.py`** — SQLAlchemy model (if plugin needs its own DB tables)
- **`migrations/`** — Alembic migration scripts for `nonebot-plugin-orm`
- **`storage.py`** — Local JSON file storage via `nonebot_plugin_localstore`

### Storage Systems

- **PostgreSQL** (Docker: `ttd-bot-postgres-1`, port 5432) — Primary DB. Used via `nonebot-plugin-orm` (SQLAlchemy). Also hosts Tortoise ORM tables for learning-chat
- **nonebot-plugin-orm** — SQLAlchemy-based ORM. Models inherit from `nonebot_plugin_orm.Model`. Sessions via `get_session()`. Migrations via `nb orm upgrade`
- **nonebot_plugin_localstore** — Simple JSON file storage. `store.get_data_file(plugin_name=..., filename=...)`. Used by: mc_server_checker, access_request, auto_ping, auto_react, tetr_chercher
- **SQLite** — citation_counter uses raw sqlite3 (legacy, not via ORM)
- **Tortoise ORM** — Used only by nonebot-plugin-learning-chat (forked). Config via `tortoise_orm_db_url` in `.env`

### Scheduling

- **APScheduler** — `require("nonebot_plugin_apscheduler")` then `from nonebot_plugin_apscheduler import scheduler`. Jobs registered in `@driver.on_startup`, removed in `@driver.on_shutdown`
- **"cron" trigger** — Wall-clock schedule. Example: `hour=4, minute=0` = 04:00 UTC = 12:00 UTC+8
- **"interval" trigger** — Fixed period. Example: `seconds=60`
- **"date" trigger** — One-shot at specific time
- **misfire_grace_time** — Seconds after scheduled time where the job still runs. Prevents missed fires from bot restarts
- **coalesce=True** — If multiple fires were missed, only run once

### Sending Messages Outside Handlers

- **`_select_bot()`** — Pattern to get a bot instance when there's no event context. Uses `get_bots()` + `cast(Bot, next(iter(bots.values())))`
- **`bot.call_api("send_private_msg", user_id=..., message=...)`** — Send DM from background/scheduler
- **`bot.call_api("send_group_msg", group_id=..., message=...)`** — Send group message from background/scheduler

---

## 2. Plugin Inventory

| Plugin | Location | Purpose |
|--------|----------|---------|
| keep_alive | local | 续火 — daily "1" DM reminder, toggle via DM |
| mc_server_checker | local | Minecraft server status monitoring, group queries |
| coc_apk_checker | local | Clash of Clans APK update checker, auto-upload |
| citation_counter | local | Citation/quote tracking per group |
| chat_statistics | local | Chat statistics and wordcloud |
| access_request | local | Permission request workflow |
| auto_ping | local | Superuser ping monitoring |
| auto_react | local | Auto-react to specific users |
| easy_trigger | local | Trigger-based responses, mute notices |
| sticker_to_image | local | Convert QQ stickers to images |
| tetr_chercher | local | Tetr.io stats lookup |
| release_note | local | Release note publisher |
| ttd_help | local | Help command |
| nonebot-plugin-learning-chat | forked dep | LLM chat, uses Tortoise ORM |
| nonebot-plugin-clovers | dep | Multi-platform card game framework |
| nonebot-plugin-uninfo | dep | User/session info persistence |
| nonebot-plugin-chatrecorder | dep | Message history recording |

---

## 3. Architecture Relationships

```
User message
  └─ NapCat (QQ protocol)
      └─ OneBot V11 (WebSocket)
          └─ NoneBot2 (matcher engine)
              ├─ Command matchers (priority 10, block=True)
              ├─ Message matchers (priority 20-1000)
              └─ Notice matchers (priority 1)

Background:
  APScheduler ──► plugin handler ──► bot.call_api() ──► NapCat ──► QQ

Storage:
  PostgreSQL ◄── nonebot-plugin-orm (SQLAlchemy)
             ◄── Tortoise ORM (learning-chat only)
  JSON files ◄── nonebot_plugin_localstore
  SQLite     ◄── citation_counter (raw sqlite3)
```

---

## 4. Command Convention

Commands are registered WITHOUT the "ttd" prefix. The `to_me()` matcher handles nickname detection.

```python
# ✅ Correct
command_rule = is_type(GroupMessageEvent) & to_me()
cmd_group = CommandGroup("cite", rule=command_rule, priority=10, block=True)

# ❌ Wrong — "ttd" in command name
cmd_group = CommandGroup("ttd cite", ...)
```

For DM-only commands, use `is_type(PrivateMessageEvent)` without `to_me()`.

---

## 5. Known Gotchas

- **Timezone-aware datetimes** — PostgreSQL `TIMESTAMP WITHOUT TIME ZONE` rejects timezone-aware Python datetimes. Use `datetime.utcnow()`, not `datetime.now(timezone.utc)`
- **Alembic migration duplication** — Same migration file in two locations causes `Branch name already used` crash at startup. Ensure migrations exist in exactly one place
- **`nb orm upgrade` permissions** — Data directory (`data/nonebot_plugin_orm/migrations/`) is owned by root. Run `nb orm upgrade` with sudo or let the OpenRC service handle it
- **Port 8901** — Bot's HTTP server. If testing locally, stop the service first (`rc-service ttd-bot stop`)
- **Font errors** — `clovers_groupmate_waifu` logs a harmless `Font:SourceHanSansSC-Regular not found` error on every startup. Ignore it
- **Duplicated prefix rule** — `tetr_chercher` logs `Duplicated prefix rule "tetr"` on startup. Harmless
- **Forked deps** — Some deps are pinned to `git+https://github.com/trytodupe/...`. Edit those repos in `~/repositories/qqBot/<repo>`, push, update hash in `pyproject.toml`, `uv sync`
