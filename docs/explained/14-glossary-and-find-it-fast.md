# Glossary and find it fast

## 1. What this part does

Use the glossary when a technical word interrupts your reading. Use the lookup tables when you know the thing you want but not its owning file or symbol. Definitions describe this code's usage, including terms that have a broader meaning outside the project.

## 2. Where it lives

The authoritative setting declarations are in app/config.py, not the local environment file. External dependency declarations live in pyproject.toml and frontend/package.json. Models are called in app/services/router.py, app/services/llm.py and app/services/embeddings.py. Collection accessors live in app/db.py and app/bank/db.py, and the vector definition is also checked into atlas/chunks_vector_index.json.

No local environment secret was read to build these tables. Required values are identified by name only. Effective overrides, current hosted-model limits and actual database permissions remain not confirmed from code.

## 3. How it works, step by step

1. Look up the unfamiliar term below before following implementation details.
2. If the question names a setting, find its Settings field and default. Then find the caller; a declared setting is not proof every route uses it.
3. If the question names a library, find both its package declaration and the imported name. Package names and import names differ, for example python-docx imports docx and google-genai imports google.genai.
4. If the question names a collection/index, separate the app and bank database. A normal Mongo index and a Vector Search index are different structures.
5. If the question names a live model, inspect the setting and call site. Treat a code default as a default, not confirmation of the owner's running override or current provider availability.

### Glossary

