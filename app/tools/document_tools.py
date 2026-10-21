from pydantic import Field, field_validator
from app.config import get_settings
from app.services.retrieval import search
from app.tools.registry import Arguments, ToolContext, ToolSpec, registry

class SearchArguments(Arguments):
    query: str = Field(min_length=1, max_length=4000)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Empty query")
        return value

async def documents(ctx: ToolContext, args: SearchArguments) -> dict:
    hits = await search(args.query, owner_id=ctx.user_id, session_id=ctx.session_id)
    return {"documents": [h for h in hits if h["score"] >= get_settings().min_doc_score]}

registry["search_documents"] = ToolSpec("search_documents", "Search bank documents and this chat's files; rewrite follow-ups into standalone queries.", SearchArguments, documents)
