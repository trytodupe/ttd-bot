"""Migrate learning_chat SQLite data to PostgreSQL."""
import asyncio
import sqlite3
import asyncpg
import os
import re

SQLITE_PATH = "/home/ttd/deploy/ttd-bot/data/learning_chat/learning_chat.db"
PG_DSN = "postgresql://ttd_bot:ttdbotpg20260404d5c8e7a1f2b3@localhost:5432/ttd_bot"

BATCH_SIZE = 50000
NULL_RE = re.compile("\\x00")
JSON_NULL_RE = re.compile("\\\\u0000")


def sanitize(val):
    """Remove null characters that PG rejects."""
    if isinstance(val, str):
        val = NULL_RE.sub("", val)
        val = JSON_NULL_RE.sub("", val)
    return val


CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS "blacklist" (
    "id" SERIAL PRIMARY KEY,
    "keywords" TEXT NOT NULL,
    "global_ban" BOOLEAN NOT NULL DEFAULT FALSE,
    "ban_group_id" JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS "idx_blacklist_keyword_6397b1" ON "blacklist" (md5("keywords"));

CREATE TABLE IF NOT EXISTS "context" (
    "id" SERIAL PRIMARY KEY,
    "keywords" TEXT NOT NULL,
    "time" BIGINT NOT NULL,
    "count" INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS "idx_context_keyword_6da3e6" ON "context" (md5("keywords"), "time");

CREATE TABLE IF NOT EXISTS "answer" (
    "id" SERIAL PRIMARY KEY,
    "keywords" TEXT NOT NULL,
    "group_id" BIGINT NOT NULL,
    "count" INTEGER NOT NULL DEFAULT 1,
    "time" BIGINT NOT NULL,
    "messages" JSONB NOT NULL DEFAULT '[]',
    "context_id" INTEGER REFERENCES "context" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_answer_keyword_eebbd3" ON "answer" (md5("keywords"), "time");

CREATE TABLE IF NOT EXISTS "message" (
    "id" SERIAL PRIMARY KEY,
    "group_id" BIGINT NOT NULL,
    "user_id" BIGINT NOT NULL,
    "message_id" BIGINT NOT NULL,
    "message" TEXT NOT NULL,
    "raw_message" TEXT NOT NULL,
    "plain_text" TEXT NOT NULL,
    "time" BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_message_group_i_382553" ON "message" ("group_id", "time");
"""


async def migrate():
    conn = await asyncpg.connect(PG_DSN)
    try:
        print("Creating tables...")
        await conn.execute(CREATE_TABLES)
        print("Tables created.")

        sqlite = sqlite3.connect(SQLITE_PATH)
        sqlite.row_factory = sqlite3.Row

        tables = ["blacklist", "context", "answer", "message"]
        for table in tables:
            cursor = sqlite.execute(f"SELECT COUNT(*) FROM {table}")
            total = cursor.fetchone()[0]
            print(f"\nMigrating {table}: {total} rows...")

            cursor = sqlite.execute(f"SELECT * FROM {table}")
            columns = [d[0] for d in cursor.description]
            placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
            cols = ", ".join(f'"{c}"' for c in columns)

            batch = []
            count = 0
            for row in cursor:
                row_data = [sanitize(v) for v in row]
                if table == "blacklist":
                    ban_idx = columns.index("global_ban")
                    row_data[ban_idx] = bool(row_data[ban_idx])
                batch.append(tuple(row_data))
                if len(batch) >= BATCH_SIZE:
                    await conn.executemany(
                        f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})',
                        batch,
                    )
                    count += len(batch)
                    print(f"  {table}: {count}/{total}")
                    batch = []

            if batch:
                await conn.executemany(
                    f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})',
                    batch,
                )
                count += len(batch)

            await conn.execute(
                f"SELECT setval('\"{table}_id_seq\"', (SELECT COALESCE(MAX(id), 0) FROM \"{table}\"))"
            )
            print(f"  {table}: {count}/{total} done.")

        sqlite.close()
        print("\nMigration complete!")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
