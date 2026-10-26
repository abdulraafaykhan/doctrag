"""Logged MongoDB operations shared by ingestion, retrieval and cleanup."""

from app.db import collection
from app.log import log


class Store:
    def one(self, name: str, query: dict, **kwargs):
        with log.span("DB", "find one", collection=name):
            return collection(name).find_one(query, **kwargs)

    def find(self, name: str, query: dict, *, sort=None, limit: int = 0):
        with log.span("DB", "find", collection=name):
            cursor = collection(name).find(query)
            if sort:
                cursor = cursor.sort(sort)
            if limit:
                cursor = cursor.limit(limit)
            return list(cursor)

    def put(self, name: str, identity: dict, value: dict):
        with log.span("DB", "upsert", collection=name):
            collection(name).replace_one(identity, value, upsert=True)

    def patch(self, name: str, query: dict, update: dict, *, upsert=False):
        with log.span("DB", "update", collection=name):
            return collection(name).update_one(query, update, upsert=upsert)

    def delete(self, name: str, query: dict) -> int:
        with log.span("DB", "delete", collection=name):
            return collection(name).delete_many(query).deleted_count

    def count(self, name: str, query: dict) -> int:
        with log.span("DB", "count", collection=name):
            return collection(name).count_documents(query)

    def aggregate(self, name: str, pipeline: list[dict]):
        with log.span("DB", "aggregate", collection=name):
            return list(collection(name).aggregate(pipeline))

    def reserve(self, owner: str, field: str, amount: int, limit: int) -> bool:
        if amount < 0 or amount > limit:
            return False
        if amount == 0:
            return True
        # The conditional update enforces session budgets across concurrent uploads.
        self.patch(
            "sessions",
            {"session_id": owner},
            {"$setOnInsert": {"session_id": owner, "counters": {}}},
            upsert=True,
        )
        with log.span("DB", "reserve upload allowance", field=field):
            result = collection("sessions").update_one(
                {
                    "session_id": owner,
                    "$or": [
                        {f"counters.{field}": {"$exists": False}},
                        {f"counters.{field}": {"$lte": limit - amount}},
                    ],
                },
                {"$inc": {f"counters.{field}": amount}},
            )
        return bool(result.modified_count)
