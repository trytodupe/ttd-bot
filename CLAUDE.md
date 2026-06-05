# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ttd-bot is a QQ chatbot built on [NoneBot2](https://v2.nonebot.dev/) with the OneBot V11 adapter. It connects to QQ via [NapCat](https://github.com/NapNeko/NapCatQQ) running on the same machine.

**Stack:** Python 3.12, uv, NoneBot2, PostgreSQL (in Docker), OpenRC

## Build & Run

```sh
# Install deps
uv sync

# Run dev (stop the service first: rc-service ttd-bot stop)
uv run python bot.py

# Run tests
uv run pytest

# Run pre-commit hooks
uv run pre-commit run --all-files
```

## Deployment

Single machine (this one). The bot runs as an **OpenRC service** (`rc-service ttd-bot start|stop|restart|status`). On restart it runs DB migrations (`nb orm upgrade`) automatically.

**Workflow:** make code changes → `rc-service ttd-bot restart`. Downtime is acceptable. Test manually by stopping the service and running `uv run python bot.py`.

PostgreSQL runs in Docker (`ttd-bot-postgres-1`), port 5432 exposed. The `.env` uses `@localhost:5432` (not Docker hostname).

CI is disabled (`.github/workflows/ci.yml.disabled`). Docker files kept as reference.

## Architecture

### Entry point

`bot.py` initializes NoneBot, registers the OneBot V11 adapter, loads plugins from `pyproject.toml` and `src/plugins/`.

### Plugins

- **Built-in plugins** (from `pyproject.toml` `[tool.nonebot.plugins]`): third-party NoneBot plugins from PyPI or git repos, listed under `[project].dependencies`
- **Local plugins** (`src/plugins/`): custom plugins loaded via `plugin_dirs = ["src/plugins"]`
- **Forked plugins**: some deps are pinned to `git+https://github.com/trytodupe/...` — edit those repos in `~/repositories/qqBot/<repo>`, push, update the hash in `pyproject.toml`, run `uv sync`

### ORM

`nonebot-plugin-orm` (SQLAlchemy) is the primary ORM for most plugins. `nonebot-plugin-learning-chat` uses `nonebot-plugin-tortoise-orm` (Tortoise ORM) which was forked and fixed for tortoise 1.1.7 compat. Set `tortoise_orm_db_url` in `.env` for Tortoise-managed databases.

### Config

`.env` — production (not committed). `.env.example` — template. `clovers.toml` — clovers plugin config.

## Commands & Matchers

The bot is addressed as `ttd`, `Ttd`, or `TTD`. Config: `COMMAND_START=[""]`, `COMMAND_SEP=[" "]`, `NICKNAME=["ttd", ...]`.

**Rule for commands like "ttd abc":** register `on_command("abc", rule=to_me(), ...)`. Do NOT put `ttd` in the command name or aliases. Reference: `citation_counter` uses `CommandGroup("cite", rule=is_type(GroupMessageEvent) & to_me())`.

- Prefer `command_rule = is_type(GroupMessageEvent) & to_me()` for addressed group commands
- `block=False` unless the plugin truly owns the message
- For messages inspecting segment content, don't rely on `get_plaintext()` alone — check `Message` segments directly
- Group LLM/chat triggers that need precision should check for explicit `at` segments, not just `to_me()`

## Git

Part of `trytodupe` GitHub account. Remote: `https://github.com/trytodupe/ttd-bot.git`.
