# Code reference

## 1. What this part does

Use this reference to find an implementation by file and symbol name. It inventories source declarations rather than importing the application, because importing a live entry point can load settings or create resources. Private helpers are grouped and public APIs are listed separately from imported library names.
### Words used here

| Term | Plain meaning |
| --- | --- |
| Symbol | A symbol is a declared code name such as a function, class, constant or field. |
| AST | An abstract syntax tree is a structured representation of source declarations and statements. |
| Public | Public here means a nonunderscored Python declaration or a named frontend API/component; it is not a guarantee of a supported external HTTP endpoint. |
| Decorator | A decorator applies behavior or registration to a function or class declaration. |
| Dataclass | A dataclass generates common data-container methods from class fields. |
| Pydantic | Pydantic validates declared structured fields and supplies schema/serialization methods. |
| Closure | A closure is a nested function that uses values from its enclosing function. |
| Interface | A TypeScript interface describes a compile-time object shape and does not validate live JSON by itself. |
| Mermaid | A text format that draws the flow diagrams shown in these documents. |

## 2. Where it lives

The tables cover every maintained Python file in app and scripts and every file in frontend/src. Python tests are mapped in document 11; frontend test files are included here because they reside under frontend/src. Empty package markers and setup-only files are explicitly listed.

A scratch Python ast script outside the repository supplied Python declarations, methods, decorators and module values. A scratch TypeScript compiler-tree walk supplied frontend functions, types and interfaces. Class-field groups are included to make settings and response shapes easy to find, although fields are not functions. No app import or live provider call was used for the inventory.

## 3. How it works, step by step

1. Find the file under the layer described in document 02. Identical short names can occur in different modules: get_db in app/db.py is not get_db in app/bank/db.py, and normalize in the evaluator is not textfix.normalize.
2. Read the declaration kind and purpose. A model class describes data; an endpoint function handles requests; a module value can be mutable even when it appears near constants.
3. Inspect decorators before following a call. Route decorators register HTTP operations. field_validator attaches validation. classmethod binds a class rather than an instance. property exposes computed attribute access. lru_cache reuses previous results. asynccontextmanager provides async entry/exit behavior. dataclass generates container methods. staticmethod supplies a method without an instance/class binding.
4. Dotted names in helper groups show lexical containment, not necessarily an attribute accessible through an imported object. For example, a nested render closure is created inside a function and cannot be called through an ordinary module attribute chain.
5. Names beginning with an underscore are implementation helpers; their group records their existence without promoting them into an external API. Magic constructors are grouped with helpers where applicable. Imported symbols such as BaseModel, Depends or Field belong to libraries and are indexed in document 14 rather than repeated as local class definitions.


```mermaid
flowchart LR
 Q[Question about a name] --> F[Find owning file]
 F --> K[Check declaration kind and decorators]
 K --> B[Read body and caller]
 B --> T[Find test evidence]
```

A symbol name only gains meaning when read with its owning file, declaration kind and caller.

## 4. Key pieces

### `app/__init__.py`

Marks this directory as a Python package; contains no request handler or data pipeline.

No functions, classes or constants are defined here.

### `app/auth/__init__.py`

Marks this directory as a Python package; contains no request handler or data pipeline.

No functions, classes or constants are defined here.

### `app/auth/router.py`

Accepts form login and returns the verified current-user summary.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `router` | module value | None | Registers this module’s HTTP routes or stores the imported router module, as shown by its declaration. |
| `LOGIN_ERROR` | constant | None | Keeps unknown-user, wrong-password and locked-user login denial text identical. |
| `TokenResponse` | class | None | Validates the login access token and literal bearer token type. |
| `UserResponse` | class | None | Restricts the current-user HTTP summary to user ID and username. |
| `login` | async function | @router.post('/login', response_model=TokenResponse) | Verifies form credentials under the login throttle and issues a signed token. |
| `me` | async function | @router.get('/me', response_model=UserResponse) | Returns the authenticated user summary supplied by the verified dependency. |
| TokenResponse fields | fields/configuration | Not decorators | access_token, token_type. These declarations define the validated or stored shape; see the class above for its role. |
| UserResponse fields | fields/configuration | Not decorators | id, username. These declarations define the validated or stored shape; see the class above for its role. |

### `app/auth/security.py`

Hashes passwords, issues/checks JWTs and loads active user identity.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `password_hash` | module value | None | Holds the recommended pwdlib password-hashing implementation. |
| `oauth2_scheme` | module value | None | Extracts the bearer token using the documented auth/login token URL. |
| `CurrentUser` | class | None | Carries the server-loaded user ID, username and bank customer link. |
| `_DUMMY_HASH` | constant | None | Holds the precomputed unknown-user password verification hash; it is not an accepted login credential. |
| `dummy_hash` | function | None | Returns the precomputed verification hash used for unknown usernames. |
| `hash_password` | function | None | Hashes a plaintext demo password before storage. |
| `verify_password` | async function | None | Checks a hash in a threadpool and returns false on verification errors. |
| `create_access_token` | function | None | Signs subject, issue-time and expiry claims using HS256. |
| `unauthorized` | function | None | Builds the generic 401 response with the bearer authentication challenge. |
| `get_current_user` | async function | None | Verifies token claims and reloads an active user record. |
| CurrentUser fields | fields/configuration | Not decorators | user_id, username, customer_id. These declarations define the validated or stored shape; see the class above for its role. |

### `app/auth/throttle.py`

Serializes login checks and tracks username failure windows in process memory.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `LoginThrottle` | class | None | Tracks username failure windows and serializes login attempts with an async lock. |
| `LoginThrottle.prune` | function | None | Removes expired login failures and expired block records from memory. |
| `LoginThrottle.attempt` | async function | @asynccontextmanager | Runs a username allowance check inside an async lock-held context. |
| `LoginThrottle.fail` | function | None | Adds a failed login time and blocks a username after five failures. |
| `LoginThrottle.succeed` | function | None | Clears the username’s failure and block state after successful verification. |
| `login_throttle` | module value | None | Stores the process-local login limiter instance used by the auth route. |
| Private/local helpers | helper group | Included within their lexical scope | LoginThrottle.__init__. They support this file’s public operations; underscored names are not public entry points. |

### `app/bank/__init__.py`

Marks this directory as a Python package; contains no request handler or data pipeline.

No functions, classes or constants are defined here.

### `app/bank/db.py`