| Term | Plain definition |
| --- | --- |
| Abort controller | An AbortController signals cancellation to pending fetch or stream work. |
| API | An application programming interface is the request/response contract through which one program uses another. |
| Argon2 | Argon2 is the password-hashing algorithm provided through the selected pwdlib implementation. |
| ASGI | The async server gateway interface is the contract used by Uvicorn to call this FastAPI application. |
| Assertion | An assertion describes a required condition that fails a test when false. |
| AST | An abstract syntax tree represents declarations and statements as structured data without running the source. |
| Async context manager | An async context manager provides awaited entry/exit logic around an async with block. |
| Async generator | An async generator yields a sequence over time and can await work between values. |
| Async/await | Python async/await lets awaited work pause while the event loop schedules other ready tasks. |
| Atomic section | An atomic section is intended to stay together during chunking unless it exceeds a hard safety maximum. |
| Atomic update | An atomic update evaluates and changes one document without interleaving those parts with another update. |
| Audit event | An audit event records an operation and outcome without copying the underlying message or financial content. |
| Authentication | Authentication verifies the login identity presenting a request. |
| Authorization | Authorization checks whether that identity may use a specific operation and data. |
| Background task | A background task runs outside the immediate response-producing path and is not automatically durable. |
| Backoff | Backoff increases waiting time between repeated failed attempts. |
| Bearer token | A bearer token authorizes its presenter and must be protected from disclosure. |
| Breadcrumb | A breadcrumb is a heading path added to embedding input to preserve document context. |
| Buffer | A buffer stores partial data until parsing or validation can safely release it. |
| Build | A build checks/compiles source and bundles browser-ready output assets. |
| Bundle | A bundle combines application code and dependencies into generated browser assets. |
| CA bundle | A certificate-authority bundle supplies trusted certificates used to verify a server connection. |
| Cache | A cache reuses a previously computed value instead of constructing it for each call. |
| CDN | A content delivery network serves remote assets; the frontend does not require CDN fonts/scripts. |
| Chunk | A chunk is a bounded extracted text unit stored with metadata and a vector for retrieval. |
| Claim | A claim is a named token value, such as sub, iat or exp. |
| Class method | A class method receives its class as the first implicit argument rather than a particular instance. |
| Closure | A closure is a nested function that retains access to values from an enclosing scope. |
| Collection | A collection is MongoDB’s named group of document records. |
| Component | A component is a function that returns part of the user interface. |
| Context window | A context window is the model’s maximum supported amount of input/output context for one request. |
| Coroutine | A coroutine is an async function execution that can suspend at await points. |
| CORS | Cross-Origin Resource Sharing is the browser mechanism that checks allowed cross-origin response access. |
| Cosine similarity | Cosine similarity compares vector directions to rank approximate semantic similarity. |
| CRLF | CRLF is a carriage-return/line-feed pair that can terminate an SSE line. |
| Dataclass | A dataclass generates common data-container methods from declared class fields. |
| Decorator | A decorator transforms or registers a Python function/class when its declaration executes. |
| Dedupe | Deduplication identifies repeated content so the same scoped upload need not be processed twice. |
| Delimiter | A delimiter marks a data boundary, such as the opening/closing tag around a passage. |
| Dependency injection | Dependency injection means a framework supplies declared inputs such as a verified current user. |
| Dimension | A dimension is one coordinate position; this project requests 768 positions per embedding. |
| Discriminated union | A discriminated union selects a data shape using a literal field such as account-card type. |
| Document | A Mongo document is a structured record; an uploaded source document is a different use of the same word. |
| DOM | The document object model is the browser’s tree of page elements. |
| Effect | An effect synchronizes rendering with outside work such as a request or timer. |
| Embedding | An embedding represents text as numeric coordinates for approximate similarity matching. |
| Endpoint | An endpoint is a specific HTTP method and path handled by application code. |
| Enum | An enum-like literal choice permits only specified values, such as savings, current or credit_card. |
| Event loop | An event loop schedules asynchronous tasks and resumes them when their awaited operations can continue. |
| Eventual consistency | Eventual consistency means storage and search visibility can reflect an update at different times. |
| Fixture | A pytest fixture supplies reusable controlled setup to tests by name. |
| GFM | GitHub-flavored Markdown extends Markdown with features such as tables. |
| Grounding | Grounding asks the model to base factual claims on supplied evidence. |
| Handler | A handler is a plain function called to perform an approved operation. |
| Hash | A hash maps input bytes to a digest; password hashing additionally aims to make guessing costly. |
| Header | A header is request/response metadata, such as Authorization or Retry-After. |
| Heuristic | A heuristic is a practical approximate rule, such as phrase matching, rather than proof of semantic correctness. |
| HMAC | A hash-based message authentication code checks message integrity using a secret key. |
| Hook | A hook connects a React component to state, effects or another React-managed capability. |
| HS256 | HS256 is JWT signing with HMAC-SHA256 using a shared secret. |
| HTTP | Hypertext Transfer Protocol carries request methods, paths, headers and bodies with corresponding responses. |
| HTTP status | An HTTP status is the numeric response result, such as 200, 401, 404 or 429. |
| Idempotent | An idempotent action can be safely repeated with the same intended persistent effect. |
| Index | An index is a database structure supporting a particular lookup or uniqueness rule. |
| Integration test | An integration test connects multiple application parts through a shared request or function boundary. |
| Interface | An interface declares an expected TypeScript object shape without checking live JSON by itself. |
| JSON | JavaScript Object Notation represents structured objects, lists, strings, numbers and literal values as text. |
| JSONL | JSON Lines stores one independent JSON object on each text line. |
| JSX | JSX is the markup-like expression syntax used inside React code. |
| JWT | A JSON Web Token is a signed claims object used here as a bearer credential. |
| Latency | Latency is elapsed time measured for a precisely defined operation. |
| Lifespan | Lifespan is the application startup/serving/shutdown context used to own clients and cleanup work. |
| LLM | A large language model produces text or structured call suggestions from supplied context. |
| Lock | A lock allows one protected operation at a time within its coordination boundary. |
| Markdown | Markdown is plain text with formatting conventions that the frontend converts into rendered elements. |
| Mermaid | Mermaid renders text descriptions into the diagrams used in these documents; it is not part of the chat UI. |
| Minor units | Minor units store money as integers; this INR formatter uses one hundred minor units per rupee. |
| Mock | A mock replaces a real dependency with a controlled test implementation. |
| Monkeypatch | A monkeypatch temporarily replaces attributes or environment values in a test. |
| Multipart form | A multipart form encodes separately named body parts, including uploaded files. |
| Native tool calling | Native tool calling lets a model return a named function and structured arguments for server validation. |
| Normalization | Vector normalization divides by vector length; text normalization instead replaces selected display characters. |
| ObjectId | ObjectId is MongoDB’s identifier type used internally for uploaded document records here. |
| OCR | Optical character recognition converts text-shaped image content into machine-readable text. |
| Origin | An origin combines a URL scheme, hostname and port. |
| Parameterization | Parameterization runs a shared test body against several declared input cases. |
| Pre-filter | A pre-filter removes ineligible records before vector ranking. |
| Projection | A projection chooses which record fields a database response includes. |
| Prompt | A prompt is the instructions and input data supplied to a model request. |
| Prompt injection | Prompt injection places hostile instructions in data in an attempt to change model behavior. |
| Property | A property presents a computed method result as attribute access. |
| Proxy | A proxy forwards requests to another server; the Vite dev proxy forwards API paths to port 8000. |
| Pydantic | Pydantic validates structured fields and supplies schemas and serialization for declared Python models. |
| Query | A query is a search text or database instruction used to select records. |
| Quota | A quota is an external account’s permitted request, token, storage or other resource allowance. |
| Race condition | A race condition occurs when the order of concurrent operations changes their outcome. |
| RAG | Retrieval-augmented generation supplies retrieved evidence before asking a model to write an answer. |
| Rate limit | A rate limit bounds starts in an interval; the local chat limit uses a rolling sixty-second window. |
| React | React builds interfaces from components whose rendered output follows state. |
| Ref | A ref holds a mutable value or page element without causing a render on each change. |
| Registry | A registry maps approved names to tool descriptions, argument models and handlers. |
| Retention | Retention controls how long inactive private conversations and their files remain stored. |
| Retrieval | Retrieval selects stored evidence that may answer the current question. |
| Retry-After | Retry-After is an HTTP header indicating when a client might retry a throttled operation. |
| Route | A route registers which endpoint function handles a method/path combination. |
| Schema | A schema describes permitted data fields, types, defaults and constraints. |
| Scope | Scope marks shared evidence versus a file private to an owner and chat. |
| Semaphore | A semaphore limits simultaneous tasks holding a shared capacity slot. |
| Serialization | Serialization converts structured data into text or bytes, such as JSON. |
| SHA-256 | SHA-256 is the digest algorithm used to identify duplicate uploaded bytes within a permitted scope. |
| SSE | Server-sent events are named text events inside one long-lived HTTP response. |
| State | State is component data whose update can trigger a render. |
| Static method | A static method receives no automatic instance/class binding when accessed through the class. |
| Stream | A stream delivers values or bytes incrementally rather than requiring the whole result first. |
| Strict types | Strict types reject certain implicit conversions, such as accepting a string where an integer is required. |
| SVG | Scalable Vector Graphics describes local icon shapes with paths rather than downloaded bitmap images. |
| System prompt | A system prompt is the application instruction message placed ahead of conversational input. |
| Task | A task is a scheduled coroutine managed by an event loop. |
| Temperature | Temperature is a requested sampling-variability setting, not a factual-accuracy guarantee. |
| Terminal event | A terminal event marks that an application stream completed or failed; here those events are done and error. |
| Threadpool | A threadpool runs synchronous work in worker threads; password verification uses one to avoid blocking the event loop. |
| TLS | Transport Layer Security encrypts network connections and supports certificate verification. |
| Token | A token is a model-specific unit of text; this app estimates token counts as characters divided by four. |
| Tool context | Tool context is the verified server data supplied to a handler separately from model arguments. |
| Transaction | A database transaction groups operations under an all-or-nothing contract; this app does not use one for chat pairs/cascades. |
| TTL | Time to live is an expiry duration; this project performs explicit periodic cleanup instead of a TTL cascade. |
| TypeScript | TypeScript adds compile-time types to JavaScript; its interfaces disappear from runtime output. |
| Unit test | A unit test isolates a small behavior under controlled inputs. |
| Upsert | An upsert updates a matching row or inserts one when no match exists. |
| URL-encoded form | A URL-encoded form represents named form values using URL-style escaping; login uses it. |
| UTF-8 | UTF-8 encodes text characters as one or more bytes, which can split across network reads. |
| UUID | A universally unique identifier is the string identifier format used for users and sessions in this app. |
| Validation | Validation checks supplied values against required fields, strict types and permitted bounds. |
| Vector | A vector is an ordered list of numeric coordinates. |
| Vector index | A vector index supports similarity search over stored embedding coordinates and declared filter fields. |
| XSS | Cross-site scripting executes malicious page script, which can read script-accessible tokens. |

