"""Small logged-store fake for pipeline code paths; no network or embeddings."""

import copy
import re
from collections import defaultdict
from threading import RLock
from types import SimpleNamespace


def get(row, path):
    for part in path.split("."):
        if not isinstance(row, dict) or part not in row:
            return None
        row = row[part]
    return row


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, child) for child in expected):
                return False
            continue
        value = get(row, key)
        if isinstance(expected, dict):
            for op, target in expected.items():
                if op == "$exists" and (value is not None) != target:
                    return False
                if op == "$in" and value not in target:
                    return False
                if op == "$lte" and (value is None or value > target):
                    return False
                if op == "$gt" and (value is None or value <= target):
                    return False
        elif value != expected:
            return False
    return True


def set_value(row, path, value):
    keys = path.split(".")
    for key in keys[:-1]:
        row = row.setdefault(key, {})
    row[keys[-1]] = value


class MemoryStore:
    def __init__(self):
        self.data = defaultdict(list)
        self.lock = RLock()

    def one(self, name, query, **kwargs):
        values = self.find(name, query, **kwargs)
        return values[0] if values else None

    def find(self, name, query, *, sort=None, limit=0):
        values = [copy.deepcopy(row) for row in self.data[name] if matches(row, query)]
        if sort:
            for key, direction in reversed(sort):
                values.sort(key=lambda row: get(row, key) or "", reverse=direction < 0)
        return values[:limit] if limit else values

    def put(self, name, identity, value):
        with self.lock:
            for index, row in enumerate(self.data[name]):
                if matches(row, identity):
                    self.data[name][index] = copy.deepcopy(value)
                    return
            self.data[name].append(copy.deepcopy(value))

    def patch(self, name, query, update, *, upsert=False):
        with self.lock:
            row = self.one(name, query)
            new = row is None
            if new and not upsert:
                return SimpleNamespace(modified_count=0)
            row = row or copy.deepcopy(query)
            for operation, fields in update.items():
                for key, value in fields.items():
                    if operation == "$set" or operation == "$setOnInsert" and new:
                        set_value(row, key, value)
                    elif operation == "$unset":
                        row.pop(key, None)
                    elif operation == "$inc":
                        set_value(row, key, (get(row, key) or 0) + value)
                    elif operation == "$addToSet":
                        values = get(row, key) or []
                        if value not in values:
                            values.append(value)
                        set_value(row, key, values)
                    elif operation == "$pull":
                        set_value(
                            row,
                            key,
                            [
                                item
                                for item in (get(row, key) or [])
                                if not (matches(item, value) if isinstance(value, dict) else item == value)
                            ],
                        )
            self.put(name, query, row)
            return SimpleNamespace(modified_count=1)

    def count(self, name, query):
        return len(self.find(name, query))

    def delete(self, name, query):
        previous = len(self.data[name])
        self.data[name] = [row for row in self.data[name] if not matches(row, query)]
        return previous - len(self.data[name])

    def reserve(self, owner, field, amount, limit):
        with self.lock:
            row = self.one("sessions", {"session_id": owner}) or {
                "session_id": owner,
                "counters": {},
            }
            used = row["counters"].get(field, 0)
            if used + amount > limit:
                return False
            row["counters"][field] = used + amount
            self.put("sessions", {"session_id": owner}, row)
            return True

    def aggregate(self, name, pipeline):
        if "$vectorSearch" in pipeline[0]:
            query = pipeline[0]["$vectorSearch"]["filter"]
            return [{**row, "vector_score": 0.8} for row in self.find(name, query)]
        query = dict(pipeline[0]["$match"])
        words = set(re.findall(r"\w+", query.pop("$text")["$search"].lower()))
        rows = self.find(name, query)
        return sorted(
            [row for row in rows if words & set(row["text"].lower().split())],
            key=lambda row: len(words & set(row["text"].lower().split())),
            reverse=True,
        )
