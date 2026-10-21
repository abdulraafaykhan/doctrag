"""Turn loader Sections into chunks.

- Prose is split into overlapping ~chunk_size pieces.
- Sections marked `atomic` (table row-groups, table summaries, short slides) are kept whole,
  because splitting them would separate rows from their column names.
- `embed_text` = heading breadcrumb + text. It is what gets embedded; `text` is what is
  stored, shown to the LLM and displayed to the user.
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings
from app.services.loaders import Section

ATOMIC_HARD_MAX = 7000  # embedding input limit safety: even "atomic" sections are split above this


def chunk_sections(sections: list[Section]) -> list[dict]:
    """Return [{'text', 'embed_text', 'page', 'location', 'kind', 'heading', 'chunk_index'}, ...]."""
    s = get_settings()
    prose = RecursiveCharacterTextSplitter(
        chunk_size=s.chunk_size,
        chunk_overlap=s.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],  # paragraph -> line -> sentence -> word -> char
    )
    oversize = RecursiveCharacterTextSplitter(
        chunk_size=3000, chunk_overlap=0, separators=["\n", " ", ""]
    )

    chunks: list[dict] = []
    for sec in sections:
        if sec.atomic:
            pieces = [sec.text] if len(sec.text) <= ATOMIC_HARD_MAX else oversize.split_text(sec.text)
        else:
            pieces = prose.split_text(sec.text)
        pieces = [p.strip() for p in pieces if p.strip()]

        for k, piece in enumerate(pieces, start=1):
            location = sec.location
            # PDFs keep a plain "p.N"; other sections that were split get "(part k/n)" so
            # citations can tell the pieces apart.
            if len(pieces) > 1 and sec.page is None:
                location = f"{sec.location} (part {k}/{len(pieces)})"
            chunks.append(
                {
                    "text": piece,
                    "embed_text": f"{sec.heading}\n{piece}" if sec.heading else piece,
                    "page": sec.page,
                    "location": location,
                    "kind": sec.kind,
                    "heading": sec.heading,
                    "chunk_index": len(chunks),
                }
            )
    return chunks