Lazily creates a separate bank client and defines bank indexes for seeding.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `get_db` | function | None | Returns the configured database handle; app and bank implementations have different client lifecycles. |
| `close_bank` | async function | None | Closes and clears the lazy bank Mongo client when present. |
| `ensure_indexes` | async function | None | Creates the collections/indexes defined in this module without rebuilding existing search definitions. |
| Private/local helpers | helper group | Included within their lexical scope | _client (decorated with @lru_cache). They support this file’s public operations; underscored names are not public entry points. |

### `app/bank/service.py`

Reads owned balances, recent transactions and card status and formats INR without floats.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `Balance` | class | None | Validates one owned balance result with masked number, integer money and display string. |
| `format_money` | function | None | Formats supported INR integer minor units with sign, commas and two decimal digits. |
| `get_balances` | async function | None | Reads only the verified customer’s accounts and returns masked formatted balances. |
| `AccountType` | module value | None | Permits savings, current or credit_card as an account category rather than an identity. |
| `RecentTransaction` | class | None | Validates one masked owned transaction with integer amount, formatted display string and direction. |
| `CardStatus` | class | None | Validates owned card kind, last four digits, status and expiry fields. |
| `get_recent_transactions` | async function | None | Resolves owned accounts, then returns newest matching transactions with a total limit. |
| `get_card_status` | async function | None | Reads only the verified customer’s card status records and formats expiry. |
| Balance fields | fields/configuration | Not decorators | model_config, type, nickname, masked_number, currency, balance_minor, balance. These declarations define the validated or stored shape; see the class above for its role. |
| RecentTransaction fields | fields/configuration | Not decorators | model_config, type, nickname, masked_number, posted_at, description, direction, currency, amount_minor, amount. These declarations define the validated or stored shape; see the class above for its role. |
| CardStatus fields | fields/configuration | Not decorators | model_config, kind, last4, status, expiry_month, expiry_year, expiry. These declarations define the validated or stored shape; see the class above for its role. |

### `app/config.py`

Declares typed configuration defaults and validators, and caches one Settings object.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `Settings` | class | None | Declares required service configuration, validated limits and code defaults. |
| `Settings.validate_jwt_secret` | function | @field_validator('jwt_secret'), @classmethod | Rejects a JWT signing secret shorter than 32 characters. |
| `Settings.validate_origins` | function | @field_validator('cors_origins'), @classmethod | Rejects wildcard CORS origin configuration. |
| `Settings.allowed_origins` | function | @property | Splits configured origins into the list consumed by CORS middleware. |
| `get_settings` | function | @lru_cache | Constructs and caches one validated configuration object. |
| Settings fields | fields/configuration | Not decorators | model_config, jwt_secret, access_token_minutes, cors_origins, mongodb_uri, mongodb_db_name, bank_db_name, bank_mongodb_uri, router_history_messages, router_message_chars, groq_api_key, groq_model, groq_rewrite_model, gemini_api_key, embed_model, embed_dim, chunk_size, chunk_overlap, top_k, history_max_messages, history_token_budget, old_answer_trim_chars, max_message_chars, max_passage_chars, answer_max_tokens, prompt_token_ceiling, llm_max_concurrency, chat_rate_per_minute, max_upload_mb, retention_days, attachment_wait_seconds, user_upload_max_mb, user_max_chunks_per_doc, user_max_table_rows, table_chunk_chars, max_table_rows, max_chunks_per_doc, answer_temperature, min_doc_score, vector_index_name. These declarations define the validated or stored shape; see the class above for its role. |

### `app/db.py`

Owns the app Mongo client, collection accessors and ordinary/vector index setup.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `connect_to_mongo` | async function | None | Creates the app Mongo client with a CA bundle and verifies it by ping. |
| `close_mongo` | async function | None | Closes and clears the app Mongo client. |
| `get_db` | function | None | Returns the configured database handle; app and bank implementations have different client lifecycles. |
| `documents_col` | function | None | Returns the app collection containing scoped document metadata and processing status. |
| `chunks_col` | function | None | Returns the app collection containing scoped extracted text and embedding vectors. |
| `messages_col` | function | None | Returns the app collection containing owner-labelled conversation messages. |
| `users_col` | function | None | Returns the app collection containing login hashes and customer links. |
| `sessions_col` | function | None | Returns the app collection containing owned chat metadata and attachment reservations. |
| `audit_log_col` | function | None | Returns the app collection used for content-free audit records. |
| `vector_index_definition` | function | None | Builds the configured embedding field plus document/scope/owner/session filter fields. |
| `ensure_indexes` | async function | None | Creates the collections/indexes defined in this module without rebuilding existing search definitions. |
| `vector_index_status` | async function | None | Returns the deployed search index status or MISSING when absent. |
| Private/local helpers | helper group | Included within their lexical scope | _client (decorated with @lru_cache). They support this file’s public operations; underscored names are not public entry points. |

### `app/main.py`

Creates FastAPI, registers routes and CORS, mounts a built frontend and owns startup/shutdown cleanup.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `lifespan` | async function | @asynccontextmanager | Connects app storage, establishes cleanup and closes process-owned tasks/clients. |
| `app` | module value | None | Holds the FastAPI ASGI application with middleware, routes and optional static mount. |
| `frontend_dist` | module value | None | Resolves the production frontend directory relative to the server source location. |
| `index` | async function | @app.get('/', include_in_schema=False) | Redirects the root request to Swagger at /docs. |
| `health` | async function | @app.get('/health', tags=['meta'], response_model=HealthResponse) | Reports application status and the current vector-index state. |
| Private/local helpers | helper group | Included within their lexical scope | _fix_binary, _default_openapi, _custom_openapi. They support this file’s public operations; underscored names are not public entry points. |

### `app/routers/__init__.py`

Marks this directory as a Python package; contains no request handler or data pipeline.

No functions, classes or constants are defined here.

### `app/routers/attachments.py`

Accepts limited multipart uploads, schedules scoped ingestion and lists owned attachment status.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `router` | module value | None | Registers this module’s HTTP routes or stores the imported router module, as shown by its declaration. |
| `upload_attachments` | async function | @router.post('', status_code=202, response_model=list[UploadResponse], response_model_exclude_none=True) | Validates owned multipart uploads and schedules scoped ingestion for accepted files. |
| `list_attachments` | async function | @router.get('', response_model=list[AttachmentResponse]) | Returns the current owner/session’s file statuses, warnings and expiry metadata. |

### `app/routers/chat.py`

