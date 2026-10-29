"""Cached real retrieval/extraction gate; resume without re-ingesting documents."""

import json
import re
import sys
from pathlib import Path
from time import monotonic, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.progress import track

from app.config import ROOT, get_settings
from app.db import close_db
from app.ingest.images import ImageCascade
from app.ingest.models import DocumentMeta
from app.ingest.pipeline import Pipeline
from app.ingest.store import Store
from app.llm import pacific_day
from app.log import log
from app.rag import lookup_rate, search_knowledge


def run_live_gate():
    cfg, store = get_settings(), Store()
    report = {"day": pacific_day(), "failures": []}
    report_path = ROOT / "eval/gate1_report.json"
    report_path.parent.mkdir(exist_ok=True)

    def save():
        report_path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    with log.span("DB", "gate quota before"):
        before = store.one("quota_usage", {"day": pacific_day()}) or {}
    report["quota_before"] = {
        key: before.get(key, 0) for key in ("embed_requests", "vlm_requests")
    }
    if report_path.exists():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        if previous.get("day") == report["day"]:
            report["quota_before"] = previous.get(
                "quota_before", report["quota_before"]
            )
    pipeline = Pipeline(store)
    manifest = json.loads((ROOT / "data/bank_docs/manifest.json").read_text())
    documents = store.find("documents", {"namespace": "bank"})
    by_file = {row["filename"]: row for row in documents}
    for entry in track(manifest, description="Gate 1 embedding stored chunks"):
        document = by_file.get(entry["filename"])
        if not document:
            report["failures"].append("Missing ingested sample: " + entry["filename"])
            save()
            continue
        pipeline.embed_document(document["doc_id"])
        # This verifies the hash skip without parsing or making provider requests.
        reused = pipeline.ingest(
            ROOT / "data/bank_docs" / entry["filename"],
            DocumentMeta.model_validate(entry),
            defer_embeddings=True,
        )
        if not reused.get("skipped"):
            report["failures"].append(
                "Unchanged sample was reprocessed: " + entry["filename"]
            )
    report["chunks"] = store.count("chunks", {"namespace": "bank"})
    queries = json.loads((ROOT / "eval/gate1_queries.json").read_text())
    # Atlas indexes update asynchronously; wait for a known phrase to be searchable.
    deadline = monotonic() + cfg.knowledge["retrieval"]["index_wait_seconds"]
    while monotonic() < deadline:
        probe = store.aggregate(
            "chunks",
            [
                {
                    "$match": {
                        "namespace": "bank",
                        "lifecycle": "active",
                        "$text": {"$search": "NEFT"},
                    }
                },
                {"$limit": 1},
            ],
        )
        if probe:
            break
        sleep(cfg.knowledge["retrieval"]["index_poll_seconds"])
    retrieval = []
    for case in track(queries, description="Gate 1 real hybrid retrieval"):
        result = search_knowledge(case["query"], store=store)
        items = result["data"].get("items", [])
        expected = by_file[case["file"]]["doc_id"]
        hit = any(
            item["doc_id"] == expected
            and case["contains"].lower() in item["text"].lower()
            for item in items[:5]
        )
        retrieval.append(
            {
                **case,
                "hit": hit,
                "returned": [
                    {
                        "doc_id": item["doc_id"],
                        "title": item["doc_title"],
                        "chunk_id": item["chunk_id"],
                    }
                    for item in items
                ],
            }
        )
        if any(item["doc_id"] == by_file["rates_old.xlsx"]["doc_id"] for item in items):
            report["failures"].append("Superseded rate card leaked")
        if case["query"] == "NEFT charges" and not any(
            "neft charges" in item["text"].lower() for item in items
        ):
            report["failures"].append("Exact-term NEFT charges query failed")
        report["retrieval"] = retrieval
        save()
    report["hit_rate_top5"] = sum(row["hit"] for row in retrieval) / len(retrieval)
    if report["hit_rate_top5"] < 0.90:
        report["failures"].append("Retrieval hit rate below 90 percent")
    missing = lookup_rate("fixed_deposit", store=store)
    exact = lookup_rate(
        "fixed_deposit", tenure_months=12, segment="general", store=store
    )
    if (
        missing["code"] != "NEEDS_MORE_INFO"
        or exact.get("data", {}).get("rate", {}).get("rate_percent") != "7.10"
    ):
        report["failures"].append("Exact rate lookup/dimension check failed")
    cascade = ImageCascade(store)
    cases = json.loads((ROOT / "data/gate1_images/truth.json").read_text())
    extraction = []
    for case in track(cases, description="Gate 1 live rate-table extraction"):
        result = cascade.extract(
            (ROOT / "data/gate1_images" / case["image"]).read_bytes(),
            "bank",
            mode="ingest",
        )
        actual = (result.get("structured") or {}).get("rows", [])
        correct, total, wrong_accepted = 0, 0, 0
        mismatches = []
        for row, expected_row in enumerate(case["rows"]):
            for col, expected in enumerate(expected_row):
                total += 1
                got = (
                    actual[row][col]
                    if row < len(actual) and col < len(actual[row])
                    else "<missing>"
                )

                def normal(value):
                    return re.sub(r"\s+", "", str(value)) if value is not None else None

                if normal(got) == normal(expected):
                    correct += 1
                else:
                    mismatches.append(
                        {"row": row, "col": col, "expected": expected, "actual": got}
                    )
                    if (
                        got not in {None, "<missing>"}
                        and re.findall(r"\d+(?:\.\d+)?", str(got))
                        != re.findall(r"\d+(?:\.\d+)?", str(expected))
                        and not result["needs_review"]
                    ):
                        wrong_accepted += 1
        truncated = bool(
            result.get("truncated") or (result.get("structured") or {}).get("truncated")
        )
        if case["truncated"] and not truncated:
            report["failures"].append(
                "Cut-off row not marked truncated: " + case["image"]
            )
        extraction.append(
            {
                "image": case["image"],
                "correct": correct,
                "total": total,
                "flagged": result["needs_review"],
                "flags": result["flags"],
                "wrong_accepted": wrong_accepted,
                "truncated": truncated,
                "mismatches": mismatches,
            }
        )
        report["extraction"] = extraction
        save()
    report["cells_correct"] = sum(row["correct"] for row in extraction)
    report["cells_total"] = sum(row["total"] for row in extraction)
    report["flagged_items"] = sum(row["flagged"] for row in extraction)
    report["wrong_numbers_accepted_unflagged"] = sum(
        row["wrong_accepted"] for row in extraction
    )
    report["unreadable_images"] = [
        row["image"] for row in extraction if "unreadable_image" in row["flags"]
    ]
    report["limitations"] = ["Optional reranker is disabled and was not exercised."]
    if report["unreadable_images"]:
        report["limitations"].append(
            "Both providers returned rate limits for some images; cached unreadable results remain flagged, not accepted extractions."
        )
    if report["wrong_numbers_accepted_unflagged"]:
        report["failures"].append("Unflagged wrong numbers in extraction set")
    after = store.one("quota_usage", {"day": pacific_day()}) or {}
    report["quota_after"] = {
        key: after.get(key, 0) for key in ("embed_requests", "vlm_requests")
    }
    report["quota_used"] = {
        key: report["quota_after"][key] - report["quota_before"][key]
        for key in report["quota_after"]
    }
    report["passed"] = not report["failures"]
    save()
    text = f"# Gate 1\n\nPassed: {report['passed']}\n\nRetrieval top 5: {report['hit_rate_top5']:.1%}\n\nExtraction: {report['cells_correct']}/{report['cells_total']} cells; {report['flagged_items']} flagged images; {report['wrong_numbers_accepted_unflagged']} unflagged wrong numbers.\n\nChunks: {report['chunks']}\n\nPacific-day usage: {report['quota_after']}\n\nFailures: {report['failures']}\n"
    text += f"\nUnreadable images: {report['unreadable_images']}\n\nLimitations: {' '.join(report['limitations'])}\n"
    (ROOT / "eval/gate1_report.md").write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"retrieval", "extraction"}
            },
            indent=2,
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    try:
        outcome = run_live_gate()
        raise SystemExit(0 if outcome["passed"] else 1)
    finally:
        close_db()
