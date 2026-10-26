import io
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import groq
import httpx
import pytest
from memory_store import MemoryStore
from PIL import Image, ImageDraw, ImageFont

from app.config import ROOT, get_settings
from app.ingest.chunking import chunk_elements
from app.ingest.embeddings import Embeddings
from app.ingest.images import ImageCascade
from app.ingest.models import DocumentMeta, Element, utcnow
from app.ingest.parsers import parse_file
from app.ingest.pipeline import Pipeline
from app.ingest.validation import InvalidFile, validate_file
from app.llm import QuotaExceeded
from app.rag import lookup_rate, rrf, search_knowledge
from app.uploads import UploadService, UploadWorker


class FakeEmbeddings:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def embed(self, texts, **kwargs):
        self.calls += 1
        if self.fail:
            raise QuotaExceeded("fake pause")
        return [[1.0] + [0.0] * (get_settings().models.embed_dims - 1) for _ in texts]


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for module in ("pipeline", "images", "embeddings"):
        monkeypatch.setattr(f"app.ingest.{module}.ROOT", tmp_path)
    monkeypatch.setattr("app.uploads.ROOT", tmp_path)
    return tmp_path


def doc(**extra):
    return {
        "doc_id": "doc_test",
        "namespace": "bank",
        "owner_key": "bank",
        "title": "Test rates",
        "doc_type": "rate_card",
        "product": "fixed_deposit",
        "version": "1",
        "effective_date": "2026-10-01",
        "lifecycle": "active",
        **extra,
    }


@pytest.mark.parametrize(
    "filename",
    [
        "savings_faq.md",
        "digital_faq.md",
        "fraud_policy.txt",
        "fees.xlsx",
        "rates.xlsx",
        "rates_old.xlsx",
        "loan_eligibility.pdf",
        "rates_image.pdf",
        "card_cheque_policy.docx",
        "offers.pptx",
        "eligibility.csv",
    ],
)
def test_parsers_emit_metadata_and_tables(filename):
    path = ROOT / "data/bank_docs" / filename
    validate_file(path, customer=False)
    elements, total, processed = parse_file(path)
    assert (
        elements
        and total == processed
        and all(1 <= item.page <= total for item in elements)
    )
    if path.suffix in {".xlsx", ".csv", ".docx"}:
        assert any(item.type == "table" for item in elements)
    if path.suffix in {".xlsx", ".csv"}:
        assert all(item.sheet for item in elements)
    if filename in {"rates_image.pdf", "offers.pptx"}:
        assert any(item.type == "image" for item in elements)
    if filename == "offers.pptx":
        assert any(item.section == "Notes" for item in elements)


def test_chunking_headers_row_budget_and_parent_links():
    prose = Element(
        type="text",
        content="Important banking rules. " * 900,
        page=1,
        section="Rules",
        source_method="text_layer",
    )
    table = Element(
        type="table",
        content={
            "columns": ["tenure_months", "rate_percent"],
            "rows": [[str(i), "7.10"] for i in range(1, 151)],
        },
        page=2,
        section="Rates",
        source_method="spreadsheet",
    )
    parents, chunks = chunk_elements([prose, table], doc())
    ids = {parent["section_id"] for parent in parents}
    assert all(parent["token_count"] <= 1000 for parent in parents)
    assert all(
        row["section_id"] in ids and row["text"].startswith("Test rates > ")
        for row in chunks
    )
    assert all(row["token_count"] <= 80 for row in chunks if row["kind"] == "table_row")
    assert all(
        "tenure_months | rate_percent" in row["text"] and row["token_count"] <= 500
        for row in chunks
        if row["kind"] == "table"
    )


