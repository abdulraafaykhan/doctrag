"""Move legacy collection contents without model calls; apply only with --apply."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, get_settings
from app.db import get_db, close_db, normal_indexes
from app.ingest.models import digest, utcnow, DocumentMeta
from app.log import mask_text
from app.vision import parse_vision_json, render_vision


def migrate(database, *, apply=False):
    names = set(database.list_collection_names())
    counts = {name: database[name].count_documents({}) for name in ('image_cache', 'ingest_jobs', 'embed_cache') if name in names}
    print({'database': database.name, 'apply': apply, 'legacy_rows': counts}, flush=True)
    if not apply:
        return counts
    if 'image_cache' in names:
        if 'extractions' not in names:
            database['image_cache'].rename('extractions', dropTarget=False)
        else:
            for row in database['image_cache'].find({}):
                database['extractions'].update_one({'_id': row['_id']}, {'$setOnInsert': row}, upsert=True)
            database['image_cache'].drop()
    # The former hash+owner uniqueness prevented provenance for repeated crops in different docs.
    for name, index in database['extractions'].index_information().items():
        if index.get('unique') and list(index['key']) == [('image_hash', 1), ('owner_key', 1)]:
            database['extractions'].drop_index(name)
    for row in database['extractions'].find({}):
        empty = {'extraction_id': 'ext_' + digest(str(row['_id']))[:24], 'doc_id': None, 'namespace': 'bank' if row.get('owner_key') == 'bank' else 'user', 'filename': '', 'page': None, 'bbox': None, 'kind': None, 'ocr': {}, 'vlm': {}, 'compare': {}, 'selected_source': None, 'selected_reason': '', 'flags': [], 'needs_review': False}
        database['extractions'].update_one({'_id': row['_id']}, {'$set': {key: value for key, value in empty.items() if key not in row}})
    for job in database['ingest_jobs'].find({}) if 'ingest_jobs' in names else []:
        doc_id = job.get('doc_id') or 'doc_retry_' + digest(job['job_id'])[:18]
        fields = {key: job.get(key, default) for key, default in {'state': 'queued', 'stage': 'parse', 'cursor': 0, 'chunks_total': 0, 'chunks_done': 0, 'attempts': 0, 'last_error': None, 'updated_at': utcnow()}.items()}
        if not database['documents'].find_one({'doc_id': doc_id}):
            if not job.get('image'):
                print(f"Discarding orphan job for deleted document {doc_id}", flush=True)
                continue
            filename = Path(job.get('image', doc_id)).name
            source = ROOT / 'data/gate1_images' / filename
            database['documents'].insert_one({**DocumentMeta(title=filename).model_dump(), 'doc_id': doc_id, 'filename': filename, 'owner_key': 'bank', 'namespace': 'bank', 'storage_path': str(source), 'file_hash': digest(source.read_bytes()) if source.exists() else digest(doc_id), 'processing_state': 'ready' if fields['state'] == 'done' else fields['state'], 'created_at': utcnow(), 'counts': {}, 'review_flags': []})
        database['documents'].update_one({'doc_id': doc_id}, {'$set': {'job': fields}})
    if 'ingest_jobs' in names:
        database['ingest_jobs'].drop()
    cfg = get_settings()
    for chunk in database['chunks'].find({}):
        document = database['documents'].find_one({'doc_id': chunk.get('doc_id')}) or {}
        text = mask_text(chunk['text'])
        prefix, separator, body = text.partition('\n')
        try:
            structured = parse_vision_json(body if separator else text)
        except ValueError:
            pass
        else:
            text = (prefix + '\n' if separator else '') + render_vision(structured)
        changes = {'text': text, 'filename': document.get('filename', ''), 'doc_title': document.get('title', ''), 'page': chunk.get('page'), 'section_path': chunk.get('section_path', ''), 'kind': chunk.get('kind', 'prose'), 'source_method': chunk.get('source_method') if chunk.get('source_method') in {'ocr', 'vlm'} else 'text_layer', 'extraction_id': chunk.get('extraction_id'), 'needs_review': chunk.get('needs_review', False), 'review_flags': chunk.get('review_flags', []), 'created_at': chunk.get('created_at', utcnow()), 'expires_at': chunk.get('expires_at'), 'content_hash': digest(text), 'embedding_model': chunk.get('embedding_model', cfg.models.embed), 'embedding_dims': len(chunk.get('embedding') or []) or cfg.models.embed_dims}
        if not chunk.get('embedding') and 'embed_cache' in names:
            key = f"{cfg.models.embed}|{cfg.models.embed_dims}|document|{changes['content_hash']}"
            cached = database['embed_cache'].find_one({'key': key})
            if cached:
                changes['embedding'] = cached['embedding']
        database['chunks'].update_one({'_id': chunk['_id']}, {'$set': changes})
    if 'embed_cache' in names:
        database['embed_cache'].drop()
    for collection in ('documents', 'chunks', 'extractions'):
        for keys, options in normal_indexes()[collection]:
            database[collection].create_index(keys, **options)
    print('Storage migration complete; no search index changed.', flush=True)
    return counts


if __name__ == '__main__':
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--apply', action='store_true')
    try:
        migrate(get_db(), apply=args.parse_args().apply)
    finally:
        close_db()
