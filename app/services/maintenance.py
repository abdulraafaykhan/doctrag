"""Mark interrupted document ingestion as failed on startup."""
from datetime import datetime, timedelta, timezone

from app.db import documents_col


async def sweep_stuck_documents() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    result = await documents_col().update_many(
        {"status": "processing", "created_at": {"$lt": cutoff}},
        {"$set": {"status": "failed", "error": "Processing was interrupted. Please upload the file again."}},
    )
    return result.modified_count