Coordinates a complete chat turn and exposes session creation, listing, history and deletion.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `router` | module value | None | Registers this module’s HTTP routes or stores the imported router module, as shown by its declaration. |
| `log` | module value | None | Holds the module logger used for exception-type-only operational diagnostics. |
| `chat_windows` | module value | None | Stores rolling per-user chat timestamps in this process. |
| `CHAT_ERROR_MESSAGE` | constant | None | Defines the generic safe failure detail used for unexpected turn exceptions. |
| `run_chat` | async function | None | Coordinates history, readiness, planning, tools, filtered generation, saves and events. |
| `chat` | async function | @router.post('/chat', response_model=ChatResponse  /  ChatError) | Validates ownership/attachments, reserves rate capacity and returns SSE or aggregated JSON. |
| `new_session` | async function | @router.post('/sessions', response_model=SessionResponse, status_code=201) | Creates an empty session for the verified user. |
| `sessions` | async function | @router.get('/sessions', response_model=list[SessionResponse]) | Lists the verified user’s latest owned chats. |
| `history` | async function | @router.get('/sessions/{session_id}/history', response_model=list[MessageResponse]) | Returns the verified user’s full stored history for an owned session. |
| `delete_session` | async function | @router.delete('/sessions/{session_id}', response_model=SessionDeleted) | Deletes owned chat data and records a session_deleted audit event. |
| Private/local helpers | helper group | Included within their lexical scope | _doc_source. They support this file’s public operations; underscored names are not public entry points. |

### `app/schemas.py`

Defines HTTP request and response models, including chat text and attachment limits.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `ChatRequest` | class | None | Forbids unknown fields and validates message, required session, attachment count and stream flag. |
| `ChatRequest.message_limit` | function | @field_validator('message'), @classmethod | Rejects a chat message longer than the configured character limit. |
| `ChatResponse` | class | None | Declares the JSON answer, citation, note, plan, source and account-card response fields. |
| `ChatError` | class | None | Declares the nonstreaming safe error response shape. |
| `SessionResponse` | class | None | Declares a public session identifier, title and timestamps. |
| `SessionDeleted` | class | None | Declares the deleted session identifier and removed-message count. |
| `MessageResponse` | class | None | Declares stored message content, ownership, sources, attachment references and timestamp. |
| `HealthResponse` | class | None | Declares the health status and vector-index status fields. |
| `DocumentResponse` | class | None | Retains a general document metadata schema; no current global document route uses it. |
| `UploadResponse` | class | None | Declares each file’s acceptance/status, document ID, duplicate flag or error. |
| `AttachmentResponse` | class | None | Declares owned attachment status/listing fields without the stored raw error. |
| ChatRequest fields | fields/configuration | Not decorators | message, session_id, attachment_ids, stream, model_config. These declarations define the validated or stored shape; see the class above for its role. |
| ChatResponse fields | fields/configuration | Not decorators | session_id, answer, cited, plan, notes, sources, account_cards. These declarations define the validated or stored shape; see the class above for its role. |
| ChatError fields | fields/configuration | Not decorators | error. These declarations define the validated or stored shape; see the class above for its role. |
| SessionResponse fields | fields/configuration | Not decorators | session_id, title, created_at, updated_at. These declarations define the validated or stored shape; see the class above for its role. |
| SessionDeleted fields | fields/configuration | Not decorators | session_id, messages_deleted. These declarations define the validated or stored shape; see the class above for its role. |
| MessageResponse fields | fields/configuration | Not decorators | session_id, user_id, role, content, sources, attachments, created_at. These declarations define the validated or stored shape; see the class above for its role. |
| HealthResponse fields | fields/configuration | Not decorators | status, vector_index. These declarations define the validated or stored shape; see the class above for its role. |
| DocumentResponse fields | fields/configuration | Not decorators | id, filename, content_type, size_bytes, status, num_chunks, error, warnings, created_at. These declarations define the validated or stored shape; see the class above for its role. |
| UploadResponse fields | fields/configuration | Not decorators | filename, status, doc_id, duplicate, error. These declarations define the validated or stored shape; see the class above for its role. |
| AttachmentResponse fields | fields/configuration | Not decorators | id, filename, status, size_bytes, num_chunks, warnings, created_at, expires_at. These declarations define the validated or stored shape; see the class above for its role. |

### `app/services/__init__.py`

Marks this directory as a Python package; contains no request handler or data pipeline.

No functions, classes or constants are defined here.

### `app/services/attachments.py`

Resolves owned document IDs and yields bounded readiness-wait events.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `resolve_attachments` | async function | None | Validates deduplicated ObjectIds against the current owner and session. |
| `attachment_events` | async function | None | Yields bounded processing progress or safe failed/timeout events for requested files. |

### `app/services/audit.py`

Writes event names, identities, outcomes, durations and count summaries without message contents.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `audit_event` | async function | None | Inserts operational event metadata without message text, money or tool arguments. |

### `app/services/chunking.py`

Splits prose with overlap while preserving pre-sized tables and short slides where possible.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `ATOMIC_HARD_MAX` | constant | None | Caps an otherwise atomic section at 7000 characters before forced splitting. |
| `chunk_sections` | function | None | Creates prose/atomic chunks with display text, embedding text and locations. |

### `app/services/citations.py`

