"""Retry only the two Gate 1 provider failures; never re-ingest bank documents."""

import io
import json
import sys
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from app.config import ROOT, get_settings
from app.db import close_db
from app.ingest.images import ImageCascade
from app.ingest.models import Element, digest, utcnow
from app.ingest.chunking import rate_records
from app.ingest.store import Store
from app.rag import lookup_rate
from tests.memory_store import MemoryStore


def main():
    store = Store()
    report = json.loads((ROOT / "eval/gate1_report.json").read_text())
    mismatches = [row for row in report["extraction"] if row["mismatches"]]
    assert sum(len(row["mismatches"]) for row in mismatches) == 24
    assert all(row["flagged"] or all(cell["actual"] is None for cell in row["mismatches"]) for row in mismatches)
    cases = json.loads((ROOT / "data/gate1_images/truth.json").read_text())
    pending = []
    cooldown = False
    for name in report["unreadable_images"]:
        blob = (ROOT / "data/gate1_images" / name).read_bytes()
        image = Image.open(io.BytesIO(blob)).convert("RGB")
        key = digest(f"{image.size}|".encode() + image.tobytes())
        job = store.one("ingest_jobs", {"job_id": "retry_" + key})
        if not job or job['state'] != 'done':
            cooldown = True
            store.patch("image_cache", {"owner_key": "bank", "image_hash": key}, {"$set": {"result.needs_retry": True, "result.processing_state": "needs_retry"}})
            store.put("ingest_jobs", {"job_id": "retry_" + key}, {"job_id": "retry_" + key, "image": name, "state": "needs_retry", "updated_at": utcnow()})
        pending.append((name, key, blob))
    # A cooldown avoids repeating the provider burst that caused the original failures.
    deadline = monotonic() + (get_settings().knowledge["images"]["retry_cooldown_seconds"] if cooldown else 0)
    while monotonic() < deadline:
        print("Cooling down before the two targeted retries", flush=True)
        sleep(min(30, deadline - monotonic()))
    results = []
    for name, key, blob in pending:
        result = ImageCascade(store).extract(blob, "bank", mode="ingest")
        state = "needs_retry" if result.get("needs_retry") else "done"
        store.patch("ingest_jobs", {"job_id": "retry_" + key}, {"$set": {"state": state, "updated_at": utcnow()}})
        truth = next(row for row in cases if row["image"] == name)
        actual = (result.get("structured") or {}).get("rows", [])
        correct = sum(str(value).replace(" ", "") == str(actual[i][j]).replace(" ", "") for i, row in enumerate(truth["rows"]) for j, value in enumerate(row) if i < len(actual) and j < len(actual[i]))
        # Fixture rates are isolated from the public bank corpus, which has different rates.
        isolated = MemoryStore()
        lookups = []
        if result.get("structured"):
            meta = {"doc_id": name, "namespace": "bank", "owner_key": "bank", "lifecycle": "active", "effective_date": "2026-01-01", "product": "fixed_deposit"}
            element = Element(type="table", content=result["structured"], page=1, section=name, source_method="vlm", needs_review=result["needs_review"], flags=result['flags'])
            _, rows = rate_records(element, meta)
            for row in rows:
                isolated.put("rate_rows", {"row_id": row["row_id"]}, row)
            for row in truth["rows"]:
                if row[0] is None:
                    continue
                found = lookup_rate("fixed_deposit", tenure_months=int(row[0]), segment="general", store=isolated)
                lookups.append({"tenure": row[0], "code": found["code"], "rate": found.get("data", {}).get("rate", {}), "expected": row[1]})
        bank = lookup_rate("fixed_deposit", tenure_months=12, segment="general", store=store)
        assert all(row['code'] == 'OK' and Decimal(row['rate']['rate_percent']) == Decimal(row['expected'].rstrip('%')) for row in lookups)
        assert bank.get('data', {}).get('rate', {}).get('rate_percent') == '7.10'
        results.append({"image": name, "state": state, "readable": bool(result.get("structured")), "correct": correct, "total": 12, "flags": result["flags"], "fixture_lookup": lookups, "bank_lookup": bank})
        print(json.dumps(results[-1], default=str), flush=True)
    (ROOT / "eval/gate1_retry.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    report_file = ROOT / 'eval/gate1_report.md'
    original = report_file.read_text(encoding='utf-8').split('## Targeted retry')[0]
    report_file.write_text('\n'.join(line for line in original.splitlines() if not line.startswith('Targeted retry ')).rstrip() + '\n', encoding='utf-8')
    with report_file.open("a", encoding="utf-8") as handle:
        handle.write('\n## Targeted retry\n\nAfter a three-minute cooldown, only the two failed images were retried in ingest mode; successful results are now reused from cache.\n\n| Image | Expected cells correct | Complete-row lookup | State |\n|---|---:|---|---|\n')
        for row in results:
            rates = ', '.join(f"{value['tenure']}m={value['rate'].get('rate_percent')}%" for value in row['fixture_lookup'])
            handle.write(f"| {row['image']} | {row['correct']}/12 | {rates} | {row['state']} |\n")
        handle.write('\nThe cut-off row in rates_19.png remains null and flagged, and is excluded from lookup. Fixture rows were checked in isolation; the Atlas bank 12-month general FD rate remains 7.10%. No bank document was re-ingested.\n')


if __name__ == "__main__":
    try:
        main()
    finally:
        close_db()
