"""Request models for the API."""
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        description="Leave empty on the first message; paste the returned session_id to continue the conversation.",
    )
    doc_ids: list[str] | None = Field(
        default=None, description="Restrict the search to these documents. Empty list = search all documents."
    )
    web: Literal["auto", "on", "off"] = Field(
        default="auto",
        description="Web access. auto = the assistant decides (and always reads URLs you mention); on = always look it up on the web; off = never.",
    )
    stream: bool = Field(
        default=True,
        description="Stream tokens via SSE (default), or return one JSON reply. Use false in Swagger.",
    )

    # Pre-fills the example body in Swagger's "Try it out". Empty values are used (instead of null)
    # so every field is visible; "" and [] behave exactly like "not provided".
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message": "What is this document about?",
                    "session_id": "",
                    "doc_ids": [],
                    "web": "auto",
                    "stream": False,
                }
            ]
        }
    }