Normalizes supported citation styles, removes invalid references and strips old history citations.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `OPEN` | constant | None | Lists bracket characters the citation parser treats as possible citation starts. |
| `CLOSE` | constant | None | Maps supported opening citation brackets to their closing character. |
| `MAX_CITATION_LEN` | constant | None | Bounds buffered potential citation content to 40 characters. |
| `_WORDS` | constant | None | Recognizes label words that may introduce a model-generated citation number. |
| `_LINES` | constant | None | Recognizes optional browsing-style line-number suffixes inside a citation. |
| `_LIST` | constant | None | Recognizes comma/semicolon/and-separated numeric source lists. |
| `_RANGE` | constant | None | Recognizes a short ascending numeric citation range. |
| `_DAGGER` | constant | None | Recognizes dagger-style citation content before line annotations. |
| `_LABELLED` | constant | None | Recognizes labelled citation forms such as Source 2 or F ^1. |
| `_CARET` | constant | None | Recognizes caret-prefixed numeric citation forms. |
| `_NUM_LINES` | constant | None | Recognizes a numeric citation with optional line-number annotations. |
| `_ALL_NUMS` | constant | None | Extracts small numeric references from recognized citation groups. |
| `CitationFilter` | class | None | Incrementally canonicalizes valid citations while preserving code and ordinary brackets. |
| `CitationFilter.feed` | function | None | Adds a streamed text piece and releases text whose citation state is decidable. |
| `CitationFilter.flush` | function | None | Finishes processing the citation buffer when generation ends. |
| `sanitize_citations` | function | None | Runs the citation filter over complete text and returns valid cited numbers. |
| `_SPACE_BEFORE_PUNCT` | constant | None | Recognizes spaces left before punctuation after removing invalid references. |
| `_STRIP` | constant | None | Recognizes old numbered markers to remove from model-history text. |
| `strip_citations` | function | None | Removes old numeric references before messages become a later turn’s history. |
| Private/local helpers | helper group | Included within their lexical scope | _parse, CitationFilter.__init__, CitationFilter._emit_citation, CitationFilter._process, _tidy. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/document_store.py`

Registers scoped file hashes, reserves chat upload slots and handles ingestion/delete races.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `user_scope` | function | None | Constructs the private scope/owner/session equality filter. |
| `expiry_for` | function | None | Derives file expiry from session activity plus configured retention days. |
| `register_document` | async function | None | Checks scoped hashes, resets failed retries and atomically reserves private attachment slots. |
| `ingest_scoped` | async function | None | Supplies scope metadata/caps and handles deletion or expiry-extension races around ingestion. |

### `app/services/embeddings.py`

Calls Gemini document/query embeddings in batches with normalization and bounded retries.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `BATCH_SIZE` | constant | None | Limits one embedding request to at most 50 text inputs. |
| `MAX_RETRIES` | constant | None | Allows at most five embedding attempts per batch, including the initial attempt. |
| `embed_documents` | async function | None | Embeds document text in sequential batches using RETRIEVAL_DOCUMENT. |
| `embed_query` | async function | None | Embeds one query with RETRIEVAL_QUERY and returns its vector. |
| Private/local helpers | helper group | Included within their lexical scope | _client, _normalize, _embed_batch. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/groq_runtime.py`

Shares Groq concurrency slots and retries short rate limits without duplicating emitted text.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `AssistantBusy` | class | None | Stores a rounded positive retry delay in a safe user-facing exception. |
| `retry_seconds` | function | None | Parses provider numeric/date Retry-After values with a 60-second fallback. |
| `groq_slot` | async function | @asynccontextmanager | Holds a shared event-loop semaphore slot for one provider operation. |
| `call_groq` | async function | None | Runs a nonstream provider call with one permitted short-rate retry. |
| `stream_groq` | async function | None | Streams under a shared slot and prevents retry after content has been emitted. |
| Private/local helpers | helper group | Included within their lexical scope | AssistantBusy.__init__. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/ingestion.py`

Loads, chunks, embeds and stores extracted evidence, then records ready or failed status.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `ingest_document` | async function | None | Loads, caps, embeds and stores a file or attempts failed-status/partial-chunk cleanup. |
| Private/local helpers | helper group | Included within their lexical scope | _now. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/llm.py`

Stores the grounded-answer prompt, fixed replies and cached Groq answer client.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `BANK_SYSTEM_PROMPT` | constant | None | Defines grounded-answer, exact-copy, citation and untrusted-data instructions. |
| `OUT_OF_SCOPE_MESSAGE` | constant | None | Defines the fixed bank-scope refusal without an answer-model call. |
| `NOTHING_FOUND_MESSAGE` | constant | None | Defines the fixed reply when no qualifying document/account evidence remains. |
| `GREETING_MESSAGE` | constant | None | Defines the fixed greeting used after a greeting route. |
| `THANKS_MESSAGE` | constant | None | Defines the fixed acknowledgement used after a thanks route. |
| `DATA_UNAVAILABLE_MESSAGE` | constant | None | Defines the fixed failure reply and partial-data note for failed tools. |
| `ACCOUNT_AMOUNT_MESSAGE` | constant | None | Defines the safe account-card note replacing invalid generated money text. |
| `FIXED_REPLIES` | constant | None | Maps planner conversational actions to their literal reply constants. |
| `client` | function | @lru_cache | Caches an AsyncGroq client with SDK automatic retries disabled. |
| `prepare_answer_context` | function | None | Calls plain prompt preparation using current settings and the bank system prompt. |
| `stream_answer` | async function | None | Requests the grounded answer stream only when supplied evidence remains usable. |

### `app/services/loaders.py`

Parses eight supported file extensions into located text sections and extraction warnings.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `SUPPORTED_EXTENSIONS` | constant | None | Lists the eight accepted PDF, Office, plain-text and delimited file suffixes. |
| `MAX_READ_ROWS` | constant | None | Caps input table reading at 200000 rows before extraction continues with a warning. |
| `MAX_COLUMNS` | constant | None | Limits extracted table width to 200 columns. |
| `MAX_PDF_PAGES` | constant | None | Caps pages in the primary pdfplumber PDF path at 400. |
| `SLIDE_ATOMIC_CHARS` | constant | None | Keeps slides of at most 3000 characters together before chunk processing. |
| `MAX_CELL_CHARS` | constant | None | Truncates individual extracted cell display text after 1000 characters. |
| `MAX_STAT_COLUMNS` | constant | None | Limits computed table-column statistics to the first 40 columns. |
| `MAX_WARNINGS` | constant | None | Limits unique loader warnings to 12 per file. |
| `UnsupportedFileType` | class | None | Signals an unsupported file extension to the ingestion layer. |
| `Section` | class | @dataclass | Holds extracted text with a location, heading, kind, optional page and atomic preference. |
| `LoadResult` | class | @dataclass | Holds extracted sections and bounded warnings using independent default lists. |
| `LoadResult.warn` | function | None | Adds a unique warning only while the warning count remains below its cap. |
| `clean_text` | function | None | Removes nulls and normalizes excess spaces/newlines in extracted prose. |
| `_NUM` | constant | None | Recognizes numeric-looking table cell text for summary statistics. |
| `_ISO_DATE` | constant | None | Recognizes ISO-style date prefixes when classifying table columns. |
| `_ID_NAME` | constant | None | Recognizes identifier-like column names to avoid treating IDs as summable quantities. |
| `_MD_HEADING` | constant | None | Recognizes simple Markdown heading lines and their heading level. |
| `_MD_SEP` | constant | None | Recognizes a Markdown pipe-table separator row. |
| `_LOADERS` | constant | None | Maps each supported extension to its format-specific extraction function. |
| `load_file` | function | None | Dispatches by supported suffix and reports corrupt/textless file failures. |
| Section fields | fields/configuration | Not decorators | text, location, page, kind, heading, atomic. These declarations define the validated or stored shape; see the class above for its role. |
| LoadResult fields | fields/configuration | Not decorators | sections, warnings. These declarations define the validated or stored shape; see the class above for its role. |
| Private/local helpers | helper group | Included within their lexical scope | _need, _decode, _cell_text, _breadcrumb, _push_heading, _unique_headers, _prepare_grid, _row_line, _table_sections, _fmt_num, _as_numbers, _column_stats, _summary_section, _split_preamble, _grid_sections, _small_table_sections, _load_text, _md_cells, _load_markdown, _load_markdown.flush, _load_delimited, _load_xlsx, _load_docx, _load_docx.flush, _load_pdf_plain, _load_pdf, _load_pptx, _load_pptx.walk, _load_pptx.alt_text, _load_pptx.chart_lines. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/maintenance.py`

Purges inactive owned data and marks documents stuck in processing as failed.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `log` | module value | None | Holds the module logger used for exception-type-only operational diagnostics. |
| `sweep_stuck_documents` | async function | None | Marks processing records older than 15 minutes failed. |
| `purge_expired` | async function | None | Cascades inactive-session deletion, expires private documents and writes cleanup counts. |
| `retention_loop` | async function | None | Runs hourly purge attempts while allowing later iterations after a failed cleanup. |

### `app/services/memory.py`

Stores owner-filtered chat sessions/messages, refreshes file expiry and deletes chat-owned data.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `public_session` | function | None | Converts internal session fields into the HTTP session response shape. |
| `require_session` | async function | None | Requires both the requested session identifier and verified owner to match. |
| `create_session` | async function | None | Inserts a new UUID chat record owned by the caller. |
| `touch_session` | async function | None | Monotonically updates activity and extends private file/chunk expiry. |
| `save_message` | async function | None | Stores an owned message, refreshes activity and initializes the first-user-message title. |
| `get_recent` | async function | None | Returns an owner-filtered chronological suffix for model-history preparation. |
| `get_full_history` | async function | None | Returns all stored owned messages ordered by creation time. |
| `list_sessions` | async function | None | Returns up to 30 owned chats sorted by most recent activity by default. |
| `clear_session` | async function | None | Removes the owned session before deleting its messages, attachments and chunks. |
| Private/local helpers | helper group | Included within their lexical scope | _iso. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/output_guard.py`

