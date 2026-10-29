from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock

import pytest

from app.config import get_settings
from app.llm import QuotaExceeded, QuotaGuard, pacific_day


class MemoryQuotaCollection:
    def __init__(self):
        self.days = {}
        self.lock = Lock()

    def update_one(self, query, update, upsert=False):
        with self.lock:
            self.days.setdefault(query["day"], dict(update["$setOnInsert"]))

    def find_one_and_update(self, query, update, **kwargs):
        with self.lock:
            row = self.days[query["day"]]
            field = next(iter(update["$inc"]))
            if row[field] >= query[field]["$lt"]:
                return None
            row[field] += update["$inc"][field]
            row.update(update["$set"])
            return dict(row)

    def find_one(self, query):
        return self.days.get(query["day"])


def test_concurrent_daily_limit():
    cfg = get_settings()
    cfg.quotas.llm_requests_per_day = 3
    guard = QuotaGuard(MemoryQuotaCollection(), cfg)

    def attempt(_):
        try:
            guard.reserve("llm")
            return True
        except QuotaExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(12))) == 3
    assert guard.remaining()["llm"] == 0


def test_upload_threshold_preserves_queries():
    cfg = get_settings()
    cfg.quotas.embed_requests_per_day = 10
    cfg.quotas.embed_reserved_for_queries = 3
    guard = QuotaGuard(MemoryQuotaCollection(), cfg)
    for _ in range(7):
        guard.reserve("embed", upload=True)
    with pytest.raises(QuotaExceeded):
        guard.reserve("embed", upload=True)
    for _ in range(3):
        guard.reserve("embed")
    with pytest.raises(QuotaExceeded):
        guard.reserve("embed")


def test_pacific_reset_and_separate_budgets():
    cfg = get_settings()
    cfg.quotas.vlm_requests_per_day = 1
    store = MemoryQuotaCollection()
    guard = QuotaGuard(store, cfg)
    before = datetime(2026, 10, 26, 6, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)
    assert pacific_day(before) == "2026-10-25"
    assert pacific_day(after) == "2026-10-26"
    guard.reserve("vlm", now=before)
    with pytest.raises(QuotaExceeded):
        guard.reserve("vlm", now=before)
    guard.reserve("llm", now=before)
    guard.reserve("embed", now=before)
    guard.reserve("vlm", now=after)


def test_batch_reservation_is_all_or_nothing():
    cfg = get_settings()
    cfg.quotas.embed_requests_per_day = 5
    guard = QuotaGuard(MemoryQuotaCollection(), cfg)
    guard.reserve("embed", count=3)
    with pytest.raises(QuotaExceeded):
        guard.reserve("embed", count=3)
    assert guard.remaining()["embed"] == 2
    guard.reserve("embed", count=2)
    assert guard.remaining()["embed"] == 0
    with pytest.raises(ValueError):
        guard.reserve("embed", count=0)