## 4. Key pieces

### Every backend setting

These rows are extracted from Settings declarations. Environment names are the uppercase form, such as USER_UPLOAD_MAX_MB for user_upload_max_mb. Default string values below are source defaults, not values copied from local configuration. Validators apply only where declared; the table does not invent a provider quota.

| Settings field | Code default | What to find / why it exists |
| --- | --- | --- |
| jwt_secret | Required; actual value never read | Signs and verifies access tokens; required and validated to at least 32 characters. Never show its real value. |
| access_token_minutes | 30 | Bounds token lifetime; browser logout alone does not revoke an issued token. |
| cors_origins | http://127.0.0.1:8000,http://localhost:8000 | Allows explicit browser origins; the validator rejects wildcards and allowed_origins splits the comma list. |
| mongodb_uri | Required; actual value never read | Selects app storage connectivity; actual credentials, cluster and database permissions are local configuration. |
| mongodb_db_name | rag_db | Separates application collections by database name rather than mixing them with bank records. |
| bank_db_name | bank_db | Names the database containing fictional customers, accounts, transactions and cards. |
| bank_mongodb_uri | Empty string | Allows a separate read-only runtime bank credential; an empty string falls back to the app URI. |
| router_history_messages | 4 | Restricts how many already-budgeted history messages the planner sees, reducing planner request size. |
| router_message_chars | 300 | Truncates each planner history message; it does not truncate the current request message. |
| groq_api_key | Required; actual value never read | Authenticates hosted router/answer calls; the code uses one cached client and disables SDK automatic retries. |
| groq_model | openai/gpt-oss-120b | Names the model used to phrase final grounded answers; effective local overrides were not inspected. |
| groq_rewrite_model | openai/gpt-oss-20b | Names the small native-tool router model; it can rewrite search queries from short history. |
| gemini_api_key | Required; actual value never read | Authenticates embedding calls; it is not a browser setting or answer-model credential. |
| embed_model | gemini-embedding-001 | Selects the text embedding model for documents and queries; stored vectors must remain compatible. |
| embed_dim | 768 | Requests embedding dimensions and configures new vector-index definitions; the checked-in Atlas definition uses 768. |
| chunk_size | 800 | Controls approximate prose character length before embedding; it is not a token count. |
| chunk_overlap | 120 | Repeats prose boundary context; it increases stored text and embedding work in exchange for continuity. |
| top_k | 5 | Caps returned/fitted passages; candidate breadth separately uses max(100, top_k multiplied by 20). |
| history_max_messages | 12 | Caps selected model-history message count; it counts messages rather than complete conversation pairs. |
| history_token_budget | 1500 | Caps estimated history content tokens before final prompt fitting and serialization overhead. |
| old_answer_trim_chars | 600 | Trims each selected assistant history entry after citation stripping, preserving a bounded recent suffix. |
| max_message_chars | 2000 | Rejects overly long chat questions; the frontend public limit should match it. |
| max_passage_chars | 5000 | Caps total complete passage text characters by dropping low-ranked passages, not slicing their values. |
| answer_max_tokens | 700 | Limits generated answer length; it is separate from estimated input size. |
| prompt_token_ceiling | 5000 | Caps estimated serialized input at 85 percent of its value, reserving an estimation margin. |
| llm_max_concurrency | 2 | Limits concurrent Groq calls/streams within an event loop; it does not control embeddings or other workers. |
| chat_rate_per_minute | 6 | Caps accepted chat starts per user across that user’s chats using a local rolling window. |
| max_upload_mb | 20 | Legacy/general limit retained in Settings; the current user attachment route uses user_upload_max_mb instead. |
| retention_days | 30 | Controls inactivity/file expiry; zero is permitted and enables immediate-old-data cleanup tests. |
| attachment_wait_seconds | 60 | Bounds server waiting for requested processing attachments; a client can instead poll before sending. |
| user_upload_max_mb | 5 | Caps bytes read for one private attachment; the default is five binary-megabyte units. |
| user_max_chunks_per_doc | 150 | Caps stored private file chunks and associated embedding cost; warnings report truncation. |
| user_max_table_rows | 500 | Caps searchable detailed rows per private table, with format-specific warnings and summary behavior. |
| table_chunk_chars | 1800 | Targets whole-row table group size while repeating table names and headers. |
| max_table_rows | 5000 | Default/shared searchable table-row cap; private uploads override it with user_max_table_rows. |
| max_chunks_per_doc | 1500 | Default/shared chunk cap; private uploads override it with user_max_chunks_per_doc. |
| answer_temperature | 0.2 | Controls requested answer sampling variability; a low value does not prove factual accuracy. |
| min_doc_score | 0.6 | Filters document tool hits before generation; it is not a calibrated confidence probability. |
| vector_index_name | chunks_vector_index | Names the Atlas Vector Search index used for creation, status checks and queries. |