def image_fixture(table=True):
    image = Image.new("RGB", (1000, 350), "white")
    draw = ImageDraw.Draw(image)
    if table:
        words = [
            ("Tenure", 20, 40),
            ("General rate", 350, 40),
            ("Senior rate", 650, 40),
            ("12", 20, 110),
            ("7.10%", 350, 110),
            ("7.60%", 650, 110),
        ]
    else:
        words = [
            (
                "Please contact your branch to update your correspondence address safely",
                20,
                100,
            )
        ]
    blocks = []
    for text, x, y in words:
        draw.text((x, y), text, fill="black", font=ImageFont.load_default(size=24))
        blocks.append(
            (
                [
                    [x, y],
                    [x + len(text) * 12, y],
                    [x + len(text) * 12, y + 25],
                    [x, y + 25],
                ],
                text,
                0.99,
            )
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), blocks


def test_image_routes_dual_extraction_cache_and_blur(store, sandbox):
    blob, blocks = image_fixture()
    calls = []

    def vision(*args, **kwargs):
        calls.append(kwargs)
        return {
            "structured": {
                "columns": ["Tenure", "General rate", "Senior rate"],
                "rows": [["12", "7.90%", "7.60%"]],
                "overall_confidence": 0.99,
            },
            "text": "12 7.90% 7.60%",
            "status": "readable",
        }

    cascade = ImageCascade(store, SimpleNamespace(vision=vision), lambda image: blocks)
    result = cascade.extract(blob, "bank", mode="ingest")
    assert result["needs_review"] and any(
        "numeric_disagreement" in flag or "unreadable_cell" in flag
        for flag in result["flags"]
    )
    assert result["structured"]["rows"][0][1] is None
    assert calls[0]["mode"] == "ingest"
    assert cascade.extract(blob, "bank", mode="ingest")["cache_hit"] and len(calls) == 1
    text_blob, text_blocks = image_fixture(False)
    clear = ImageCascade(
        store, SimpleNamespace(vision=vision), lambda image: text_blocks
    ).extract(text_blob, "session_a", mode="online")
    assert clear["method"] == "ocr" and len(calls) == 1
    empty = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(empty, format="PNG")
    blurry = ImageCascade(
        store, SimpleNamespace(vision=vision), lambda image: []
    ).extract(empty.getvalue(), "bank", mode="ingest")
    assert blurry["needs_review"] and "reliably" in blurry["text"]


def test_embeddings_cache_retry_and_normalization(store, sandbox):
    cfg = get_settings()
    cfg.knowledge["embedding"]["retry_backoff_seconds"] = 0
    calls = []

    def embed(texts, **kwargs):
        calls.append(texts)
        if len(calls) == 1:
            raise groq.RateLimitError(
                "fake 429",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://example.invalid")
                ),
                body={},
            )
        return [[2.0] + [0.0] * 767 for _ in texts]

    service = Embeddings(store, SimpleNamespace(embed=embed), pace=False)
    vectors = service.embed(["Unique cached bank terms"])
    assert len(vectors[0]) == 768 and sum(
        value * value for value in vectors[0]
    ) == pytest.approx(1)
    assert service.embed(["Unique cached bank terms"]) == vectors and len(calls) == 2


def test_cut_off_cascade_uses_pixel_geometry(store, sandbox):
    from scripts.make_sample_docs import HEADERS, RATE_ROWS, table_image

    blocks = []
    for r, row in enumerate([HEADERS, *RATE_ROWS[:2]]):
        y = 78 if r == 0 else 130 + (r - 1) * 58
        for text, x in zip(row, (28, 298, 548, 793), strict=True):
            blocks.append(
                ([[x, y], [x + 150, y], [x + 150, y + 24], [x, y + 24]], text, 0.99)
            )

    def vision(*args, **kwargs):
        return {
            "structured": {"columns": HEADERS, "rows": RATE_ROWS, "truncated": False},
            "text": "synthetic",
            "status": "readable",
        }

    cascade = ImageCascade(store, SimpleNamespace(vision=vision), lambda image: blocks)
    result = cascade.extract(
        table_image(RATE_ROWS, clipped=True), "bank", mode="ingest"
    )
    assert result["structured"]["truncated"]
    assert result["structured"]["rows"][-1] == [None] * 4
    assert result["needs_review"] and "truncated" in result["flags"]