Checks recognized currency-like answer strings against account display amounts and retrieved text.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `_NUMBER` | constant | None | Defines recognized numeric forms used by the currency-copy patterns. |
| `_CODE` | constant | None | Defines recognized currency-code text used by the currency-copy pattern. |
| `_AMOUNT` | constant | None | Recognizes currency-like strings whose exact occurrence must be authorized by evidence. |
| `_DECIMAL` | constant | None | Recognizes decimal substrings allowed from supplied amount/evidence text. |
| `valid_account_amounts` | function | None | Checks recognized generated amount strings against supplied account displays and passage text. |

### `app/services/prompting.py`

Builds bounded history, delimited evidence and fitted answer prompts using character estimates.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `PromptBudgetExceeded` | class | None | Signals that required input cannot fit the configured application prompt budget. |
| `PromptLimits` | class | @dataclass(frozen=True) | Stores the history, text, passage and input-ceiling limits used by plain prompt helpers. |
| `PromptLimits.from_settings` | function | @classmethod | Copies the dataclass’s declared limit fields from a settings object. |
| `estimate_tokens` | function | None | Returns text length divided by four as a deliberately approximate token estimate. |
| `prompt_tokens` | function | None | Estimates compact serialized message and optional tool-schema input size. |
| `build_history` | function | None | Selects recent user/assistant messages under count/content limits after citation removal. |
| `format_sources` | function | None | Serializes labelled source text into escaped numbered passage delimiters. |
| `render_messages` | function | None | Combines system rules, fitted history, original question, sources and optional account data. |
| `PreparedPrompt` | class | @dataclass | Holds fitted history, fitted sources and the complete final message list. |
| `prepare_prompt` | function | None | Ranks/caps passages and drops history then low-ranked complete sources until input fits. |
| PromptLimits fields | fields/configuration | Not decorators | history_max_messages, history_token_budget, old_answer_trim_chars, max_message_chars, max_passage_chars, prompt_token_ceiling, top_k. These declarations define the validated or stored shape; see the class above for its role. |
| PreparedPrompt fields | fields/configuration | Not decorators | history, sources, messages. These declarations define the validated or stored shape; see the class above for its role. |
| Private/local helpers | helper group | Included within their lexical scope | prepare_prompt.render. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/rate_limits.py`

Reserves per-user sliding-window chat slots using a process-local lock.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `check_rate_limit` | function | None | Atomically reserves a user’s rolling-window chat slot or returns wait seconds. |
| `rate_message` | function | None | Builds the safe per-user traffic-limit message with a retry delay. |
| Private/local helpers | helper group | Included within their lexical scope | _lock. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/retrieval.py`

Combines a visibility filter, ready-document IDs and an Atlas vector search pipeline.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `visibility_filter` | function | None | Allows shared evidence or files matching both verified owner and current session. |
| `search` | async function | None | Gates by visible ready document IDs, embeds a query and performs scoped Atlas vector search. |
| `ready_document_names` | async function | None | Lists bounded ready visible filenames for a planner file-presence signal. |

### `app/services/router.py`

Requests native tool calls, validates them, caps selected calls and falls back to document search.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `PLANNER_PROMPT` | constant | None | Instructs tool selection, follow-up interpretation and the untrusted-data boundary. |
| `ReplyArguments` | class | None | Restricts planner-only fixed reply selection to greeting, thanks or out_of_scope. |
| `ToolCall` | class | None | Stores a validated tool name and its argument dictionary inside a plan. |
| `Plan` | class | None | Stores the selected action/question and at most two explicit data calls. |
| `Plan.tool_calls` | function | None | Returns explicit calls or derives a compatible single-call action. |
| `plan_turn` | async function | None | Requests/validates native calls, retries once and falls back to document search. |
| ReplyArguments fields | fields/configuration | Not decorators | kind. These declarations define the validated or stored shape; see the class above for its role. |
| ToolCall fields | fields/configuration | Not decorators | model_config, name, arguments. These declarations define the validated or stored shape; see the class above for its role. |
| Plan fields | fields/configuration | Not decorators | model_config, question, action, tools. These declarations define the validated or stored shape; see the class above for its role. |
| Private/local helpers | helper group | Included within their lexical scope | plan_turn.messages. They support this file’s public operations; underscored names are not public entry points. |

### `app/services/textfix.py`

Normalizes problematic hyphens, spaces and zero-width characters before Markdown display.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `_MAP` | constant | None | Maps selected Unicode code points to plain hyphens/spaces or removal. |
| `normalize` | function | None | Maps selected Unicode hyphens/spaces and removes zero-width characters before display. |

