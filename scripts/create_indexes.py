"""Create normal/text indexes and wait for the Atlas vector index."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_db, create_indexes
from app.log import log

if __name__ == "__main__":
    try:
        create_indexes()
    except Exception as exc:
        log.step("DB", "index creation failed", error=type(exc).__name__, level="ERROR")
        raise SystemExit(1) from None
    finally:
        close_db()