def test_numeric_screenshot_mismatch_requires_confirmation(store, sandbox):
    blob, blocks = image_fixture(False)
    blocks = [
        (box, "Payment amount 7.10%", confidence) for box, _, confidence in blocks
    ]

    def vision(*args, **kwargs):
        assert kwargs["mode"] == "online"
        return {
            "structured": {"amount": {"value": "7.90%", "confidence": 0.99}},
            "text": "synthetic",
            "status": "readable",
        }

    result = ImageCascade(
        store, SimpleNamespace(vision=vision), lambda image: blocks
    ).extract(blob, "session_x", mode="online")
    assert result["structured"]["amount"]["value"] is None
    assert result["needs_review"] and any(
        "unreadable_field" in flag for flag in result["flags"]
    )


def test_upload_limits_reuse_isolation_cleanup(store, sandbox):
    service = UploadService(store, Pipeline(store, embeddings=FakeEmbeddings()))
    import pymupdf

    pdf = pymupdf.open()
    for _ in range(52):
        page = pdf.new_page()
        page.insert_text(
            (30, 50),
            "Digital banking terms and conditions with enough text to identify a digital page.",
        )
    content = pdf.tobytes()
    pdf.close()
    saved = service.save("session_a", "large.pdf", content)
    assert service.save("session_a", "large.pdf", content)["reused"]
    result = service.process("session_a", saved["id"], defer_embeddings=True)
    assert result["status"] == "Partial" and "30 of 52" in result["warnings"][0]
    with pytest.raises(FileNotFoundError):
        service.get_file("session_b", saved["id"])
    document = store.one("documents", {"attachment.id": saved["id"]})
    assert document["pages_processed"] == 30
    store.patch(
        "documents",
        {"doc_id": document["doc_id"]},
        {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}},
    )
    assert service.cleanup() == 1 and not store.find(
        "chunks", {"doc_id": document["doc_id"]}
    )
    assert not Path(document["storage_path"]).exists()


def test_reject_corrupt_password_and_executable(store, sandbox):
    import pymupdf

    service = UploadService(store, Pipeline(store, embeddings=FakeEmbeddings()))
    for name, data in (
        ("bad.pdf", b"not pdf"),
        ("bad.exe", b"MZbinary"),
        ("spoof.txt", b"MZbinary"),
    ):
        with pytest.raises(InvalidFile):
            service.save("session_x", name, data)
    protected = sandbox / "protected.pdf"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((20, 40), "Password protected")
    pdf.save(
        protected,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="fake-owner",
        user_pw="fake-user",
    )
    pdf.close()
    with pytest.raises(InvalidFile, match="Password"):
        parse_file(protected)


def test_pause_resume_no_duplicate_chunks_and_rate_rows(store, sandbox):
    embeddings = FakeEmbeddings(fail=True)
    pipeline = Pipeline(store, embeddings=embeddings)
    path = ROOT / "data/bank_docs/rates.xlsx"
    result = pipeline.ingest(
        path,
        DocumentMeta(
            title="Current rates",
            product="fixed_deposit",
            doc_type="rate_card",
            effective_date="2026-10-01",
        ),
    )
    assert result["paused_quota"]
    before = store.count("chunks", {})
    assert store.count("rate_rows", {}) >= 6
    embeddings.fail = False
    pipeline.embed_document(result["doc_id"])
    assert store.count("chunks", {}) == before
    assert pipeline.ingest(path, DocumentMeta(title="Current rates"))["skipped"]
    assert lookup_rate("fixed_deposit", store=store)["code"] == "NEEDS_MORE_INFO"
    rate = lookup_rate(
        "fixed_deposit", tenure_months=12, segment="general", store=store
    )
    assert rate["data"]["rate"]["rate_percent"] == "7.10"


