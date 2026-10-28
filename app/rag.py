"""Tenant-filtered hybrid retrieval and exact structured-rate lookup."""

import re
from datetime import date

from pydantic import BaseModel, Field

from app.config import get_settings
from app.ingest.embeddings import Embeddings
from app.ingest.store import Store
from app.log import log
from app.llm import QuotaExceeded


class SearchArgs(BaseModel):
    query: str
    doc_type: str | None = None
    product: str | None = None
    top_k: int | None = None


class RateArgs(BaseModel):
    product: str
    tenure_months: int | None = Field(default=None, ge=1)
    amount_rupees: int | None = Field(default=None, ge=0)
    segment: str | None = None
    as_of: date | None = None


def envelope(code, data=None, display="", hint=""):
    return {
        "ok": code == "OK",
        "code": code,
        "data": data or {},
        "hint": hint,
        "display": display,
    }


def round_search_values(value):
    if isinstance(value, float):
        return round(value, get_settings().retrieval['result_float_decimals'])
    if isinstance(value, dict):
        return {key: round_search_values(child) for key, child in value.items()}
    if isinstance(value, list):
        return [round_search_values(child) for child in value]
    return value


def rrf(vector: list[dict], keyword: list[dict], k: int):
    rows, scores = {}, {}
    for source in (vector, keyword):
        for rank, row in enumerate(source, 1):
            key = row["chunk_id"]
            rows.setdefault(key, row)
            scores[key] = scores.get(key, 0) + 1 / (k + rank)
    return [
        {**rows[key], "rrf_score": score}
        for key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ]


def search_knowledge(
    query: str,
    *,
    namespace="bank",
    owner_key="bank",
    doc_id=None,
    doc_type=None,
    product=None,
    top_k=None,
    store=None,
    embeddings=None,
):
    cfg = get_settings()
    rules = cfg.knowledge["retrieval"]
    args = SearchArgs(query=query, doc_type=doc_type, product=product, top_k=top_k)
    top_k = args.top_k or rules["top_k"]
    if (
        not rules["min_query_chars"] <= len(query) <= rules["max_query_chars"]
        or not 1 <= top_k <= rules["max_top_k"]
        or namespace not in {"bank", "user"}
        or namespace == "user"
        and owner_key == "bank"
    ):
        return envelope("INVALID_ARGUMENT")
    if (
        product
        and product not in cfg.products["products"]
        or doc_type
        and doc_type not in {"faq", "policy", "rate_card", "offer"}
    ):
        return envelope("INVALID_ARGUMENT")
    store = store or Store()
    filters = {
        "namespace": namespace,
        "owner_key": "bank" if namespace == "bank" else owner_key,
        "lifecycle": "active",
    }
    if product:
        filters["product"] = product
    if doc_type:
        filters["doc_type"] = doc_type
    if doc_id:
        filters["doc_id"] = doc_id
    if namespace == "user":
        from app.ingest.models import utcnow
        filters["expires_at"] = {"$gt": utcnow()}
    if not store.count("chunks", filters):
        return envelope(
            "NO_DOCUMENTS",
            {"items": [], "no_good_match": True},
            cfg.knowledge["messages"]["no_documents"],
        )
    embeddings = embeddings or Embeddings(store)
    try:
        vector = embeddings.embed([query], query=True)[0]
    except QuotaExceeded:
        vector = None
        log.step("RAG", "embedding quota exhausted; keyword-only retrieval")
    vector_filters = {key: value for key, value in filters.items() if key not in {"doc_id", "expires_at"}}
    vector_rows = store.aggregate(
        "chunks",
        [
            {
                "$vectorSearch": {
                    "index": cfg.database.vector_index,
                    "path": "embedding",
                    "queryVector": vector,
                    "numCandidates": rules["vector_num_candidates"],
                    "limit": cfg.retrieval["candidates_each"],
                    "filter": vector_filters,
                }
            },
            {"$match": filters},
            {"$set": {"vector_score": {"$meta": "vectorSearchScore"}}},
        ],
    ) if vector is not None else []
    keyword_rows = store.aggregate(
        "chunks",
        [
            {"$match": {**filters, "$text": {"$search": query}}},
            {"$sort": {"score": {"$meta": "textScore"}}},
            {"$limit": cfg.retrieval["candidates_each"]},
        ],
    )
    merged = rrf(vector_rows, keyword_rows, cfg.retrieval["rrf_k"])
    if cfg.features["reranker"]:
        from sentence_transformers import CrossEncoder

        with log.span("RAG", "local rerank"):
            scores = CrossEncoder(cfg.models.reranker).predict(
                [(query, row["text"]) for row in merged]
            )
        merged = [
            row
            for _, row in sorted(
                zip(scores, merged, strict=True), key=lambda item: item[0], reverse=True
            )
        ]
    items, seen, used = [], set(), 0
    query_words = set(re.findall(r"\w+", query.lower()))
    vector_scores = {row["chunk_id"]: row.get("vector_score", 0) for row in vector_rows}
    best = 0.0
    for row in merged:
        overlap = len(query_words & set(re.findall(r"\w+", row["text"].lower()))) / max(
            1, len(query_words)
        )
        best = max(best, vector_scores.get(row["chunk_id"], 0), overlap)
        if row["section_id"] in seen:
            continue
        parent = store.one(
            "sections",
            {
                "section_id": row["section_id"],
                **{k: filters[k] for k in ("namespace", "owner_key", "lifecycle")},
            },
        )
        text = parent["text"] if parent else row["text"]
        cost = len(text) // cfg.chunking["chars_per_token"]
        if used + cost > cfg.retrieval["context_token_budget"]:
            continue
        document = store.one("documents", {"doc_id": row["doc_id"]}) or {}
        items.append(
            {
                "n": len(items) + 1,
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "text": text,
                "doc_title": document.get("title", ""),
                "page": row["page"],
                "version": row["version"],
                "effective_date": row["effective_date"],
                "confidence": row["confidence"],
                "needs_review": row.get("needs_review", False),
                "label": "bank" if namespace == "bank" else "your file",
                "score": row["rrf_score"],
            }
        )
        used += cost
        seen.add(row["section_id"])
        if len(items) >= min(top_k, cfg.retrieval["parents_to_model"]):
            break
    low = best < cfg.retrieval["min_score"]
    log.step(
        "RAG",
        "hybrid search",
        why='vector and regular text indexes provide complementary candidates; Python RRF merges them',
        vector_index=cfg.database.vector_index,
        filters=filters,
        vector=len(vector_rows),
        keyword=len(keyword_rows),
        merged=len(merged),
        no_good_match=low,
    )
    return round_search_values(envelope("OK", {"items": [] if low else items, "no_good_match": low}))