### Public frontend build settings

| Public name | Default | File and symbol | Purpose |
| --- | --- | --- | --- |
| VITE_MAX_UPLOAD_MB | 5 | frontend/src/limits.ts — MAX_UPLOAD_MB | Match the private attachment route’s USER_UPLOAD_MAX_MB; this is public and never a credential. |
| VITE_MAX_MESSAGE_CHARS | 2000 | frontend/src/limits.ts — MAX_MESSAGE_CHARS | Match the server message cap before building or starting development. |

### Libraries: where to find the import or call

Every direct declared dependency is listed, plus the directly imported Pydantic dependency supplied through the Python graph. Standard-library modules are explained by their named symbols in the code reference; they do not require separate package installation.

| Library/package | File | Imported/called name |
| --- | --- | --- |
| fastapi | app/main.py | FastAPI; APIRouter; Depends |
| uvicorn[standard] | pyproject.toml | uvicorn app.main:app |
| pydantic-settings | app/config.py | BaseSettings; SettingsConfigDict |
| pydantic | app/schemas.py | BaseModel; Field; field_validator |
| python-dotenv | app/config.py | BaseSettings dotenv integration |
| pymongo | app/db.py | AsyncMongoClient; SearchIndexModel |
| certifi | app/db.py | certifi.where |
| groq | app/services/llm.py | AsyncGroq |
| google-genai | app/services/embeddings.py | genai.Client; types.EmbedContentConfig |
| langchain-text-splitters | app/services/chunking.py | RecursiveCharacterTextSplitter |
| pdfplumber | app/services/loaders.py | pdfplumber.open |
| pypdf | app/services/loaders.py | PdfReader |
| python-docx | app/services/loaders.py | Document; Table; Paragraph |
| python-pptx | app/services/loaders.py | Presentation; MSO_SHAPE_TYPE; PP_PLACEHOLDER |
| openpyxl | app/services/loaders.py | openpyxl.load_workbook |
| pwdlib[argon2] | app/auth/security.py | PasswordHash.recommended |
| pyjwt | app/auth/security.py | jwt.encode; jwt.decode |
| python-multipart | app/routers/attachments.py | File; UploadFile form parsing |
| sse-starlette | app/routers/chat.py | EventSourceResponse |
| httpx | scripts/run_eval.py | httpx.Client; httpx.MockTransport |
| pytest | tests/conftest.py | pytest.fixture; pytest.mark.parametrize |
| pytest-asyncio | pyproject.toml | development dependency |
| react | frontend/src/App.tsx | useState; useEffect; useRef; useSyncExternalStore |
| react-dom | frontend/src/main.tsx | createRoot |
| react-markdown | frontend/src/Answer.tsx | ReactMarkdown |
| remark-gfm | frontend/src/Answer.tsx | remarkGfm |
| unified | frontend/src/Answer.tsx | Plugin type |
| typescript | frontend/tsconfig.json | tsc -b |
| vite | frontend/vite.config.ts | defineConfig; vite commands |
| @vitejs/plugin-react | frontend/vite.config.ts | react |
| vitest | frontend/src/api.test.ts | describe; it; expect; vi |
| jsdom | frontend/vite.config.ts | test.environment |
| @testing-library/react | frontend/src/App.test.tsx | render; screen; waitFor |
| @testing-library/user-event | frontend/src/App.test.tsx | userEvent.setup; userEvent.click |
| @testing-library/jest-dom | frontend/src/test-setup.ts | jest-dom/vitest matchers |
| @types/react; @types/react-dom | frontend/src/main.tsx | StrictMode; createRoot type declarations |
| @types/mdast | frontend/src/Answer.tsx | Root; RootContent |