### `app/tools/__init__.py`

Marks this directory as a Python package; contains no request handler or data pipeline.

No functions, classes or constants are defined here.

### `app/tools/account_tools.py`

Registers three plain account handlers and the optional transaction-type/limit argument model.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `balances` | async function | None | Calls the plain balance service with server context and adds a balances display block. |
| `TransactionArguments` | class | None | Validates optional account category and an integer transaction limit from 1 through 20. |
| `recent_transactions` | async function | None | Calls the owned transaction service and adds its display block. |
| `card_status` | async function | None | Calls the owned card service and adds its display block. |
| TransactionArguments fields | fields/configuration | Not decorators | account_type, limit. These declarations define the validated or stored shape; see the class above for its role. |

### `app/tools/document_tools.py`

Registers scoped document search and rejects blank model-supplied queries.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `SearchArguments` | class | None | Validates a nonblank bounded document-search query with no identity fields. |
| `SearchArguments.nonblank` | function | @field_validator('query'), @classmethod | Rejects a query containing only whitespace. |
| `documents` | async function | None | Runs server-scoped document search and removes hits below the minimum score. |
| SearchArguments fields | fields/configuration | Not decorators | query. These declarations define the validated or stored shape; see the class above for its role. |

### `app/tools/registry.py`

Defines server identity context, strict arguments, tool specifications, schemas and audited execution.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `ToolContext` | class | None | Holds immutable strict server-injected user, customer and session identity. |
| `Arguments` | class | None | Defines a strict identity-free base argument object that forbids additional fields. |
| `ToolSpec` | class | @dataclass(frozen=True) | Stores a fixed tool name, description, argument class, handler and read-only declaration. |
| `ToolSpec.schema` | function | None | Returns the native function-tool schema from the argument model. |
| `registry` | module value | None | Maps permitted data-tool names to their ToolSpec objects. |
| `schemas` | function | None | Returns the registry’s model-visible argument schemas. |
| `execute` | async function | None | Revalidates one call, invokes its handler and always attempts a tool_call audit. |
| ToolContext fields | fields/configuration | Not decorators | model_config, user_id, customer_id, session_id. These declarations define the validated or stored shape; see the class above for its role. |
| Arguments fields | fields/configuration | Not decorators | model_config. These declarations define the validated or stored shape; see the class above for its role. |
| ToolSpec fields | fields/configuration | Not decorators | name, description, arguments, handler, read_only. These declarations define the validated or stored shape; see the class above for its role. |

### `scripts/run_eval.py`

Exercises the running API over HTTP and reports checks, latencies and cleanup failures.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `ROOT` | constant | None | Resolves the repository root used for evaluator dataset/result defaults. |
| `User` | module value | None | Restricts evaluation users to Alice or Bob; frontend User instead declares the current-user shape. |
| `Category` | module value | None | Restricts evaluation cases to five declared category strings. |
| `DEMO_LOGINS` | constant | None | Stores public fictional evaluator credentials, independently of application imports. |
| `UNAVAILABLE` | constant | None | Defines the exact account-unavailable text checked by the evaluator. |
| `Model` | class | None | Defines strict evaluator data models that reject unknown fields. |
| `Checks` | class | None | Declares phrase, citation, card, fixed-reply and HTTP-status expectations. |
| `Attachment` | class | None | Declares evaluator upload text/filename; frontend Attachment declares listed file metadata. |
| `EvalCase` | class | None | Declares a unique-id question, user, category, checks and optional upload/foreign probe. |
| `EvaluationError` | class | None | Carries deliberately safe runner diagnostics suitable for reports. |
| `load_cases` | function | None | Parses JSONL, validates case shapes and rejects duplicate IDs or an empty dataset. |
| `normalize` | function | None | Folds case/whitespace and removes simple Markdown markers for phrase comparisons. |
| `answer_text` | function | None | Extracts the safe answer/detail/error string from an HTTP result payload. |
| `check_response` | function | None | Validates expected/forbidden text, status, cards and matching citation metadata. |
| `summary` | function | None | Calculates pass counts and rates grouped by evaluation category. |
| `HTTPOnlyRunner` | class | None | Coordinates HTTP login, pacing, requests, attachment readiness, cases and cleanup. |
| `HTTPOnlyRunner.redact` | function | None | Removes known credentials and recognizable secret formats from recorded strings. |
| `HTTPOnlyRunner.login` | function | None | Logs in through HTTP, caches a safe login failure and remembers issued tokens for redaction. |
| `HTTPOnlyRunner.request` | function | None | Makes authenticated paced HTTP requests with bounded 401 refresh and 429 retry. |
| `HTTPOnlyRunner.payload` | function | @staticmethod | Requires an HTTP response to contain a JSON object for runner processing. |
| `HTTPOnlyRunner.create_session` | function | None | Creates a disposable owned session through HTTP and validates its identifier. |
| `HTTPOnlyRunner.attach` | function | None | Uploads one text fixture over HTTP, polls readiness and waits for indexing. |
| `HTTPOnlyRunner.run_case` | function | None | Runs one question/probe and cleanup, checks results and redacts report values. |
| `write_report` | function | None | Creates a timestamped JSON report containing category rates and per-case timings. |
| `print_summary` | function | None | Prints category pass rates and failed question/answer/check details. |
| `main` | function | None | Validates CLI inputs, runs HTTP cases, writes/prints results and returns an exit code. |
| Model fields | fields/configuration | Not decorators | model_config. These declarations define the validated or stored shape; see the class above for its role. |
| Checks fields | fields/configuration | Not decorators | expected_phrases, any_phrases, forbidden_phrases, citation, account_card, card_types, fixed_reply, status_code. These declarations define the validated or stored shape; see the class above for its role. |
| Attachment fields | fields/configuration | Not decorators | filename, text. These declarations define the validated or stored shape; see the class above for its role. |
| EvalCase fields | fields/configuration | Not decorators | id, category, user, message, checks, attachment, probe. These declarations define the validated or stored shape; see the class above for its role. |
| Private/local helpers | helper group | Included within their lexical scope | HTTPOnlyRunner.__init__, HTTPOnlyRunner.run_case.redact_values. They support this file’s public operations; underscored names are not public entry points. |

### `scripts/seed_bank.py`

Builds and upserts deterministic fictional customers, accounts, transactions and cards.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `CUSTOMER_IDS` | constant | None | Lists the fixed three bank customers matching the user seed contract. |
| `build_seed` | function | None | Constructs three customers, six coherent accounts, 216 transactions and six debit cards. |
| `main` | async function | None | Creates bank indexes, upserts deterministic seed rows and closes the bank client. |

