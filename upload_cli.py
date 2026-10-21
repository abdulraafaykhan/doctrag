"""Upload one or more documents to the running API and wait until they are processed.

    uv run python upload_cli.py "C:\\path\\to\\file1.pdf" "C:\\path\\to\\notes.md"
"""
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"


def main(paths: list[str]) -> None:
    if not paths:
        sys.exit('Usage: uv run python upload_cli.py "<file1>" ["<file2>" ...]')

    files = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            sys.exit(f"File not found: {p}")
        files.append(("files", (path.name, path.read_bytes())))

    r = httpx.post(f"{BASE}/documents", files=files, timeout=120)
    if r.status_code >= 400:
        sys.exit(f"Upload failed ({r.status_code}): {r.text}")

    results = r.json()
    for x in results:
        print(x)

    pending = {x["doc_id"] for x in results if x.get("status") == "processing"}
    while pending:
        time.sleep(3)
        for d in httpx.get(f"{BASE}/documents", timeout=30).json():
            if d["id"] in pending and d["status"] != "processing":
                print(f"{d['filename']}: {d['status']} ({d['num_chunks']} chunks) {d.get('error') or ''}")
                pending.discard(d["id"])
    print("Done.")


if __name__ == "__main__":
    main(sys.argv[1:])