### Standard-library and dependency namespace lookup

| Standard-library module | Example file/import | Purpose |
| --- | --- | --- |
| __future__ | app/services/loaders.py — from __future__ import annotations | Defers annotation evaluation in the loader module. |
| argparse | scripts/run_eval.py — import argparse | Parses evaluator command-line options. |
| ast | tests/test_eval_runner.py — import ast | Parses source in boundary/import tests without executing it. |
| asyncio | app/auth/throttle.py — import asyncio | Schedules tasks, waits, locks, semaphores and async test coroutines. |
| codecs | app/services/loaders.py — import codecs | Recognizes UTF byte-order marks while decoding source files. |
| collections | app/auth/throttle.py — from collections import deque | Supplies deques, counters, default dictionaries and async collection interfaces. |
| concurrent | tests/test_phase5.py — from concurrent.futures import ThreadPoolExecutor | Supplies worker threads for limiter-concurrency tests. |
| contextlib | app/auth/throttle.py — from contextlib import asynccontextmanager | Provides managed lifespan/slot contexts, generator closing and cancellation suppression. |
| copy | tests/test_phase2.py — import copy | Copies fake database rows so tests do not mutate shared fixture inputs accidentally. |
| csv | app/services/loaders.py — import csv | Reads delimited source tables and the fee-fact test. |
| dataclasses | app/services/loaders.py — from dataclasses import dataclass, field | Builds internal section/tool/prompt data containers and inspects their fields. |
| datetime | app/auth/security.py — from datetime import datetime, timedelta, timezone | Creates UTC timestamps, expiry arithmetic and source cell date strings. |
| email | app/services/groq_runtime.py — from email.utils import parsedate_to_datetime | Parses HTTP date-form retry timing. |
| functools | app/config.py — from functools import lru_cache | Caches settings and provider clients. |
| hashlib | app/services/document_store.py — import hashlib | Computes scoped upload SHA-256 digests. |
| importlib | tests/test_phase2.py — import importlib.util | Loads real database source separately from a test stub. |
| inspect | tests/test_phase4b.py — import inspect | Checks handler signatures in boundary tests. |
| io | app/services/loaders.py — import io | Wraps file bytes/text for parser libraries. |
| json | app/routers/chat.py — import json | Serializes prompts/events and parses model arguments/evaluation files. |
| logging | app/routers/chat.py — import logging | Writes safe operational diagnostics by exception type. |
| math | app/services/embeddings.py — import math | Computes vector lengths, finite checks and rounded retry timing. |
| mimetypes | scripts/seed_kb.py — import mimetypes | Guesses seeded source content types. |
| os | tests/conftest.py — import os | Supplies controlled test environment values without opening dotenv secrets. |
| pathlib | app/main.py — from pathlib import Path | Resolves source/build paths and source filename extensions. |
| random | tests/test_citations.py — import os, random, sys | Generates repeatable stream-split test inputs. |
| re | app/services/citations.py — import re | Recognizes citations, formats, amount strings and evaluator text patterns. |
| sys | tests/conftest.py — import sys | Manages test import paths and application-module substitutes. |
| threading | app/services/rate_limits.py — from threading import Lock | Protects local rate-limit reservations across threads. |
| time | app/auth/throttle.py — import time | Measures monotonic windows and evaluator timings. |
| traceback | app/routers/chat.py — import traceback | Extracts safe traceback locations without logging exception messages. |
| types | tests/test_chat.py — import types | Constructs fake modules and simple provider/database response objects. |
| typing | app/auth/router.py — from typing import Annotated, Literal | Declares literals, annotated dependencies and callable/result types. |
| urllib | scripts/run_eval.py — from urllib.parse import quote | Safely encodes evaluator session identifiers in HTTP paths. |
| uuid | app/services/memory.py — from uuid import uuid4 | Constructs session UUIDs and validates stable demo-user IDs. |

