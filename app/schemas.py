"""Request models for the API."""


from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, description="Create a session with POST /sessions, then paste its session_id here.")
    attachment_ids: list[str] = Field(default_factory=list, max_length=5,
        description="Optional attachment ids from this session. Existing files remain searchable for follow-ups.")
    stream: bool = Field(
        default=True,
        description="Stream tokens via SSE (default), or return one JSON reply. Use false in Swagger.",
    )

    # Keep the required session visible in Swagger; obtain it from POST /sessions.
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "message": "What is this document about?",
                    "session_id": "paste-session-id-here",
                    "attachment_ids": [],
                    "stream": False,
                }
            ]
        }
    }

class ChatResponse(BaseModel):
    session_id: str | None
    answer: str
    cited: list[int]
    plan: dict | None
    notes: list[str]
    sources: list[dict]
    account_cards: list[dict] = Field(default_factory=list)


class ChatError(BaseModel):
    error: str


class SessionResponse(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str


class SessionDeleted(BaseModel):
    session_id: str
    messages_deleted: int


class MessageResponse(BaseModel):
    session_id: str
    user_id: str
    role: str
    content: str
    sources: list[dict]
    attachments: list[dict] = Field(default_factory=list)
    created_at: str


class HealthResponse(BaseModel):
    status: str
    vector_index: str


class DocumentResponse(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: str
    num_chunks: int
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: str


class UploadResponse(BaseModel):
    filename: str
    status: str
    doc_id: str | None = None
    duplicate: bool | None = None
    error: str | None = None


class AttachmentResponse(BaseModel):
    id: str
    filename: str
    status: str
    size_bytes: int
    num_chunks: int
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    expires_at: str
