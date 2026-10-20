import json
import os
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self):
        self.spammers = {}
        self.messages = {}

    async def init(self):
        return None

    async def ping(self):
        return True

    async def add_spammer(self, user_uuid, username=None):
        self.spammers[user_uuid] = {
            "uuid": user_uuid,
            "username": username,
            "created_at": self.spammers.get(user_uuid, {}).get("created_at", now()),
        }

    async def remove_spammer(self, user_uuid):
        self.spammers.pop(user_uuid, None)

    async def list_spammers(self):
        return list(self.spammers.values())

    async def is_spammer(self, user_uuid):
        return user_uuid in self.spammers

    async def save_message(self, message):
        self.messages[message["id"]] = json.loads(json.dumps(message))

    async def update_message(self, message_id, **fields):
        current = self.messages[message_id]
        current.update(fields)

    async def get_message(self, message_id):
        message = self.messages.get(message_id)
        return json.loads(json.dumps(message)) if message else None

    async def list_messages(self, limit=30):
        ordered = sorted(self.messages.values(), key=lambda item: item["created_at"], reverse=True)
        return json.loads(json.dumps(ordered[:limit]))


class MySQLStore:
    def __init__(self, pool):
        self.pool = pool

    async def init(self):
        await self._exec(
            """
            CREATE TABLE IF NOT EXISTS spammers (
                uuid VARCHAR(36) PRIMARY KEY,
                username VARCHAR(255) NULL,
                created_at VARCHAR(40) NOT NULL
            )
            """
        )
        await self._exec(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id VARCHAR(36) PRIMARY KEY,
                kind VARCHAR(16) NOT NULL,
                user_uuid VARCHAR(36) NOT NULL,
                username VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                priority INTEGER NULL,
                queue_name VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL,
                trace TEXT NOT NULL,
                correlation_id VARCHAR(36) NOT NULL,
                servicenow_number VARCHAR(64) NULL,
                created_at VARCHAR(40) NOT NULL
            )
            """
        )

    async def ping(self):
        await self._fetch("SELECT 1 AS ok")

    async def add_spammer(self, user_uuid, username=None):
        if await self.is_spammer(user_uuid):
            await self._exec(
                "UPDATE spammers SET username=%s WHERE uuid=%s",
                (username, user_uuid),
            )
            return
        await self._exec(
            "INSERT INTO spammers (uuid, username, created_at) VALUES (%s, %s, %s)",
            (user_uuid, username, now()),
        )

    async def remove_spammer(self, user_uuid):
        await self._exec("DELETE FROM spammers WHERE uuid=%s", (user_uuid,))

    async def list_spammers(self):
        return await self._fetch(
            "SELECT uuid, username, created_at FROM spammers ORDER BY created_at"
        )

    async def is_spammer(self, user_uuid):
        rows = await self._fetch("SELECT uuid FROM spammers WHERE uuid=%s", (user_uuid,))
        return bool(rows)

    async def save_message(self, message):
        await self._exec(
            """
            INSERT INTO messages (
                id, kind, user_uuid, username, description, priority, queue_name,
                status, trace, correlation_id, servicenow_number, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                message["id"],
                message["kind"],
                message["user_uuid"],
                message.get("username") or "",
                message["description"],
                message.get("priority"),
                message["queue_name"],
                message["status"],
                json.dumps(message.get("trace") or []),
                message["correlation_id"],
                message.get("servicenow_number"),
                message["created_at"],
            ),
        )

    async def update_message(self, message_id, **fields):
        allowed = {"status", "servicenow_number", "priority", "queue_name", "trace"}
        sets = []
        values = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(key)
            if key == "trace" and not isinstance(value, str):
                value = json.dumps(value)
            sets.append(f"{key}=%s")
            values.append(value)
        if not sets:
            return
        values.append(message_id)
        await self._exec(
            f"UPDATE messages SET {', '.join(sets)} WHERE id=%s",
            values,
        )

    async def get_message(self, message_id):
        rows = await self._fetch("SELECT * FROM messages WHERE id=%s", (message_id,))
        return _message(rows[0]) if rows else None

    async def list_messages(self, limit=30):
        rows = await self._fetch(
            "SELECT * FROM messages ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        return [_message(row) for row in rows]

    async def _fetch(self, sql, params=()):
        import aiomysql

        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def _exec(self, sql, params=()):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
            await conn.commit()


def _message(row):
    item = dict(row)
    item["trace"] = json.loads(item.get("trace") or "[]")
    return item


async def connect_from_env(retries=30):
    import asyncio

    import aiomysql

    last = None
    for _ in range(retries):
        try:
            pool = await aiomysql.create_pool(
                host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
                port=int(os.environ.get("MYSQL_PORT", "3306")),
                user=os.environ.get("MYSQL_USER", "events"),
                password=os.environ.get("MYSQL_PASSWORD", "events"),
                db=os.environ.get("MYSQL_DATABASE", "events"),
                autocommit=False,
            )
            store = MySQLStore(pool)
            await store.init()
            await store.ping()
            return store
        except Exception as exc:
            last = exc
            await asyncio.sleep(1)
    raise last