def lookup_rate(
    product: str,
    *,
    tenure_months=None,
    amount_rupees=None,
    segment=None,
    as_of=None,
    store=None,
):
    cfg = get_settings()
    args = RateArgs(
        product=product,
        tenure_months=tenure_months,
        amount_rupees=amount_rupees,
        segment=segment,
        as_of=as_of,
    )
    if (
        product not in cfg.products["products"]
        or args.tenure_months
        and args.tenure_months > cfg.knowledge["retrieval"]["tenure_max_months"]
        or segment
        and segment not in cfg.products["segments"]
    ):
        return envelope("INVALID_ARGUMENT")
    store = store or Store()
    query = {
        "namespace": "bank",
        "owner_key": "bank",
        "product": product,
        "lifecycle": "active",
        "effective_date": {"$lte": (args.as_of or date.today()).isoformat()},
        "needs_review": False,
    }
    if tenure_months is not None:
        query["dims.tenure_months"] = tenure_months
    if segment is not None:
        query["dims.segment"] = segment
    rows = store.find("rate_rows", query, sort=[("effective_date", -1)])
    if amount_rupees is not None:
        rows = [
            row
            for row in rows
            if row["dims"].get("amount_min_rupees", 0)
            <= amount_rupees
            <= row["dims"].get("amount_max_rupees", amount_rupees)
        ]
    if not rows:
        return envelope("NOT_FOUND")
    missing = [
        field
        for field in cfg.products["products"][product]["dims"]
        if getattr(args, field, None) is None
        and len({row["dims"].get(field) for row in rows}) > 1
    ]
    if missing:
        return envelope(
            "NEEDS_MORE_INFO",
            {"missing": missing},
            cfg.knowledge["messages"]["missing_dimensions"].format(
                fields=", ".join(missing)
            ),
        )
    latest = rows[0]
    if (
        len(
            {
                str(row["values"])
                for row in rows
                if row["effective_date"] == latest["effective_date"]
            }
        )
        > 1
    ):
        return envelope(
            "NEEDS_MORE_INFO", {"missing": cfg.products["products"][product]["dims"]}
        )
    return envelope(
        "OK",
        {
            "rate": latest["values"],
            "effective_date": latest["effective_date"],
            "source": latest["source"],
            "row": latest["raw"],
        },
    )