### `scripts/seed_kb.py`

Replaces same-named shared demo documents and runs their embedding ingestion.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `SEED_DIR` | constant | None | Resolves the checked-in fictional shared document folder. |
| `seed_kb` | async function | None | Replaces same-named shared files and requires successful ingestion before continuing. |
| `main` | async function | None | Connects app storage, ensures indexes, seeds shared documents and closes the client. |

### `scripts/seed_users.py`

Inserts missing fictional users with fixed customer links, preserving existing accounts.

| Symbol | Kind | Decorators on declaration | One-line purpose |
| --- | --- | --- | --- |
| `DEMO_USERS` | constant | None | Defines public fake users, passwords and stable matching bank customer links. |
| `seed_users` | async function | None | Inserts missing fictional users while preserving existing passwords and account status. |
| `main` | async function | None | Connects app storage, ensures indexes, seeds missing users and closes the client. |

### `frontend/src/AccountCards.tsx`

Displays server-formatted balances, transaction rows and masked card status.

| Symbol | Kind | One-line purpose |
| --- | --- | --- |
| `AccountCards` | function | Renders balances, newest transactions and masked card statuses from server display blocks. |

### `frontend/src/Answer.test.tsx`

Checks safe Markdown, citation clicks, missing excerpts and exact account display strings.

| Symbol or declaration group | Kind | Purpose |
| --- | --- | --- |
| Vitest test callbacks | tests | Inline it/describe callbacks assert the behaviors described for this file; they are not exported application APIs. |
| Local fixture values and helpers | helper group | click, source. These names construct controlled test inputs and responses, not production entry points. |

### `frontend/src/Answer.tsx`

Renders safe Markdown, replaces numbered references and opens a native source dialog.

| Symbol | Kind | One-line purpose |
| --- | --- | --- |
| `remarkCitations` | function | Transforms numbered text nodes while leaving code, links, images and HTML nodes unchanged. |
| `Answer` | function | Renders Markdown/GFM while suppressing raw HTML/images and replacing valid numbered sources. |
| `SourceDialog` | function | Synchronizes a native dialog and displays a supplied source text/snippet or its absence. |
| Nested/private helper group | local helpers | remarkCitations.walk. These closures coordinate the surrounding operation and are not module exports. |

### `frontend/src/api.test.ts`

Checks SSE framing, authentication, 401 clearing, 429 timing and POST body shape.

| Symbol or declaration group | Kind | Purpose |
| --- | --- | --- |
| Vitest test callbacks | tests | Inline it/describe callbacks assert the behaviors described for this file; they are not exported application APIs. |
| Local fixture values and helpers | helper group | encoder, fetch, received, stream, text. These names construct controlled test inputs and responses, not production entry points. |

### `frontend/src/api.ts`

Stores auth state, makes HTTP requests and decodes fragmented POST SSE responses.

| Symbol | Kind | One-line purpose |
| --- | --- | --- |
| `STORAGE_KEY` | constant | Names the sessionStorage entry used to mirror the in-memory access token. |
| `storedToken` | function | Reads tab-scoped token storage without failing when storage is unavailable. |
| `token` | constant | Stores mutable auth state in memory; this is a module value rather than a financial token. |
| `listeners` | constant | Stores the set of auth-snapshot subscribers. |
| `getToken` | function | Returns the in-memory authentication snapshot. |
| `setToken` | function | Updates memory, mirrors sessionStorage and notifies auth listeners. |
| `subscribeAuth` | function | Registers an auth-store listener and returns its unsubscribe function. |
| `ApiError` | class | Carries a safe message, HTTP status and optional absolute retry timestamp. |
| ApiError constructor | constructor | Calls Error with the safe message and stores public status/retryAt values; it has no provider side effects. |
| `isAbort` | function | Recognizes AbortError DOM exceptions used for cancelled work. |
| `friendly` | function | Returns an approved API error message or the generic safe fallback. |
| `retrySeconds` | function | Parses a numeric or date Retry-After header into nonnegative seconds. |
| `request` | function | Adds current bearer headers and maps HTTP/network failures to safe API errors. |
| `json` | function | Parses a successful response into its declared TypeScript result shape. |
| `sessionPath` | function | URL-encodes a session identifier into the fixed session route prefix. |
| `login` | function | Submits URL-encoded credentials and stores a usable returned access token. |
| `me` | function | Fetches the current-user summary with an abort signal. |
| `sessions` | function | Lists owned sessions with an abort signal. |
| `createSession` | function | Creates one empty owned chat through POST. |
| `history` | function | Loads an owned session’s stored messages. |
| `attachments` | function | Lists owned current-chat attachment statuses. |
| `deleteSession` | function | Deletes an owned chat through the session API. |
| `upload` | function | Posts repeated multipart files fields to the owned chat attachment endpoint. |
| `readEvents` | function | Decodes UTF-8 and SSE framing, dispatching known events until done/error. |
| `sendChat` | function | POSTs the actual chat JSON contract and requires a streaming response. |
| Nested/private helper group | local helpers | readEvents.dispatch, readEvents.line, readEvents.drain. These closures coordinate the surrounding operation and are not module exports. |

### `frontend/src/App.test.tsx`

Checks login, upload/poll/send, failures, session actions, 401 and failed-file exclusion.

| Symbol or declaration group | Kind | Purpose |
| --- | --- | --- |
| Vitest test callbacks | tests | Inline it/describe callbacks assert the behaviors described for this file; they are not exported application APIs. |
| Local fixture values and helpers | helper group | alert, dialog, fetch, json, mockApi, original, request, session, sse, user. These names construct controlled test inputs and responses, not production entry points. |

### `frontend/src/App.tsx`

Owns login, chat state, sidebar navigation, uploads, polling, streaming and deletion confirmation.

| Symbol | Kind | One-line purpose |
| --- | --- | --- |
| `key` | function | Creates random local message/file keys with crypto.randomUUID. |
| `useCooldown` | function | Maintains a one-second countdown to an absolute retry deadline. |
| `Notice` | function | Displays a friendly alert with an optional dismissal button. |
| `Login` | function | Collects credentials, submits form login and displays authentication failure/cooldown. |
| `mergeFiles` | function | Combines listed server attachments with pending local chips and safe failure text. |
| `blankAssistant` | function | Creates an empty pending local assistant message with evidence/display containers. |
| `Workspace` | function | Coordinates identity, owned chats, uploads, polling, stream updates and dialogs. |
| `App` | function | Subscribes to token state and selects Login or Workspace. |
| Nested/private helper group | local helpers | Login.submit, Workspace.remember, Workspace.problem, Workspace.activate, Workspace.openSession, Workspace.bootstrap, Workspace.tick, Workspace.ensureSession, Workspace.newChat, Workspace.removeChat, Workspace.chooseFiles, Workspace.uploadFiles, Workspace.send, Workspace.send.abort. These closures coordinate the surrounding operation and are not module exports. |

