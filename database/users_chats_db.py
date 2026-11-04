import motor.motor_asyncio
from info import DATABASE_NAME, DATABASE_URI


class Database:

    def __init__(self, uri, database_name):
        # Fail fast (8s) instead of hanging 30s when Mongo is unreachable
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
        self.db = self._client[database_name]
        self.col = self.db.users
        self.stats = self.db.stats

    async def ping(self):
        await self._client.admin.command("ping")

    async def add_user(self, id, name):
        await self.col.update_one(
            {"id": int(id)},
            {"$setOnInsert": {"id": int(id), "name": name}},
            upsert=True,
        )

    async def is_user_exist(self, id):
        user = await self.col.find_one({"id": int(id)})
        return bool(user)

    async def total_users_count(self):
        return await self.col.count_documents({})

    async def get_all_users(self):
        return self.col.find({})

    async def delete_user(self, user_id):
        await self.col.delete_many({"id": int(user_id)})

    # ---- lifetime counters (links made, ...) ----
    async def inc_stat(self, name: str, amount: int = 1):
        try:
            await self.stats.update_one({"_id": name}, {"$inc": {"value": amount}}, upsert=True)
        except Exception:
            pass  # stats must never break the bot

    async def get_stat(self, name: str) -> int:
        try:
            doc = await self.stats.find_one({"_id": name})
            return int(doc["value"]) if doc else 0
        except Exception:
            return 0


db = Database(DATABASE_URI, DATABASE_NAME)
