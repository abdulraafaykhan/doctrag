"""Ingest once by hash; resume incomplete jobs and pending embeddings."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.progress import track

from app.config import ROOT
from app.db import close_db
from app.ingest.models import DocumentMeta
from app.ingest.pipeline import Pipeline
from app.log import log


def ingest_bank_docs(*, defer_embeddings=False):
    folder = ROOT / "data/bank_docs"
    entries = json.loads((folder / "manifest.json").read_text())
    pipeline = Pipeline()
    results = []
    for entry in track(entries, description="Bank document ingestion"):
        path = folder / entry["filename"]
        result = pipeline.ingest(
            path, DocumentMeta.model_validate(entry), defer_embeddings=defer_embeddings
        )
        results.append(
            {
                "filename": path.name,
                "doc_id": result["doc_id"],
                "state": result["processing_state"],
                "skipped": result.get("skipped", False),
            }
        )
        log.step("INGEST", "document progress", **results[-1])
        if result.get("paused_quota"):
            break
    (ROOT / "data/bank_docs/ingest_report.json").write_text(
        json.dumps(results, indent=2)
    )
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--defer-embeddings", action="store_true")
    options = parser.parse_args()
    try:
        ingest_bank_docs(defer_embeddings=options.defer_embeddings)
    finally:
        close_db()
