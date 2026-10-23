"""All tests disable dotenv before importing API code and replace every live boundary."""

import copy
import os
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings

Settings.model_config["env_file"] = None
os.environ.update(
    MONGODB_URI="mongodb://localhost:27017",
    BANK_MONGODB_URI="",
    GROQ_API_KEY="test",
    GEMINI_API_KEY="test",
)
get_settings.cache_clear()


class Cursor:
    def __init__(self, rows, projection=None):
        self.rows, self.projection = copy.deepcopy(rows), projection

    def sort(self, key, direction):
        self.rows.sort(key=lambda row: row[key], reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __aiter__(self):
        async def rows():
            for row in await self.to_list():
                yield row

        return rows()

    async def to_list(self, length=None):
        rows = copy.deepcopy(self.rows if length is None else self.rows[:length])
        if self.projection:
            for row in rows:
                if any(value == 1 for value in self.projection.values()):
                    for key in list(row):
                        if not self.projection.get(key):
                            row.pop(key)
                else:
                    for key, include in self.projection.items():
                        if not include:
                            row.pop(key, None)
        return rows


def nested(row, key):
    for part in key.split("."):
        if not isinstance(row, dict):
            return None
        row = row.get(part)
    return row


class Collection:
    def __init__(self, rows=()):
        self.rows = copy.deepcopy(list(rows))
        self.queries = []

    def matches(self, row, query):
        for key, expected in query.items():
            if key in ("$or", "$and"):
                matches = [self.matches(row, clause) for clause in expected]
                if not (any(matches) if key == "$or" else all(matches)):
                    return False
                continue
            actual = nested(row, key)
            if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
                for op, value in expected.items():
                    if op == "$exists" and (actual is not None) != value:
                        return False
                    if op == "$in" and actual not in value:
                        return False
                    if op == "$lt" and (actual is None or not actual < value):
                        return False
                    if op == "$lte" and (actual is None or not actual <= value):
                        return False
                    if op == "$gt" and (actual is None or not actual > value):
                        return False
            elif actual != expected:
                return False
        return True

    def apply_update(self, row, update):
        for key, value in update.get("$set", {}).items():
            target = row
            parts = key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = copy.deepcopy(value)
        for key in update.get("$unset", {}):
            row.pop(key, None)
        for key, value in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + value
        for key, value in update.get("$max", {}).items():
            if row.get(key) is None or row[key] < value:
                row[key] = value

    async def count_documents(self, query):
        return len([row for row in self.rows if self.matches(row, query)])

    async def find_one(self, query, projection=None):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    def find(self, query, projection=None):
        self.queries.append(copy.deepcopy(query))
        return Cursor(
            [row for row in self.rows if self.matches(row, query)], projection
        )

    async def insert_one(self, row):
        self.rows.append(copy.deepcopy(row))

    async def insert_many(self, rows):
        self.rows.extend(copy.deepcopy(rows))

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [row for row in self.rows if not self.matches(row, query)]
        return NS(deleted_count=before - len(self.rows))

    async def delete_one(self, query):
        for row in self.rows:
            if self.matches(row, query):
                self.rows.remove(row)
                return NS(deleted_count=1)
        return NS(deleted_count=0)

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if self.matches(row, query):
                self.apply_update(row, update)
                return NS(matched_count=1, modified_count=1)
        if upsert:
            row = {
                key: value
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            row.update(update.get("$setOnInsert", {}))
            self.apply_update(row, update)
            self.rows.append(row)
        return NS(matched_count=0, modified_count=int(upsert))

    async def update_many(self, query, update):
        count = 0
        for row in self.rows:
            if self.matches(row, query):
                self.apply_update(row, update)
                count += 1
        return NS(modified_count=count)

    async def find_one_and_update(self, query, update, **kwargs):
        await self.update_one(query, update, kwargs.get("upsert", False))
        return await self.find_one(query)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for key in (
        "CHAT_RATE_PER_MINUTE",
        "CHAT_RATE_PER_IP_PER_MINUTE",
        "VERIFY_RATE_PER_IP_PER_MINUTE",
    ):
        monkeypatch.setenv(key, "1000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def world(monkeypatch):
    from app import db
    from app.bank import service
    from app.services import (
        attachments,
        audit,
        document_store,
        ingestion,
        maintenance,
        memory,
        retrieval,
    )
    from app.sessions import store
    from app.verification import flow, lockout
    from scripts.seed_bank import build_seed

    cols = {
        name: Collection()
        for name in (
            "sessions",
            "messages",
            "documents",
            "chunks",
            "audit_log",
            "verify_locks",
        )
    }
    for module in (
        db,
        store,
        lockout,
        flow,
        memory,
        audit,
        maintenance,
        document_store,
        ingestion,
        retrieval,
        attachments,
    ):
        for name, collection in cols.items():
            accessor = name + "_col"
            if hasattr(module, accessor):
                monkeypatch.setattr(
                    module, accessor, lambda collection=collection: collection
                )
    bank = NS(**{name: Collection(rows) for name, rows in build_seed().items()})
    monkeypatch.setattr(service, "get_db", lambda: bank)
    from app.routers import chat
    from app.services.router import Plan

    plans, answers = [], []

    async def plan(history, message, names):
        plans.append((copy.deepcopy(history), message))
        action = (
            "get_balances"
            if "balance" in message.lower() and "minimum" not in message.lower()
            else "greeting"
        )
        return Plan(question=message, action=action)

    async def names(**kwargs):
        return []

    async def answer(history, message, sources, **kwargs):
        answers.append((history, message, sources, kwargs))
        yield (
            "Your account shows "
            + kwargs["account_data"]["accounts"][0]["balance"]
            + "."
        )

    monkeypatch.setattr(chat, "plan_turn", plan)
    monkeypatch.setattr(chat, "ready_document_names", names)
    monkeypatch.setattr(chat, "stream_answer", answer)
    monkeypatch.setattr(chat, "chat_windows", {})
    monkeypatch.setattr(chat, "ip_chat_windows", {})
    monkeypatch.setattr(flow, "verify_windows", {})
    from app import main

    async def ready():
        return "READY"

    monkeypatch.setattr(main, "vector_index_status", ready)
    client = TestClient(main.app)
    result = NS(
        client=client,
        bank=bank,
        plans=plans,
        answers=answers,
        **{name: collection for name, collection in cols.items()},
    )
    yield result
    client.close()