| Imported dependency namespace | Owning package | Example file and name | Purpose |
| --- | --- | --- | --- |
| bson | pymongo | app/services/attachments.py — ObjectId; InvalidId | Converts/validates internal Mongo document identifiers; it is not an independently declared bson dependency. |
| starlette | FastAPI dependency | app/auth/security.py — run_in_threadpool | Runs synchronous password verification outside the async request event loop; other Starlette behavior underlies FastAPI. |

### Models, collections and indexes

| I want to find | File and symbol | What it represents |
| --- | --- | --- |
| Router model default openai/gpt-oss-20b | app/config.py — Settings.groq_rewrite_model; app/services/router.py — plan_turn | The native-tool selection request, with default 250 output tokens at the call site. |
| Answer model default openai/gpt-oss-120b | app/config.py — Settings.groq_model; app/services/llm.py — stream_answer | The grounded text-generation request, with output capped by answer_max_tokens. |
| Embedding model default gemini-embedding-001 | app/config.py — Settings.embed_model; app/services/embeddings.py — _embed_batch | The document/query embedding model with configured output dimension. |
| App database rag_db | app/config.py — Settings.mongodb_db_name; app/db.py — get_db | Application users, chats, files, chunks and audits. |
| Bank database bank_db | app/config.py — Settings.bank_db_name; app/bank/db.py — get_db | Fictional customer, account, transaction and card records. |
| users | app/db.py — users_col | User identity, password hash, customer link and account status. |
| sessions | app/db.py — sessions_col | Owned chat metadata, activity and attachment reservations. |
| messages | app/db.py — messages_col | Owned user/assistant text, document source metadata and attachment references. |
| documents | app/db.py — documents_col | Scoped source-file metadata, file hash, processing status and expiry. |
| chunks | app/db.py — chunks_col | Located scoped extracted text plus vectors and document linkage. |
| audit_log | app/db.py — audit_log_col | Content-free event/outcome records and cleanup count summaries. |
| customers | app/bank/db.py — get_db; scripts/seed_bank.py — build_seed | Fictional customer rows; current read services chiefly use customer_id on accounts/cards. |
| accounts | app/bank/service.py — get_balances; get_recent_transactions | Owned account type, masked output input and integer stored balance. |
| transactions | app/bank/service.py — get_recent_transactions | Rows linked to account_id; allowed IDs are resolved from owned accounts first. |
| cards | app/bank/service.py — get_card_status | Owned masked status/expiry records with last4 rather than complete card numbers. |
| chunks_vector_index | atlas/chunks_vector_index.json; app/db.py — vector_index_definition | Vector Search definition: embedding, 768 default dimensions, cosine and four filter fields. |
| user_document_dedupe | app/db.py — ensure_indexes | Named partial unique index on scope/owner/session/file_hash for user documents. |
| Ordinary app indexes | app/db.py — ensure_indexes | Unique username, owned activity/history lookups, chunk-document keys and scope lookups; most names are driver-generated. |
| Ordinary bank indexes | app/bank/db.py — ensure_indexes | Unique customer/account/transaction/card keys plus ownership and recent-transaction lookup fields. |

