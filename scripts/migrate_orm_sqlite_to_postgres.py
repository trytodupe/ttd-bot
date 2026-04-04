import argparse
import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql.sqltypes import Boolean, DateTime, JSON, Time


@dataclass(frozen=True)
class TableConfig:
    name: str
    json_columns: set[str] = field(default_factory=set)
    datetime_columns: set[str] = field(default_factory=set)
    time_columns: set[str] = field(default_factory=set)


TABLES: tuple[TableConfig, ...] = (
    TableConfig(name="nonebot_plugin_uninfo_botmodel"),
    TableConfig(
        name="nonebot_plugin_uninfo_scenemodel",
        json_columns={"scene_data"},
    ),
    TableConfig(
        name="nonebot_plugin_uninfo_usermodel",
        json_columns={"user_data"},
    ),
    TableConfig(
        name="nonebot_plugin_uninfo_sessionmodel",
        json_columns={"member_data"},
    ),
    TableConfig(
        name="nonebot_plugin_chatrecorder_messagerecord_v2",
        json_columns={"message"},
        datetime_columns={"time"},
    ),
    TableConfig(
        name="nonebot_plugin_wordcloud_schedule",
        json_columns={"target"},
        time_columns={"time"},
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate nonebot ORM data from SQLite to PostgreSQL.")
    parser.add_argument(
        "--sqlite-path",
        default="./data/nonebot_plugin_orm/db.sqlite3",
        help="Path to the source SQLite database.",
    )
    parser.add_argument(
        "--pg-url",
        required=True,
        help="Target PostgreSQL URL. postgresql:// and postgres:// are normalized to postgresql+asyncpg://.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=5000,
        help="Rows to copy per batch.",
    )
    return parser.parse_args()


def normalize_pg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def open_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_sqlite_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    if not rows:
        raise RuntimeError(f"SQLite table not found: {table_name}")
    return [row["name"] for row in rows]


def get_sqlite_count(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) AS count FROM "{table_name}"').fetchone()
    if row is None:
        raise RuntimeError(f"Failed to count SQLite table: {table_name}")
    return int(row["count"])


def fetch_sqlite_batch(
    conn: sqlite3.Connection,
    table_name: str,
    columns: list[str],
    last_id: int,
    chunk_size: int,
) -> list[sqlite3.Row]:
    column_sql = ", ".join(f'"{column}"' for column in columns)
    query = (
        f'SELECT {column_sql} FROM "{table_name}" '
        f'WHERE "id" > ? ORDER BY "id" ASC LIMIT ?'
    )
    return conn.execute(query, (last_id, chunk_size)).fetchall()


def parse_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise TypeError(f"Unsupported JSON value type: {type(value)!r}")


def parse_datetime_value(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Unsupported datetime value type: {type(value)!r}")


def parse_time_value(value: Any) -> Any:
    if value is None or isinstance(value, time):
        return value
    if isinstance(value, str):
        return time.fromisoformat(value)
    raise TypeError(f"Unsupported time value type: {type(value)!r}")


def convert_row(
    config: TableConfig,
    table: Table,
    row: sqlite3.Row,
) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for column_name in table.columns.keys():
        value = row[column_name]
        column = table.columns[column_name]

        if column_name in config.json_columns or isinstance(column.type, (JSON, JSONB)):
            converted[column_name] = parse_json_value(value)
            continue
        if column_name in config.datetime_columns or isinstance(column.type, DateTime):
            converted[column_name] = parse_datetime_value(value)
            continue
        if column_name in config.time_columns or isinstance(column.type, Time):
            converted[column_name] = parse_time_value(value)
            continue
        if isinstance(column.type, Boolean) and value is not None:
            converted[column_name] = bool(value)
            continue

        converted[column_name] = value

    return converted


async def reflect_tables(engine: AsyncEngine) -> MetaData:
    metadata = MetaData()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.reflect, only=[config.name for config in TABLES])
    return metadata


async def get_postgres_count(engine: AsyncEngine, table_name: str) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))
        value = result.scalar_one()
    return int(value)


async def reset_postgres_sequence(engine: AsyncEngine, table_name: str) -> None:
    sql = text(
        f"""
        SELECT setval(
            pg_get_serial_sequence('"${table_name_placeholder}"', 'id'),
            COALESCE((SELECT MAX(id) FROM "{table_name}"), 1),
            (SELECT COUNT(*) > 0 FROM "{table_name}")
        )
        """.replace("${table_name_placeholder}", table_name)
    )
    async with engine.begin() as conn:
        await conn.execute(sql)


async def ensure_target_empty(engine: AsyncEngine) -> None:
    for config in TABLES:
        count = await get_postgres_count(engine, config.name)
        if count != 0:
            raise RuntimeError(
                f"Target PostgreSQL table {config.name} is not empty (count={count}). "
                "Refuse to migrate into a non-empty schema."
            )


async def migrate_table(
    sqlite_conn: sqlite3.Connection,
    engine: AsyncEngine,
    table: Table,
    config: TableConfig,
    chunk_size: int,
) -> None:
    columns = get_sqlite_columns(sqlite_conn, config.name)
    source_count = get_sqlite_count(sqlite_conn, config.name)
    print(f"[migrate] {config.name}: source_count={source_count}")

    last_id = 0
    copied = 0
    while True:
        rows = fetch_sqlite_batch(
            sqlite_conn,
            config.name,
            columns,
            last_id=last_id,
            chunk_size=chunk_size,
        )
        if not rows:
            break

        batch = [convert_row(config, table, row) for row in rows]
        async with engine.begin() as conn:
            await conn.execute(table.insert(), batch)

        copied += len(batch)
        last_id = int(rows[-1]["id"])
        print(f"[migrate] {config.name}: copied={copied}/{source_count} last_id={last_id}")

    await reset_postgres_sequence(engine, config.name)
    target_count = await get_postgres_count(engine, config.name)
    if target_count != source_count:
        raise RuntimeError(
            f"Count mismatch for {config.name}: source={source_count}, target={target_count}"
        )
    print(f"[migrate] {config.name}: done target_count={target_count}")


async def async_main(args: argparse.Namespace) -> None:
    sqlite_path = Path(args.sqlite_path).expanduser().resolve()
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    pg_url = normalize_pg_url(args.pg_url)
    sqlite_conn = open_sqlite(sqlite_path)
    try:
        engine = create_async_engine(pg_url, future=True)
        try:
            metadata = await reflect_tables(engine)
            await ensure_target_empty(engine)

            for config in TABLES:
                table = metadata.tables[config.name]
                await migrate_table(
                    sqlite_conn=sqlite_conn,
                    engine=engine,
                    table=table,
                    config=config,
                    chunk_size=args.chunk_size,
                )
        finally:
            await engine.dispose()
    finally:
        sqlite_conn.close()


def main() -> None:
    args = parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