### `frontend/src/Icons.tsx`

Draws local SVG icons and the demo-bank wordmark without remote assets.

| Symbol | Kind | One-line purpose |
| --- | --- | --- |
| `IconName` | type | Restricts icon selection to the declared local icon-name union. |
| `paths` | constant | Stores the fixed SVG path definitions for supported UI icons. |
| `Icon` | function | Renders a named local SVG path with accessible decorative markup. |
| `Brand` | function | Renders the local demo-bank mark and optional full wordmark. |

### `frontend/src/limits.test.ts`

Checks file count, format, empty/oversized inputs and explicit failed-file retries.

| Symbol or declaration group | Kind | Purpose |
| --- | --- | --- |
| Vitest test callbacks | tests | Inline it/describe callbacks assert the behaviors described for this file; they are not exported application APIs. |
| Local fixture values and helpers | helper group | chips, file, large. These names construct controlled test inputs and responses, not production entry points. |

### `frontend/src/limits.ts`

Validates file size, extension, per-upload/per-chat counts and public text limits.

| Symbol | Kind | One-line purpose |
| --- | --- | --- |
| `positive` | function | Parses positive public build values and floors them, otherwise using defaults. |
| `MAX_UPLOAD_MB` | constant | Reads the public upload-size build value, defaulting to five. |
| `MAX_MESSAGE_CHARS` | constant | Reads the public message-length build value, defaulting to 2000. |
| `MAX_CHAT_FILES` | constant | Sets the client chat attachment count to five. |
| `MAX_UPLOAD_FILES` | constant | Sets the client per-selection/upload count to three. |
| `ACCEPT` | constant | Lists the eight supported file suffixes for selection and validation. |
| `validateFiles` | function | Checks file count, retry filename, occupied slots, size, emptiness and suffix. |

### `frontend/src/main.tsx`

Mounts App under React StrictMode and imports local styles.

No named public functions/classes/constants. This file executes the mounting or test setup described above.

### `frontend/src/test-setup.ts`

Adds DOM matchers, dialog shims and per-test cleanup for simulated browser tests.

No named public functions/classes/constants. This file executes the mounting or test setup described above.

### `frontend/src/types.ts`

Declares TypeScript API, event, file-chip, message and account-card shapes.

| Symbol | Kind | One-line purpose |
| --- | --- | --- |
| `User` | interface | Current authenticated user summary. |
| `ChatSession` | interface | Owned chat identifier, title and timestamps. |
| `Source` | interface | Numbered source identity, location and optional live excerpt. |
| `Balance` | interface | Server-formatted balance row. |
| `Transaction` | interface | Server-formatted owned transaction row. |
| `BankCard` | interface | Masked card kind, status and expiry. |
| `AccountCard` | type | Discriminated balances/transactions/cards display union. |
| `FileRef` | interface | Filename and uploaded document reference. |
| `HistoryMessage` | interface | Stored role/text/source/attachment history contract. |
| `FileStatus` | type | Upload and ingestion state union. |
| `FileChip` | interface | Local upload status, filename, ID and safe error state. |
| `Attachment` | interface | Server attachment listing shape. |
| `UploadResult` | interface | Per-file upload status/duplicate/error response. |
| `Message` | interface | Local conversation content, evidence, cards and completion state. |
| `ChatEvent` | type | Known status/meta/token/done/error event union. |

### `frontend/src/styles.css`

Defines system-font desktop/mobile layout, local colors, tables, dialogs and reduced-motion styles.

CSS selectors are presentation rules rather than Python/TypeScript functions or exported constants. Media rules cover desktop/phone widths and reduced motion.

## 5. Decisions

| Decision | Because | Alternative not used | Trade-off |
| --- | --- | --- | --- |
| Use syntax-tree inventories | Declarations can be listed without running startup or reading secrets. | Import every application module and inspect live objects. | Dynamic assignments and external library-generated methods still need explanation. |
| Include class fields | A fresher needs to locate model/settings shapes as well as functions. | List only top-level function names. | The reference is longer but avoids hidden configuration gaps. |
| Group private and nested helpers | They support public behavior but are not separate stable APIs. | Treat every closure as a callable module export. | Readers must inspect the enclosing body for its runtime values. |
| Separate imported libraries | Dependency definitions belong to their packages, not to this repository. | Claim imported classes are defined locally. | Use the dependency/glossary lookup alongside this reference. |

## 6. What can go wrong

This reference is a snapshot of the code read for the documentation task. A later code change can add or rename declarations. Re-run a source inventory before relying on completeness after modifications.

Python can define values dynamically and Pydantic/dataclasses generate methods not written in the file. Those inherited/generated methods are library behavior, not missing local source declarations. Anonymous React effects and Vitest callbacks have no stable declared public name; their behavior is explained in documents 10 and 11.

A class field that allows a value does not prove the seed contains that value. A read_only field does not enforce a database permission. A function docstring is useful evidence but can overstate behavior; ingest_document attempts failure cleanup, yet an exception in that cleanup can still escape. The purpose descriptions use the implementation rather than treating the docstring as an executable guarantee.

## 7. How to see it working

Search by a table symbol in its listed file, then inspect the corresponding tests from document 11. For example, follow get_current_user to its JWT/user checks, execute to its validation/audit finally block, and readEvents to its framing buffer. These are useful small functions to explain aloud without navigating an entire application at once.

The documentation validation checks source paths and named references against the safe source inventory. It does not import app/main.py, evaluate settings from the local environment or run a live integration. No line numbers are used, so an insertion elsewhere in a file will not invalidate the reference location.

## 8. Check yourself

1. **Why are imported BaseModel methods not listed as local functions?** They are supplied by Pydantic rather than declared in these source files.

2. **Does a dotted helper name imply an importable attribute?** No. It can mark a nested closure’s lexical scope.

3. **Why list module values separately from constants?** Some values, including token/listener state, are mutable runtime state rather than fixed configuration.

4. **Why inventory without imports?** Imports can load settings and startup resources; parsing source avoids those side effects.

5. **How should this reference change after new code is added?** Refresh the syntax inventory, purposes and reference checks before claiming it remains complete.