def test_rrf_filters_superseded_and_no_good_match(store, sandbox):
    vector = [{"chunk_id": "a"}, {"chunk_id": "b"}]
    keyword = [{"chunk_id": "b"}]
    assert rrf(vector, keyword, 60)[0]["chunk_id"] == "b"
    assert (
        search_knowledge(
            "valid query", namespace="user", owner_key="bank", store=store
        )["code"]
        == "INVALID_ARGUMENT"
    )
    assert search_knowledge("no docs query", store=store)["data"]["no_good_match"]
    base = {
        **doc(),
        "section_id": "s1",
        "page": 1,
        "confidence": 1,
        "text": "NEFT charges are free",
        "chunk_id": "active",
    }
    store.put("chunks", {"chunk_id": "active"}, base)
    store.put(
        "chunks",
        {"chunk_id": "old"},
        {**base, "chunk_id": "old", "lifecycle": "superseded"},
    )
    store.put(
        "chunks",
        {"chunk_id": "private"},
        {**base, "chunk_id": "private", "namespace": "user", "owner_key": "session_a"},
    )
    found = search_knowledge("NEFT charges", store=store, embeddings=FakeEmbeddings())
    assert [row["chunk_id"] for row in found["data"]["items"]] == ["active"]
    aggregate = store.aggregate
    store.aggregate = lambda name, stages: (
        [{**base, "vector_score": 0.1}] if "$vectorSearch" in stages[0] else []
    )
    assert search_knowledge(
        "unrelated zebras", store=store, embeddings=FakeEmbeddings()
    )["data"]["no_good_match"]
    store.aggregate = aggregate


def test_crash_resumes_embedding_stage_without_reparse(store, sandbox, monkeypatch):
    calls = []

    class CrashingEmbeddings:
        def embed(self, texts, **kwargs):
            calls.append(texts)
            if len(calls) == 1:
                raise RuntimeError("simulated process crash")
            return [[1.0] + [0.0] * 767 for _ in texts]

    pipeline = Pipeline(store, embeddings=CrashingEmbeddings())
    source = ROOT / "data/bank_docs/savings_faq.md"
    with pytest.raises(RuntimeError):
        pipeline.ingest(source, DocumentMeta(title="Crash fixture"))
    ids = {row["chunk_id"] for row in store.find("chunks", {})}
    monkeypatch.setattr(
        "app.ingest.pipeline.parse_file",
        lambda *args, **kwargs: pytest.fail("Resume reparsed the unchanged document"),
    )
    resumed = pipeline.ingest(source, DocumentMeta(title="Crash fixture"))
    assert (
        resumed["skipped"]
        and {row["chunk_id"] for row in store.find("chunks", {})} == ids
    )
    assert store.one("ingest_jobs", {})["state"] == "done"


def test_upload_budget_pause_preserves_bank_queries(store, sandbox):
    def embed(texts, **kwargs):
        if kwargs.get("upload"):
            raise QuotaExceeded("upload reservation exhausted at threshold")
        return [[1.0] + [0.0] * 767 for _ in texts]

    embeddings = Embeddings(store, SimpleNamespace(embed=embed), pace=False)
    service = UploadService(store, Pipeline(store, embeddings=embeddings))
    attachment = service.save(
        "session_a",
        "budget.txt",
        b"Customer uploaded banking document with some useful text",
    )
    result = service.process("session_a", attachment["id"])
    assert result["status"] == "Processing"
    assert store.one("ingest_jobs", {})["state"] == "paused_quota"
    assert embeddings.embed(["Bank FAQ remains available"], query=True)[0][0] == 1


def test_background_upload_worker_publishes_ready_status(store, sandbox):
    from threading import Event

    get_settings().knowledge["worker"]["poll_seconds"] = 0.01
    service = UploadService(store, Pipeline(store, embeddings=FakeEmbeddings()))
    saved = service.save(
        "session_a",
        "queued.txt",
        b"Background worker processes this useful customer document",
    )
    ready = Event()
    process = service.process

    def complete(*args, **kwargs):
        result = process(*args, **kwargs)
        ready.set()
        return result

    service.process = complete
    worker = UploadWorker(service)
    try:
        worker.start()
        assert ready.wait(5)
        document = store.one("documents", {"attachment.id": saved["id"]})
        assert document["attachment"]["status"] == "Ready"
        assert document["embedding_state"] == "ready"
    finally:
        worker.stop()