### Find behavior quickly

| I want to find | File and symbol |
| --- | --- |
| Exact refusal/greeting texts | app/services/llm.py — FIXED_REPLIES and the individual message constants. |
| Why an answer cannot invent currency | app/services/output_guard.py — valid_account_amounts; app/routers/chat.py — run_chat. |
| What the model can request | app/tools/registry.py — schemas; app/tools/account_tools.py — TransactionArguments; app/tools/document_tools.py — SearchArguments. |
| Which source text a model sees | app/services/prompting.py — format_sources; render_messages; prepare_prompt. |
| Why the browser cannot restore a source/card | app/routers/chat.py — public/stored source construction; app/services/memory.py — save_message; frontend/src/App.tsx — Workspace. |
| Where token storage is cleared | frontend/src/api.ts — setToken; request; frontend/src/App.tsx — App. |
| Where provider waits are bounded | app/services/groq_runtime.py — retry_seconds; call_groq; stream_groq. |
| Where ingestion retries happen | app/services/embeddings.py — _embed_batch; app/services/document_store.py — register_document. |
| How to run live evaluation | scripts/run_eval.py — main; HTTPOnlyRunner; eval/cases.jsonl. |
| How production UI is served | app/main.py — frontend_dist; frontend/vite.config.ts — base setting. |

## 5. Decisions

| Decision | Because | Alternative not used | Trade-off |
| --- | --- | --- | --- |
| Use setting symbols as the lookup key | The code field is stable across case-insensitive environment names. | Treat an example environment file as proof of effective configuration. | The owner still has to inspect their own overrides privately. |
| Keep package and import names together | Installation names often differ from Python import/module names. | Search only for the package spelling in application code. | Some libraries are used indirectly rather than through a direct call. |
| Separate data indexes | Ordinary uniqueness/lookups and vector ranking enforce different contracts. | Assume one Mongo index covers every query. | Deployment review needs both index families. |
| Keep glossary definitions local | A fresher should not have to infer a term from another chapter. | Use unexplained acronyms throughout the explanations. | Definitions recur where a document needs to stand on its own. |

## 6. What can go wrong

A familiar name can refer to different things: token can mean a bearer login credential, a model text unit or the token SSE event. Document can mean a source file or a Mongo record. Normalize can refer to vectors, display text or evaluator phrase text. Use the owning file and caller to disambiguate.

Differs from DESIGN.md: effective UI source inspection is excerpt-only and saved history lacks account cards/excerpts.

The code default cors_origins contains only port 8000 origins; the example and documented shell override add development origins through the same setting. The actual locally configured list was not read.

The backend declares max_upload_mb=20, but user attachments use user_upload_max_mb=5. The existing README's frontend override instruction names the former, which is misleading for this route. The explanation tables use the actual caller. Likewise, a model name being present in Settings does not confirm that provider access is currently available.

## 7. How to see it working

Use the table's file and symbol as a pair when searching. For example, find Settings.user_upload_max_mb, then search for user_upload_max_mb in app/routers/attachments.py to prove which route uses it. Find schemas in app/tools/registry.py and inspect its argument-model output alongside test_schemas_no_identity in tests/test_phase4a.py.

To inspect indexes without running a service, read atlas/chunks_vector_index.json and ensure_indexes. To inspect a live index, use your Atlas console privately; the current deployment state is not confirmed by a checked-in definition. Nothing in this glossary requires opening the local dotenv file, running a seed or contacting a provider.

## 8. Check yourself

1. **How do you distinguish the three uses of token?** Use the file/context: authentication credential, estimated model text unit, or SSE text event.

2. **Is a value in .env.example the running value?** No. It is an example; environment/file overrides and cached settings determine the running process.

3. **What setting controls private upload bytes?** Settings.user_upload_max_mb, exposed as USER_UPLOAD_MAX_MB, rather than the retained general max_upload_mb.

4. **Does a checked-in index prove Atlas is READY?** No. The deployed definition and state require an actual operator check.

5. **Why find both package and imported name?** They can differ, and some declared dependencies are used through another library rather than a direct import.
